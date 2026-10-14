"""Background particulars enrichment (Rule D08, A02, A03).

The hybrid architecture this implements:

  * **aisstream.io supplies positions** - precise, real-time, high-frequency.
  * **VesselFinder supplies particulars** - gross tonnage, deadweight, LOA,
    beam, year built and flag, none of which AIS transmits at all.

A position feed discovers far more vessels than are worth looking up (a wide
bounding box over Indonesia yields tens of thousands, most seen once), so this
enricher is deliberately **scoped and budgeted** rather than exhaustive:

  * ``scope`` decides which vessels are even eligible (default: only vessels
    being recorded).
  * ``max_per_hour`` caps outbound lookups with a sliding window.
  * Results are persisted, so a vessel is looked up once and then left alone
    until ``refresh_after_days``; failures back off for ``retry_after_days``.
"""
from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Callable, Deque, Optional, Set

from src.models.vessel import Vessel, VesselStatic
from src.storage.database import DatabaseManager
from src.sources.vesselfinder import VesselFinderScraper, VesselFinderSnapshot

logger = logging.getLogger(__name__)

#: Names the database/UI use when a vessel's identity is still unknown.
_PLACEHOLDER_NAMES = {"", "unknown vessel", "unspecified"}


class EnrichmentScope(str, Enum):
    """Which vessels from the position feed are eligible for lookup."""

    WATCHLIST = "watchlist"  # only a session's explicit target MMSIs
    RECORDING = "recording"  # any vessel observed while recording (default)
    ALL = "all"             # every vessel the feed reports (expensive)


def _is_placeholder_name(name: Optional[str], mmsi: int) -> bool:
    """True when a vessel's name is a stand-in rather than a real AIS name."""
    if not name:
        return True
    cleaned = name.strip().lower()
    return (
        cleaned in _PLACEHOLDER_NAMES
        or cleaned == str(mmsi)
        or cleaned == f"vessel-{mmsi}"
    )


def build_gap_filling_static(vessel: Vessel, snapshot: VesselFinderSnapshot) -> VesselStatic:
    """Project a scraped snapshot onto only what the vessel is missing.

    AIS stays authoritative for everything it actually transmits - voyage data
    (destination, ETA, draught) and dynamics are deliberately **not** taken
    from VesselFinder, whose copy may be staler than the live feed.

    Particulars are the exception: VesselFinder's registered tonnage and
    dimensions supersede values derived from AIS antenna offsets.
    """
    return VesselStatic(
        mmsi=vessel.mmsi,
        ship_name=snapshot.name if _is_placeholder_name(vessel.name, vessel.mmsi) else None,
        ship_type=snapshot.ship_type if not vessel.ship_type or vessel.ship_type == "Unspecified" else None,
        call_sign=snapshot.call_sign if not vessel.call_sign else None,
        imo_number=snapshot.imo_number if not vessel.imo_number else None,
        particulars=snapshot.particulars,
    )


