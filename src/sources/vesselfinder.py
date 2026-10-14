"""VesselFinder public-web scraper (Phase 3 data source).

Reads the same unauthenticated endpoints that back vesselfinder.com's own
vessel pages:

  * ``/api/pub/click/<mmsi>``  - JSON popup payload: identity, particulars,
    SOG, COG, draught, destination, ETA and the UTC timestamp of the vessel's
    last position report.
  * ``/vessels/details/<imo>`` - server-rendered HTML: call sign, AIS type,
    flag, LOA / beam, current draught, and a *whole-degree* position.
  * ``/api/pub/vi2/<mmsi>``    - voyage / last-port-call JSON.

None of these paths are disallowed by https://www.vesselfinder.com/robots.txt
(which blocks ``/vessels/view/*``, ``/aismap*``, ``/worker1/*``, ``/worker2/*``
and the photo paths - all avoided here). Requests are rate-limited and cached
per Rule A02.

IMPORTANT - position precision
------------------------------
VesselFinder does not publish precise coordinates to unauthenticated clients.
The live map renders vessels server-side into PNG tiles, and the detail page
exposes ``ship_lat`` / ``ship_lon`` rounded to whole degrees (~111 km at the
equator). Every other field this source returns is exact; the position is not.

Dynamic observations emitted here are therefore tagged ``POS:APPROX`` in their
raw sentence, and approximate positions can be suppressed entirely with
``include_approximate_position=False``. For precise tracks keep
``AIS_DATA_SOURCE=aisstream`` and use this source for particulars enrichment.
"""
from __future__ import annotations

import re
import json
import html
import time
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict, Iterator, List, Optional, Tuple

import httpx

from src.sources.base import DataSource
from src.models.vessel import (
    NavigationStatus,
    VesselDynamic,
    VesselParticulars,
    VesselStatic,
)

logger = logging.getLogger(__name__)

BASE_URL = "https://www.vesselfinder.com"

# A desktop browser UA; the public endpoints reject the default httpx agent.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)

# Whole-degree rounding on the public detail page.
POSITION_UNCERTAINTY_DEG = 0.5


# ---------------------------------------------------------------------------
# HTML / JSON parsing helpers (pure functions - unit-testable without network)
# ---------------------------------------------------------------------------

# Live AIS panel rows, e.g. <td class="n3">Callsign</td><td class="v3">D5VK6</td>
_N3_ROW_RE = re.compile(
    r'<td class="n3">(?P<label>[^<]+)</td>\s*<td class="v3[^"]*">(?P<value>.*?)</td>',
    re.S,
)
# Particulars table rows, e.g. <td class="tpc1">Gross Tonnage</td><td class="tpc2">75590</td>
_TPC_ROW_RE = re.compile(
    r'<td class="tpc1">(?P<label>[^<]+)</td>\s*<td class="tpc2">(?P<value>.*?)</td>',
    re.S,
)
# Inline map-bootstrap object carrying the coarse position.
_SHIP_JSON_RE = re.compile(r"\{[^{}]*\"ship_lat\"[^{}]*\}")

_TAG_RE = re.compile(r"<[^>]+>")


def _clean(raw: str) -> str:
    """Strip tags/entities/whitespace from a table cell."""
    return html.unescape(_TAG_RE.sub("", raw)).replace("\xa0", " ").strip()


def _as_float(value: Any) -> Optional[float]:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> Optional[int]:
    num = _as_float(value)
    return int(num) if num is not None else None


def _positive_int(value: Any) -> Optional[int]:
    """VesselFinder uses 0 as 'unknown' for tonnage / IMO / year fields."""
    num = _as_int(value)
    return num if num and num > 0 else None


def _positive_float(value: Any) -> Optional[float]:
    num = _as_float(value)
    return num if num and num > 0 else None


def _format_eta(eta_ts: Any) -> Optional[str]:
    """Render an ETA unix timestamp as the ``MM-DD HH:MM`` form AIS/VF use."""
    ts = _positive_int(eta_ts)
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%m-%d %H:%M")


