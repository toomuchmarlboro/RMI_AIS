"""Unit tests for the VesselFinder scraper source.

All fixtures are verbatim shapes captured from vesselfinder.com's public
endpoints, so the parsers are exercised without any network access.
"""
import asyncio

import httpx
import pytest

from src.models.vessel import NavigationStatus, Vessel
from src.sources.factory import build_source, parse_mmsi_list
from src.sources.vesselfinder import (
    POSITION_UNCERTAINTY_DEG,
    VesselFinderScraper,
    VesselFinderSource,
    merge_details,
    parse_click_payload,
    parse_details_html,
)

# /api/pub/click/636092932 - a fully populated container ship.
CLICK_ILONA = {
    "country": "Liberia",
    "imo": 9225641,
    "pic": "9225641-636092932-80655f56bb8f2d9c48fbadc391882410",
    "dest": "CNTAO>MXLZC",
    "type": "Container Ship",
    "dw": 85890,
    "etaTS": 1792886400,
    "sl": True,
    "drm": "14.5",
    "ss": 17.1,
    "al": 300,
    "m9": 0,
    "gt": 75590,
    "a2": "lr",
    "r": 1,
    "cu": 84.0,
    ".ns": -1,
    "draught": 128,
    "aw": 40,
    "name": "ILONA",
    "y": 2001,
    "ts": 1791647805,
}

# /api/pub/click/<mmsi> for an MMSI VesselFinder has no record of.
CLICK_UNKNOWN = {
    "ss": -1,
    "country": "Indonesia",
    "imo": 0,
    "al": 0,
    "pic": "",
    "dest": "",
    "type": "unknown type",
    "m9": 0,
    "gt": 0,
    "d2": 0,
    "a2": "id",
    "cu": -1,
    "r": 0,
    "dw": 0,
    "etaTS": 0,
    "draught": 0,
    "aw": 0,
    "name": "525019876",
    "y": 0,
    "ts": -1,
}

# Trimmed /vessels/details/9225641 markup, preserving the real class names.
DETAILS_HTML = """
<table class="aparams">
<tr><td class="n3">Current draught</td><td class="v3">12.8 m</td></tr>
<tr><td class="n3">Navigation Status</td><td class="v3" id="nvs0">&nbsp;</td></tr>
<tr><td class="n3">Position Received</td><td class="v3" id="prt0">&nbsp;</td></tr>
<tr><td class="n3">IMO / MMSI</td><td class="v3 v3np">9225641 / 636092932</td></tr>
<tr><td class="n3">Callsign</td><td class="v3">D5VK6</td></tr>
<tr><td class="n3">AIS Type</td><td class="v3">Cargo ship (HAZ-A)</td></tr>
<tr><td class="n3">AIS Flag</td><td class="v3">Liberia</td></tr>
<tr><td class="n3">Length / Beam</td><td class="v3">300 / 40 m</td></tr>
</table>
<table class="tparams">
<tr><td class="tpc1">Vessel Name</td><td class="tpc2">ILONA</td></tr>
<tr><td class="tpc1">Ship Type</td><td class="tpc2">Container Ship</td></tr>
<tr><td class="tpc1">Flag</td><td class="tpc2">Liberia</td></tr>
<tr><td class="tpc1">Year of Build</td><td class="tpc2">2001</td></tr>
<tr><td class="tpc1">Gross Tonnage</td><td class="tpc2">75590</td></tr>
<tr><td class="tpc1">Engine Type</td><td class="tpc2">-</td></tr>
</table>
<script>var ship_data = { "v":true,"lrpd":"4 days ago ","lrpc":1,"sar":false,
"mmsi":636092932,"ship_lat":42,"ship_lon":142,"ship_cog":84.0,"ship_sog":17.1,
"ship_type":71,"no_pc":"No port calls detected"};</script>
"""


# ---------------------------------------------------------------------------
# Click payload parsing
# ---------------------------------------------------------------------------