class ParticularsEnricher:
    """Fills in vessel master data from VesselFinder, off the ingestion path."""

    SOURCE_NAME = "vesselfinder"

    def __init__(
        self,
        db: DatabaseManager,
        scraper: Optional[VesselFinderScraper] = None,
        enabled: bool = True,
        scope: str = EnrichmentScope.RECORDING.value,
        max_per_hour: int = 60,
        refresh_after_days: float = 30.0,
        retry_after_days: float = 7.0,
        queue_size: int = 500,
        breaker_threshold: int = 5,
        breaker_cooldown_sec: float = 900.0,
    ):
        self.db = db
        self.scraper = scraper or VesselFinderScraper()
        self.enabled = enabled
        self.max_per_hour = max(0, max_per_hour)
        self.refresh_after = timedelta(days=refresh_after_days)
        self.retry_after = timedelta(days=retry_after_days)
        self.breaker_threshold = max(1, breaker_threshold)
        self.breaker_cooldown = timedelta(seconds=breaker_cooldown_sec)

        try:
            self.scope = EnrichmentScope(str(scope).strip().lower())
        except ValueError:
            logger.warning(f"Unknown enrichment scope {scope!r}; falling back to 'recording'.")
            self.scope = EnrichmentScope.RECORDING

        self._queue: asyncio.Queue[int] = asyncio.Queue(maxsize=max(1, queue_size))
        self._pending: Set[int] = set()
        self._worker: Optional[asyncio.Task] = None
        self._lookups: Deque[datetime] = deque()

        #: Called with (mmsi, snapshot) after a successful lookup, so a live
        #: in-memory vessel registry can apply the result without re-reading
        #: the database (and without clobbering the newer live position).
        self.on_enriched: Optional[Callable[[int, VesselFinderSnapshot], None]] = None

        # Counters surfaced on the dashboard
        self.enriched_count = 0
        self.not_found_count = 0
        self.error_count = 0
        self.skipped_budget_count = 0

        # Circuit breaker: vesselfinder.com throttles persistent clients, and
        # once it starts refusing connections there is nothing to gain by
        # continuing to call it. Trips after consecutive transport failures.
        self._consecutive_errors = 0
        self._breaker_opened_at: Optional[datetime] = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def start(self) -> None:
        if not self.enabled:
            logger.info("Particulars enrichment is disabled.")
            return
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(self._run())
            logger.info(
                f"Particulars enricher started (source={self.SOURCE_NAME}, "
                f"scope={self.scope.value}, max {self.max_per_hour}/h)."
            )

    async def stop(self) -> None:
        if self._worker and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        self._worker = None
        await self.scraper.aclose()
        logger.info(
            f"Particulars enricher stopped (enriched={self.enriched_count}, "
            f"not_found={self.not_found_count}, errors={self.error_count})."
        )

    @property
    def is_paused(self) -> bool:
        """True while the circuit breaker is open."""
        if self._breaker_opened_at is None:
            return False
        if datetime.now(timezone.utc) - self._breaker_opened_at > self.breaker_cooldown:
            logger.info("Enrichment circuit breaker cooled down; resuming lookups.")
            self._breaker_opened_at = None
            self._consecutive_errors = 0
            return False
        return True

    @property
    def stats(self) -> dict:
        return {
            "enabled": self.enabled,
            "source": self.SOURCE_NAME,
            "scope": self.scope.value,
            "max_per_hour": self.max_per_hour,
            "queued": self._queue.qsize(),
            "lookups_last_hour": len(self._lookups),
            "enriched": self.enriched_count,
            "not_found": self.not_found_count,
            "errors": self.error_count,
            "skipped_over_budget": self.skipped_budget_count,
            "paused": self.is_paused,
        }

    def _record_success(self) -> None:
        self._consecutive_errors = 0
        self._breaker_opened_at = None

    def _record_failure(self) -> None:
        self._consecutive_errors += 1
        if self._consecutive_errors >= self.breaker_threshold and self._breaker_opened_at is None:
            self._breaker_opened_at = datetime.now(timezone.utc)
            logger.warning(
                f"{self._consecutive_errors} consecutive enrichment failures - pausing lookups for "
                f"{self.breaker_cooldown.total_seconds() / 60:.0f} min. "
                f"vesselfinder.com may be throttling this client; positions are unaffected."
            )

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------
    def is_eligible(self, is_recording: bool = False, is_target: bool = False) -> bool:
        """Apply the configured scope to a sighting."""
        if self.scope is EnrichmentScope.ALL:
            return True
        if self.scope is EnrichmentScope.RECORDING:
            return is_recording
        return is_target  # WATCHLIST

    def submit(self, mmsi: int, is_recording: bool = False, is_target: bool = False) -> bool:
        """Offer a vessel for enrichment. Never blocks and never raises.

        Returns True when the MMSI was accepted onto the queue.
        """
        if not self.enabled or not mmsi:
            return False
        if not self.is_eligible(is_recording=is_recording, is_target=is_target):
            return False
        if mmsi in self._pending:
            return False
        if not self._needs_enrichment(mmsi):
            return False

        try:
            self._queue.put_nowait(mmsi)
        except asyncio.QueueFull:
            logger.debug(f"Enrichment queue full; dropping MMSI {mmsi}.")
            return False

        self._pending.add(mmsi)
        return True

    def _needs_enrichment(self, mmsi: int) -> bool:
        """Consult persisted state so a vessel is looked up at most once."""
        state = self.db.get_enrichment_state(mmsi)
        if not state or not state["status"]:
            return True

        now = datetime.now(timezone.utc)
        if state["status"] == "ok":
            updated = state["updated_utc"]
            return updated is None or (now - updated) > self.refresh_after

        attempted = state["attempted_utc"]
        return attempted is None or (now - attempted) > self.retry_after

    # ------------------------------------------------------------------
    # Worker
    # ------------------------------------------------------------------
    def _within_budget(self) -> bool:
        """Sliding one-hour window over outbound lookups (Rule A02)."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=1)
        while self._lookups and self._lookups[0] < cutoff:
            self._lookups.popleft()
        return len(self._lookups) < self.max_per_hour

    async def _run(self) -> None:
        try:
            while True:
                mmsi = await self._queue.get()
                try:
                    if self.is_paused:
                        logger.debug(f"Enrichment paused (breaker open); skipping MMSI {mmsi}.")
                        continue
                    if not self._within_budget():
                        self.skipped_budget_count += 1
                        logger.debug(f"Hourly enrichment budget reached; skipping MMSI {mmsi}.")
                        continue
                    self._lookups.append(datetime.now(timezone.utc))
                    await self.enrich(mmsi)
                finally:
                    self._pending.discard(mmsi)
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Particulars enricher crashed: {e}", exc_info=True)

    async def enrich(self, mmsi: int) -> Optional[Vessel]:
        """Look a vessel up and persist whatever gaps it filled.

        Returns the updated vessel, or None when VesselFinder has no record.
        Raises nothing - failures are recorded for back-off.
        """
        try:
            snapshot = await self.scraper.fetch_snapshot(mmsi)
        except Exception as e:
            self.error_count += 1
            self.db.mark_enrichment(mmsi, "error")
            self._record_failure()
            detail = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            logger.warning(f"Enrichment failed for MMSI {mmsi} ({detail}).")
            return None

        # A reachable site that simply has no record is not a failure.
        self._record_success()

        if snapshot is None or not snapshot.particulars.model_dump(exclude_none=True):
            self.not_found_count += 1
            self.db.mark_enrichment(mmsi, "not_found")
            logger.debug(f"VesselFinder has no particulars for MMSI {mmsi}.")
            return None

        vessel = self.db.get_vessel(mmsi) or Vessel(mmsi=mmsi)
        vessel.update_static(build_gap_filling_static(vessel, snapshot))
        vessel.particulars_source = self.SOURCE_NAME
        vessel.particulars_updated_utc = datetime.now(timezone.utc)

        self.db.upsert_vessel(vessel)
        self.db.mark_enrichment(mmsi, "ok", source=self.SOURCE_NAME)
        self.enriched_count += 1

        if self.on_enriched:
            try:
                self.on_enriched(mmsi, snapshot)
            except Exception as e:
                logger.debug(f"Enrichment callback failed for MMSI {mmsi}: {e}")

        logger.info(f"Enriched MMSI {mmsi} ({vessel.name}) from {self.SOURCE_NAME}.")
        return vessel