def _utc_from_ts(ts: Any) -> Optional[datetime]:
    seconds = _positive_int(ts)
    if seconds is None:
        return None
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def parse_details_html(page_html: str) -> Dict[str, Any]:
    """Extract the server-rendered fields from a ``/vessels/details/<imo>`` page.

    The ``Navigation Status`` and ``Position Received`` rows are populated
    client-side by VesselFinder and are empty in the raw HTML, so they are not
    read here.
    """
    rows = {_clean(m.group("label")): _clean(m.group("value")) for m in _N3_ROW_RE.finditer(page_html)}
    specs = {_clean(m.group("label")): _clean(m.group("value")) for m in _TPC_ROW_RE.finditer(page_html)}

    out: Dict[str, Any] = {}

    call_sign = rows.get("Callsign")
    if call_sign and call_sign != "-":
        out["call_sign"] = call_sign

    for key, source in (("ais_type", rows.get("AIS Type")), ("ship_type", specs.get("Ship Type"))):
        if source and source != "-":
            out[key] = source

    flag = rows.get("AIS Flag") or specs.get("Flag")
    if flag and flag != "-":
        out["flag"] = flag

    # "300 / 40 m"
    dims = rows.get("Length / Beam", "")
    dim_match = re.match(r"\s*([\d.]+)\s*/\s*([\d.]+)", dims)
    if dim_match:
        out["length_overall_m"] = _positive_float(dim_match.group(1))
        out["beam_m"] = _positive_float(dim_match.group(2))

    # "12.8 m"
    draught_match = re.match(r"\s*([\d.]+)", rows.get("Current draught", ""))
    if draught_match:
        out["draught_meters"] = _positive_float(draught_match.group(1))

    out["gross_tonnage"] = _positive_int(specs.get("Gross Tonnage"))
    out["year_built"] = _positive_int(specs.get("Year of Build"))

    # "9225641 / 636092932"
    ids_match = re.match(r"\s*(\d+)\s*/\s*(\d+)", rows.get("IMO / MMSI", ""))
    if ids_match:
        out["imo_number"] = _positive_int(ids_match.group(1))
        out["mmsi"] = _positive_int(ids_match.group(2))

    ship_json = _SHIP_JSON_RE.search(page_html)
    if ship_json:
        try:
            boot = json.loads(ship_json.group(0))
        except json.JSONDecodeError:
            boot = {}
        lat, lon = _as_float(boot.get("ship_lat")), _as_float(boot.get("ship_lon"))
        if lat is not None and lon is not None and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
            out["latitude"] = lat
            out["longitude"] = lon
            out["position_is_approximate"] = True
        out["course_deg"] = _as_float(boot.get("ship_cog"))
        out["speed_knots"] = _as_float(boot.get("ship_sog"))
        out["position_age_text"] = (boot.get("lrpd") or "").strip() or None

    return {k: v for k, v in out.items() if v is not None}