def test_parse_click_payload_full():
    snap = parse_click_payload(636092932, CLICK_ILONA)

    assert snap.mmsi == 636092932
    assert snap.name == "ILONA"
    assert snap.imo_number == 9225641
    assert snap.ship_type == "Container Ship"
    assert snap.destination == "CNTAO>MXLZC"
    assert snap.speed_knots == 17.1
    assert snap.course_deg == 84.0
    # 'draught' arrives decimetres x10
    assert snap.draught_meters == 12.8
    # '.ns' of -1 means unknown, not status 0
    assert snap.nav_status_code == int(NavigationStatus.UNDEFINED)

    assert snap.particulars.gross_tonnage == 75590
    assert snap.particulars.deadweight_tonnes == 85890
    assert snap.particulars.length_overall_m == 300.0
    assert snap.particulars.beam_m == 40.0
    assert snap.particulars.year_built == 2001
    assert snap.particulars.flag == "Liberia"


def test_parse_click_payload_eta_and_timestamp_are_utc():
    snap = parse_click_payload(636092932, CLICK_ILONA)

    assert snap.position_time_utc is not None
    assert snap.position_time_utc.tzinfo is not None
    assert snap.position_time_utc.year == 2026
    # Rendered in the 'MM-DD HH:MM' form the rest of the project uses
    assert snap.eta == "10-25 00:00"


def test_parse_click_payload_unknown_vessel_yields_no_junk():
    snap = parse_click_payload(525019876, CLICK_UNKNOWN)

    # VesselFinder echoes the MMSI as the name when it knows nothing
    assert snap.name is None
    assert snap.ship_type is None
    assert snap.imo_number is None
    assert snap.destination is None
    assert snap.eta is None
    assert snap.draught_meters is None
    assert snap.position_time_utc is None
    # Sentinel -1 values must not become real readings
    assert snap.speed_knots is None
    assert snap.course_deg is None
    # 0 means 'unknown' for these, not a real measurement
    assert snap.particulars.gross_tonnage is None
    assert snap.particulars.length_overall_m is None
    assert snap.particulars.year_built is None


def test_parse_click_payload_accepts_valid_nav_status():
    snap = parse_click_payload(232003233, {**CLICK_ILONA, ".ns": 5})
    assert snap.nav_status_code == 5
    assert snap.nav_status_label == "Moored"


def test_snapshot_without_position_has_no_dynamic_report():
    snap = parse_click_payload(525019876, CLICK_UNKNOWN)
    assert snap.has_position is False
    assert snap.to_dynamic() is None
    # Static identity is still emitted
    assert snap.to_static().mmsi == 525019876


# ---------------------------------------------------------------------------
# Detail page parsing
# ---------------------------------------------------------------------------


def test_parse_details_html():
    details = parse_details_html(DETAILS_HTML)

    assert details["call_sign"] == "D5VK6"
    assert details["ais_type"] == "Cargo ship (HAZ-A)"
    assert details["ship_type"] == "Container Ship"
    assert details["flag"] == "Liberia"
    assert details["length_overall_m"] == 300.0
    assert details["beam_m"] == 40.0
    assert details["draught_meters"] == 12.8
    assert details["gross_tonnage"] == 75590
    assert details["year_built"] == 2001
    assert details["imo_number"] == 9225641
    assert details["mmsi"] == 636092932


def test_parse_details_html_position_is_flagged_approximate():
    details = parse_details_html(DETAILS_HTML)

    assert details["latitude"] == 42.0
    assert details["longitude"] == 142.0
    assert details["position_is_approximate"] is True
    assert details["position_age_text"] == "4 days ago"


def test_parse_details_html_skips_js_populated_rows():
    """Navigation Status / Position Received are filled in client-side."""
    details = parse_details_html(DETAILS_HTML)
    assert "nav_status_code" not in details
    assert "position_received" not in details


def test_parse_details_html_tolerates_garbage():
    assert parse_details_html("") == {}
    assert parse_details_html("<html><body>404</body></html>") == {}