@dataclass
class VesselFinderSnapshot:
    """One merged read of a vessel across VesselFinder's public endpoints."""

    mmsi: int
    name: Optional[str] = None
    imo_number: Optional[int] = None
    call_sign: Optional[str] = None
    ship_type: Optional[str] = None
    destination: Optional[str] = None
    eta: Optional[str] = None
    draught_meters: Optional[float] = None
    speed_knots: Optional[float] = None
    course_deg: Optional[float] = None
    nav_status_code: int = int(NavigationStatus.UNDEFINED)
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    position_is_approximate: bool = True
    position_time_utc: Optional[datetime] = None
    position_age_text: Optional[str] = None
    particulars: VesselParticulars = field(default_factory=VesselParticulars)

    @property
    def nav_status_label(self) -> str:
        return NavigationStatus.label_for(self.nav_status_code)

    @property
    def has_position(self) -> bool:
        return self.latitude is not None and self.longitude is not None

    def to_dynamic(self) -> Optional[VesselDynamic]:
        """Build a position report, or ``None`` when no coordinates are known."""
        if not self.has_position:
            return None
        course = self.course_deg if self.course_deg is not None and 0.0 <= self.course_deg <= 360.0 else 0.0
        speed = self.speed_knots if self.speed_knots is not None and self.speed_knots >= 0.0 else 0.0
        return VesselDynamic(
            mmsi=self.mmsi,
            timestamp=self.position_time_utc or datetime.now(timezone.utc),
            latitude=round(self.latitude, 6),
            longitude=round(self.longitude, 6),
            speed_knots=round(speed, 1),
            course_deg=round(course, 1),
            heading_deg=None,  # not published on the public endpoints
            nav_status_code=self.nav_status_code,
            nav_status_label=self.nav_status_label,
        )

    def to_static(self) -> VesselStatic:
        """Build the voyage / identity report, carrying scraped master data."""
        return VesselStatic(
            mmsi=self.mmsi,
            ship_name=self.name,
            ship_type=self.ship_type,
            call_sign=self.call_sign,
            imo_number=self.imo_number,
            draught_meters=self.draught_meters,
            destination=self.destination,
            eta=self.eta,
            particulars=self.particulars,
        )

    def raw_sentence(self, endpoint: str) -> str:
        """Pseudo-NMEA provenance line archived alongside each observation."""
        parts = [f"[vesselfinder:{endpoint}]", f"MMSI:{self.mmsi}"]
        if self.imo_number:
            parts.append(f"IMO:{self.imo_number}")
        if self.speed_knots is not None:
            parts.append(f"SOG:{self.speed_knots}")
        if self.course_deg is not None:
            parts.append(f"COG:{self.course_deg}")
        if self.has_position:
            parts.append(f"LAT:{self.latitude} LON:{self.longitude}")
            if self.position_is_approximate:
                parts.append(f"POS:APPROX(+/-{POSITION_UNCERTAINTY_DEG}deg)")
        if self.position_time_utc:
            parts.append(f"TS:{self.position_time_utc.isoformat()}")
        return " ".join(parts)


def parse_click_payload(mmsi: int, payload: Dict[str, Any]) -> VesselFinderSnapshot:
    """Map an ``/api/pub/click/<mmsi>`` JSON body onto a snapshot.

    Field notes, derived from live responses:
      ``ss`` speed (kn, -1 unknown)   ``cu`` course (deg, -1 unknown)
      ``al`` LOA (m)                  ``aw`` beam (m)
      ``gt`` gross tonnage            ``dw`` deadweight
      ``draught`` current draught x10 ``drm`` max draught (string, m)
      ``y`` year built                ``ts`` last position report (unix)
      ``.ns`` navigation status code (-1 unknown)
      ``etaTS`` ETA (unix)            ``country`` flag state
    """
    name = (payload.get("name") or "").strip() or None
    # VesselFinder echoes the MMSI as the name when it knows nothing else.
    if name == str(mmsi):
        name = None

    ship_type = (payload.get("type") or "").strip() or None
    if ship_type and ship_type.lower() == "unknown type":
        ship_type = None

    nav_raw = _as_int(payload.get(".ns"))
    nav_code = nav_raw if nav_raw is not None and 0 <= nav_raw <= 15 else int(NavigationStatus.UNDEFINED)

    speed = _as_float(payload.get("ss"))
    course = _as_float(payload.get("cu"))
    draught = _as_float(payload.get("draught"))

    return VesselFinderSnapshot(
        mmsi=mmsi,
        name=name,
        imo_number=_positive_int(payload.get("imo")),
        ship_type=ship_type,
        destination=(payload.get("dest") or "").strip() or None,
        eta=_format_eta(payload.get("etaTS")),
        draught_meters=round(draught / 10.0, 1) if draught and draught > 0 else None,
        speed_knots=speed if speed is not None and speed >= 0.0 else None,
        course_deg=course if course is not None and course >= 0.0 else None,
        nav_status_code=nav_code,
        position_time_utc=_utc_from_ts(payload.get("ts")),
        particulars=VesselParticulars(
            gross_tonnage=_positive_int(payload.get("gt")),
            deadweight_tonnes=_positive_int(payload.get("dw")),
            length_overall_m=_positive_float(payload.get("al")),
            beam_m=_positive_float(payload.get("aw")),
            year_built=_positive_int(payload.get("y")),
            flag=(payload.get("country") or "").strip() or None,
        ),
    )


def merge_details(snapshot: VesselFinderSnapshot, details: Dict[str, Any]) -> VesselFinderSnapshot:
    """Fold parsed detail-page fields into a click-API snapshot (in place)."""
    snapshot.call_sign = snapshot.call_sign or details.get("call_sign")
    snapshot.ship_type = snapshot.ship_type or details.get("ship_type") or details.get("ais_type")
    snapshot.imo_number = snapshot.imo_number or details.get("imo_number")
    if snapshot.draught_meters is None:
        snapshot.draught_meters = details.get("draught_meters")

    if snapshot.speed_knots is None:
        snapshot.speed_knots = details.get("speed_knots")
    if snapshot.course_deg is None:
        snapshot.course_deg = details.get("course_deg")

    if "latitude" in details and "longitude" in details:
        snapshot.latitude = details["latitude"]
        snapshot.longitude = details["longitude"]
        snapshot.position_is_approximate = True
    snapshot.position_age_text = snapshot.position_age_text or details.get("position_age_text")

    p = snapshot.particulars
    p.gross_tonnage = p.gross_tonnage or details.get("gross_tonnage")
    p.length_overall_m = p.length_overall_m or details.get("length_overall_m")
    p.beam_m = p.beam_m or details.get("beam_m")
    p.year_built = p.year_built or details.get("year_built")
    p.flag = p.flag or details.get("flag")
    return snapshot


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------