def test_merge_details_fills_gaps_without_clobbering():
    snap = parse_click_payload(636092932, CLICK_ILONA)
    snap.particulars.flag = "Liberia"
    merge_details(snap, parse_details_html(DETAILS_HTML))

    # Filled from the detail page (absent from the click API)
    assert snap.call_sign == "D5VK6"
    assert snap.has_position
    assert snap.position_is_approximate is True
    # Click API values survive
    assert snap.ship_type == "Container Ship"
    assert snap.particulars.deadweight_tonnes == 85890


# ---------------------------------------------------------------------------
# Report construction
# ---------------------------------------------------------------------------


def test_raw_sentence_tags_approximate_positions():
    snap = parse_click_payload(636092932, CLICK_ILONA)
    merge_details(snap, parse_details_html(DETAILS_HTML))
    raw = snap.raw_sentence("click")

    assert raw.startswith("[vesselfinder:click]")
    assert "MMSI:636092932" in raw
    assert "IMO:9225641" in raw
    assert f"POS:APPROX(+/-{POSITION_UNCERTAINTY_DEG}deg)" in raw


def test_dynamic_report_clamps_sentinel_values():
    snap = parse_click_payload(636092932, {**CLICK_ILONA, "ss": -1, "cu": -1})
    merge_details(snap, parse_details_html(DETAILS_HTML))
    # merge_details backfills SOG/COG from the detail page bootstrap
    snap.speed_knots = None
    snap.course_deg = None

    dyn = snap.to_dynamic()
    assert dyn is not None
    # Model bounds require non-negative SOG and 0-360 COG
    assert dyn.speed_knots == 0.0
    assert dyn.course_deg == 0.0
    assert dyn.heading_deg is None  # never published publicly


def test_static_report_carries_particulars_into_vessel():
    """Master data must survive the DataSource -> Vessel handoff."""
    snap = parse_click_payload(636092932, CLICK_ILONA)
    vessel = Vessel(mmsi=636092932)
    vessel.update_static(snap.to_static())

    assert vessel.name == "ILONA"
    assert vessel.call_sign is None  # click API alone has no call sign
    assert vessel.particulars.gross_tonnage == 75590
    assert vessel.particulars.deadweight_tonnes == 85890
    assert vessel.particulars.year_built == 2001
    assert vessel.particulars.flag == "Liberia"


def test_update_static_keeps_existing_particulars_for_blank_fields():
    vessel = Vessel(mmsi=525019876)
    vessel.particulars.gross_tonnage = 9989
    vessel.update_static(parse_click_payload(525019876, CLICK_UNKNOWN).to_static())

    assert vessel.particulars.gross_tonnage == 9989


# ---------------------------------------------------------------------------
# Source behaviour (offline, via a stub scraper)
# ---------------------------------------------------------------------------


class StubScraper(VesselFinderScraper):
    """Returns canned snapshots instead of making requests."""

    def __init__(self, payloads):
        super().__init__(min_request_interval_sec=0.0)
        self.payloads = payloads
        self.calls = 0

    async def fetch_snapshot(self, mmsi, enrich_details=True):
        self.calls += 1
        payload = self.payloads.get(mmsi)
        if payload is None:
            return None
        snap = parse_click_payload(mmsi, payload)
        merge_details(snap, parse_details_html(DETAILS_HTML))
        return snap

    async def aclose(self):
        return None


def _drain(source, limit):
    async def _run():
        await source.start()
        out = []
        async for report in source.stream():
            out.append(report)
            if len(out) >= limit:
                break
        await source.stop()
        return out

    return asyncio.run(_run())


def test_stream_emits_static_then_dynamic():
    source = VesselFinderSource(
        target_mmsis=[636092932],
        poll_interval_sec=30.0,
        scraper=StubScraper({636092932: CLICK_ILONA}),
    )
    reports = _drain(source, 2)

    assert len(reports) == 2
    raw_static, dyn_static, static = reports[0]
    assert dyn_static is None and static is not None
    assert static.ship_name == "ILONA"
    assert raw_static.startswith("[vesselfinder:vd]")

    raw_dyn, dynamic, st_none = reports[1]
    assert st_none is None and dynamic is not None
    assert dynamic.mmsi == 636092932
    assert dynamic.latitude == 42.0
    assert raw_dyn.startswith("[vesselfinder:click]")


def test_stream_does_not_repeat_unchanged_reports(monkeypatch):
    """Repeated polls with an identical AIS timestamp must emit nothing new."""
    monkeypatch.setattr(VesselFinderSource, "MIN_POLL_INTERVAL_SEC", 0.0)
    stub = StubScraper({636092932: CLICK_ILONA})
    source = VesselFinderSource(target_mmsis=[636092932], poll_interval_sec=0.0, scraper=stub)

    async def _collect(out):
        async for report in source.stream():
            out.append(report)

    async def _run():
        await source.start()
        out = []
        task = asyncio.create_task(_collect(out))
        await asyncio.sleep(0.2)
        task.cancel()
        await source.stop()
        return out

    reports = asyncio.run(_run())
    # Many poll cycles ran, but the data never changed
    assert stub.calls > 1
    assert len(reports) == 2  # one static + one dynamic, total


def test_stream_emits_again_when_position_timestamp_advances(monkeypatch):
    monkeypatch.setattr(VesselFinderSource, "MIN_POLL_INTERVAL_SEC", 0.0)
    stub = StubScraper({636092932: CLICK_ILONA})
    source = VesselFinderSource(target_mmsis=[636092932], poll_interval_sec=0.0, scraper=stub)

    async def _run():
        await source.start()
        out = []
        async for report in source.stream():
            out.append(report)
            if len(out) == 2:
                # Simulate a fresh AIS fix arriving before the next poll
                stub.payloads[636092932] = {**CLICK_ILONA, "ts": CLICK_ILONA["ts"] + 600}
            if len(out) >= 3:
                break
        await source.stop()
        return out

    reports = asyncio.run(_run())
    assert len(reports) == 3
    # The third report is a new position, not a repeated static record
    dyn = reports[2][1]
    assert dyn is not None
    assert dyn.timestamp.timestamp() == CLICK_ILONA["ts"] + 600


def test_include_approximate_position_false_suppresses_coordinates():
    source = VesselFinderSource(
        target_mmsis=[636092932],
        poll_interval_sec=0.01,
        scraper=StubScraper({636092932: CLICK_ILONA}),
        include_approximate_position=False,
    )
    reports = _drain(source, 1)

    assert len(reports) == 1
    assert reports[0][1] is None  # no dynamic report at all
    assert reports[0][2] is not None


def test_stream_without_watchlist_exits_cleanly():
    source = VesselFinderSource(target_mmsis=[])

    async def _run():
        await source.start()
        out = [r async for r in source.stream()]
        await source.stop()
        return out

    assert asyncio.run(_run()) == []


def test_stream_skips_mmsis_vesselfinder_does_not_know():
    source = VesselFinderSource(
        target_mmsis=[111111111, 636092932],
        poll_interval_sec=30.0,
        scraper=StubScraper({636092932: CLICK_ILONA}),
    )
    reports = _drain(source, 1)

    assert reports[0][2].mmsi == 636092932


def test_poll_interval_has_a_floor():
    """Guard against hammering the site with a tiny configured interval."""
    assert VesselFinderSource(target_mmsis=[1], poll_interval_sec=0.5).poll_interval_sec == 30.0


# ---------------------------------------------------------------------------
# Scraper request routing (stubbed transport, no sockets)
# ---------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, payload=None, text=""):
        self._payload = payload
        self.text = text
        self.status_code = 200
        self.headers = {}

    def json(self):
        return self._payload


def _recording_scraper(click_payload, detail_html=DETAILS_HTML):
    """A scraper whose transport is replaced by a path recorder."""
    scraper = VesselFinderScraper(min_request_interval_sec=0.0)
    paths = []

    async def fake_request(path):
        paths.append(path)
        if "/api/pub/click/" in path:
            return FakeResponse(payload=click_payload)
        if "/vessels/details/" in path:
            return FakeResponse(text=detail_html)
        return None

    scraper._request = fake_request
    return scraper, paths