class VesselFinderScraper:
    """Polite HTTP reader for VesselFinder's public endpoints.

    Serialises requests behind a single lock with a minimum spacing between
    them, caches responses for a TTL, and backs off on 429 / 5xx (Rule A02).
    """

    def __init__(
        self,
        min_request_interval_sec: float = 5.0,
        timeout_sec: float = 15.0,
        click_cache_ttl_sec: float = 60.0,
        details_cache_ttl_sec: float = 21600.0,
        max_retries: int = 3,
        user_agent: str = DEFAULT_USER_AGENT,
    ):
        self.min_request_interval_sec = max(0.0, min_request_interval_sec)
        self.timeout_sec = timeout_sec
        self.click_cache_ttl_sec = click_cache_ttl_sec
        self.details_cache_ttl_sec = details_cache_ttl_sec
        self.max_retries = max(1, max_retries)
        self.user_agent = user_agent

        self._client: Optional[httpx.AsyncClient] = None
        self._throttle = asyncio.Lock()
        self._last_request_at = 0.0
        self._cache: Dict[str, Tuple[float, Any]] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=BASE_URL,
                timeout=self.timeout_sec,
                follow_redirects=True,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept-Language": "en-US,en;q=0.9",
                    "Referer": f"{BASE_URL}/",
                },
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    def _cached(self, key: str, ttl: float) -> Tuple[bool, Any]:
        entry = self._cache.get(key)
        if entry is None:
            return False, None
        stored_at, value = entry
        if ttl > 0 and (time.monotonic() - stored_at) > ttl:
            self._cache.pop(key, None)
            return False, None
        return True, value

    def _store(self, key: str, value: Any) -> None:
        self._cache[key] = (time.monotonic(), value)

    async def _request(self, path: str) -> Optional[httpx.Response]:
        """Throttled GET with retry/backoff. Returns ``None`` when unavailable."""
        client = await self._get_client()
        delay = 2.0
        transport_error = ""

        for attempt in range(1, self.max_retries + 1):
            async with self._throttle:
                wait = self.min_request_interval_sec - (time.monotonic() - self._last_request_at)
                if wait > 0:
                    await asyncio.sleep(wait)
                try:
                    response = await client.get(path)
                except httpx.RequestError as e:
                    # Timeouts/DNS/connection resets: retry here rather than
                    # surfacing a transport blip as a missing vessel. Several
                    # of these carry an empty str(), so report the type.
                    response = None
                    transport_error = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
                finally:
                    self._last_request_at = time.monotonic()

            if response is None:
                if attempt < self.max_retries:
                    logger.warning(
                        f"VesselFinder request to {path} failed ({transport_error}); "
                        f"retrying in {delay:.0f}s ({attempt}/{self.max_retries})."
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 60.0)
                    continue
                raise httpx.TransportError(
                    f"VesselFinder unreachable for {path} after {attempt} attempts ({transport_error})"
                )

            if response.status_code == 200:
                return response
            if response.status_code == 404:
                logger.debug(f"VesselFinder 404 for {path}")
                return None
            if response.status_code in (403, 429) or response.status_code >= 500:
                retry_after = _as_float(response.headers.get("Retry-After")) or delay
                if attempt < self.max_retries:
                    logger.warning(
                        f"VesselFinder returned {response.status_code} for {path}; "
                        f"retrying in {retry_after:.0f}s ({attempt}/{self.max_retries})."
                    )
                    await asyncio.sleep(retry_after)
                    delay = min(delay * 2, 60.0)
                    continue
                logger.error(f"VesselFinder gave up on {path} after {attempt} attempts ({response.status_code}).")
                return None

            logger.warning(f"Unexpected status {response.status_code} from VesselFinder for {path}.")
            return None
        return None

    async def fetch_click(self, mmsi: int) -> Optional[Dict[str, Any]]:
        """Fetch the map popup JSON for an MMSI."""
        key = f"click:{mmsi}"
        hit, value = self._cached(key, self.click_cache_ttl_sec)
        if hit:
            return value

        response = await self._request(f"/api/pub/click/{int(mmsi)}")
        payload: Optional[Dict[str, Any]] = None
        if response is not None:
            try:
                decoded = response.json()
                if isinstance(decoded, dict):
                    payload = decoded
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Unparseable click payload for MMSI {mmsi}: {e}")

        self._store(key, payload)
        return payload

    async def fetch_details(self, identifier: int) -> Optional[Dict[str, Any]]:
        """Fetch and parse a vessel detail page.

        ``identifier`` is the vessel's IMO number when it has one; vessels
        without an IMO (small craft, many ferries and tugs) are reachable at
        the same path by MMSI instead.
        """
        if not identifier or identifier <= 0:
            return None

        key = f"details:{identifier}"
        hit, value = self._cached(key, self.details_cache_ttl_sec)
        if hit:
            return value

        response = await self._request(f"/vessels/details/{int(identifier)}")
        details = parse_details_html(response.text) if response is not None else None
        self._store(key, details)
        return details

    async def fetch_voyage(self, mmsi: int) -> Optional[Dict[str, Any]]:
        """Fetch the voyage / last-port-call JSON for an MMSI."""
        key = f"voyage:{mmsi}"
        hit, value = self._cached(key, self.click_cache_ttl_sec)
        if hit:
            return value

        response = await self._request(f"/api/pub/vi2/{int(mmsi)}?fv")
        payload: Optional[Dict[str, Any]] = None
        if response is not None:
            try:
                decoded = response.json()
                if isinstance(decoded, dict):
                    payload = decoded
            except (json.JSONDecodeError, ValueError):
                payload = None

        self._store(key, payload)
        return payload

    async def fetch_snapshot(self, mmsi: int, enrich_details: bool = True) -> Optional[VesselFinderSnapshot]:
        """Merge the click API and (optionally) the detail page into a snapshot."""
        payload = await self.fetch_click(mmsi)
        if payload is None:
            return None

        snapshot = parse_click_payload(mmsi, payload)
        if enrich_details:
            # Vessels without an IMO are served at the same path by MMSI.
            details = await self.fetch_details(snapshot.imo_number or mmsi)
            if details:
                merge_details(snapshot, details)
        return snapshot

    async def fetch_vessel_particulars(self, mmsi: int) -> Optional[VesselParticulars]:
        """Fetch master data for a single MMSI (used for dashboard enrichment)."""
        snapshot = await self.fetch_snapshot(mmsi)
        return snapshot.particulars if snapshot else None


# ---------------------------------------------------------------------------
# DataSource
# ---------------------------------------------------------------------------