def test_fetch_snapshot_uses_imo_for_the_detail_page():
    scraper, paths = _recording_scraper(CLICK_ILONA)
    snap = asyncio.run(scraper.fetch_snapshot(636092932))

    assert paths == ["/api/pub/click/636092932", "/vessels/details/9225641"]
    assert snap.call_sign == "D5VK6"


def test_fetch_snapshot_falls_back_to_mmsi_when_there_is_no_imo():
    """Vessels with no IMO (ferries, tugs, small craft) still resolve."""
    no_imo = {**CLICK_ILONA, "imo": 0}
    scraper, paths = _recording_scraper(no_imo)
    asyncio.run(scraper.fetch_snapshot(211627650))

    assert paths == ["/api/pub/click/211627650", "/vessels/details/211627650"]


def test_fetch_snapshot_skips_the_detail_page_when_not_enriching():
    scraper, paths = _recording_scraper(CLICK_ILONA)
    asyncio.run(scraper.fetch_snapshot(636092932, enrich_details=False))

    assert paths == ["/api/pub/click/636092932"]


def test_transport_errors_retry_then_raise_with_a_useful_message(monkeypatch):
    """A timeout must surface as a transport error, not 'vessel not found'."""
    monkeypatch.setattr(asyncio, "sleep", lambda _: _noop())

    scraper = VesselFinderScraper(min_request_interval_sec=0.0, max_retries=3)
    attempts = []

    class TimingOutClient:
        is_closed = False

        async def get(self, path):
            attempts.append(path)
            # httpx timeout exceptions commonly carry an empty str()
            raise httpx.ReadTimeout("")

    async def _client():
        return TimingOutClient()

    scraper._get_client = _client

    with pytest.raises(httpx.TransportError) as excinfo:
        asyncio.run(scraper.fetch_click(636092932))

    assert len(attempts) == 3  # retried up to max_retries
    assert "ReadTimeout" in str(excinfo.value)


async def _noop():
    return None


def test_scraper_caches_responses_including_misses():
    scraper, paths = _recording_scraper(CLICK_ILONA)

    async def _run():
        await scraper.fetch_snapshot(636092932)
        await scraper.fetch_snapshot(636092932)

    asyncio.run(_run())
    # Second call served entirely from cache
    assert len(paths) == 2


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def test_parse_mmsi_list_filters_invalid_entries():
    assert parse_mmsi_list("636092932, 232003233") == [636092932, 232003233]
    assert parse_mmsi_list([636092932, "bogus", 123, None]) == [636092932]
    assert parse_mmsi_list(None) == []
    assert parse_mmsi_list("") == []


def test_build_source_vesselfinder_from_config():
    cfg = {
        "vesselfinder": {
            "target_mmsis": [636092932],
            "poll_interval_sec": 300.0,
            "include_approximate_position": False,
        }
    }
    source = build_source("vesselfinder", cfg)

    assert source.name == "vesselfinder"
    assert source.target_mmsis == [636092932]
    assert source.poll_interval_sec == 300.0
    assert source.include_approximate_position is False


def test_build_source_vesselfinder_falls_back_to_preset_fleet(monkeypatch):
    monkeypatch.delenv("VESSELFINDER_TARGET_MMSIS", raising=False)
    cfg = {"simulator": {"vessels": [{"mmsi": 525019876}, {"mmsi": 525022134}]}}

    source = build_source("vesselfinder", cfg)
    assert source.target_mmsis == [525019876, 525022134]


def test_build_source_rejects_unknown_mode():
    with pytest.raises(ValueError, match="Unsupported data source mode"):
        build_source("marinetraffic", {})


def test_build_source_aisstream_requires_a_key(monkeypatch):
    monkeypatch.delenv("AISSTREAM_API_KEY", raising=False)
    with pytest.raises(ValueError, match="API key"):
        build_source("aisstream", {})