class VesselFinderSource(DataSource):
    """Polls VesselFinder's public endpoints for a watchlist of MMSIs.

    This is a watchlist poller, not a feed: VesselFinder publishes no
    unauthenticated bounding-box query, so ``target_mmsis`` is required.

    Read the module docstring before recording positions from this source -
    public coordinates are rounded to whole degrees.
    """

    #: Floor on the poll interval, so a misconfiguration cannot hammer the site.
    MIN_POLL_INTERVAL_SEC = 30.0

    def __init__(
        self,
        target_mmsis: Optional[List[int]] = None,
        poll_interval_sec: float = 120.0,
        scraper: Optional[VesselFinderScraper] = None,
        enrich_details: bool = True,
        include_approximate_position: bool = True,
        min_request_interval_sec: float = 5.0,
    ):
        super().__init__(name="vesselfinder")
        self.target_mmsis = [int(m) for m in (target_mmsis or [])]
        self.poll_interval_sec = max(self.MIN_POLL_INTERVAL_SEC, poll_interval_sec)
        self.enrich_details = enrich_details
        self.include_approximate_position = include_approximate_position
        self.scraper = scraper or VesselFinderScraper(min_request_interval_sec=min_request_interval_sec)

        # mmsi -> last emitted position timestamp / static fingerprint
        self._last_position_ts: Dict[int, Optional[datetime]] = {}
        self._last_static_fingerprint: Dict[int, str] = {}

    async def start(self) -> None:
        self._running = True
        logger.info(
            f"VesselFinderSource started (watching {len(self.target_mmsis)} vessels, "
            f"poll every {self.poll_interval_sec:.0f}s)."
        )
        if self.include_approximate_position:
            logger.warning(
                "VesselFinder's public pages round coordinates to whole degrees "
                f"(+/-{POSITION_UNCERTAINTY_DEG} deg, ~55 km). Recorded positions from this "
                "source are approximate and tagged POS:APPROX; use aisstream for precise tracks."
            )

    async def stop(self) -> None:
        self._running = False
        await self.scraper.aclose()
        logger.info("VesselFinderSource stopped.")

    async def fetch_vessel_particulars(self, mmsi: int) -> Optional[VesselParticulars]:
        """Convenience passthrough for one-off particulars enrichment."""
        return await self.scraper.fetch_vessel_particulars(mmsi)

    def _emit(
        self, snapshot: VesselFinderSnapshot
    ) -> Iterator[Tuple[str, Optional[VesselDynamic], Optional[VesselStatic]]]:
        """Yield static/dynamic reports for a snapshot, skipping unchanged data."""
        static = snapshot.to_static()
        fingerprint = static.model_dump_json()
        if self._last_static_fingerprint.get(snapshot.mmsi) != fingerprint:
            self._last_static_fingerprint[snapshot.mmsi] = fingerprint
            yield snapshot.raw_sentence("vd"), None, static

        if not self.include_approximate_position:
            return

        dynamic = snapshot.to_dynamic()
        if dynamic is None:
            return

        # Only record a position when VesselFinder reports a newer AIS fix.
        previous_ts = self._last_position_ts.get(snapshot.mmsi)
        if snapshot.position_time_utc is not None and previous_ts == snapshot.position_time_utc:
            return
        self._last_position_ts[snapshot.mmsi] = snapshot.position_time_utc

        yield snapshot.raw_sentence("click"), dynamic, None

    async def stream(self) -> AsyncGenerator[Tuple[str, Optional[VesselDynamic], Optional[VesselStatic]], None]:
        """Poll each watched MMSI in turn, yielding reports as they change."""
        if not self.target_mmsis:
            logger.warning(
                "VesselFinderSource has no target MMSIs. Set 'vesselfinder.target_mmsis' "
                "in config.yaml or pass target_mmsis=[...]."
            )
            return

        while self._running:
            for mmsi in self.target_mmsis:
                if not self._running:
                    break
                try:
                    snapshot = await self.scraper.fetch_snapshot(mmsi, enrich_details=self.enrich_details)
                except Exception as e:
                    logger.error(f"Error scraping VesselFinder for MMSI {mmsi}: {e}")
                    continue

                if snapshot is None:
                    logger.debug(f"VesselFinder returned no data for MMSI {mmsi}.")
                    continue

                for report in self._emit(snapshot):
                    yield report

            if self._running:
                await asyncio.sleep(self.poll_interval_sec)
