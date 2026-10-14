"""Unit tests for the hybrid particulars enricher.

Covers the architecture decision: aisstream.io supplies positions, VesselFinder
supplies master data, and AIS stays authoritative for anything it transmits.
All lookups are stubbed - no network access.
"""
import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from src.models.vessel import Vessel, VesselParticulars
from src.recorder.enricher import (
    EnrichmentScope,
    ParticularsEnricher,
    build_gap_filling_static,
)
from src.sources.vesselfinder import VesselFinderScraper, VesselFinderSnapshot
from src.storage.database import DatabaseManager


def _snapshot(mmsi=636092932, **overrides):
    defaults = dict(
        mmsi=mmsi,
        name="ILONA",
        imo_number=9225641,
        call_sign="D5VK6",
        ship_type="Container Ship",
        destination="CNTAO>MXLZC",
        eta="10-25 00:00",
        draught_meters=12.8,
        particulars=VesselParticulars(
            gross_tonnage=75590,
            deadweight_tonnes=85890,
            length_overall_m=300.0,
            beam_m=40.0,
            year_built=2001,
            flag="Liberia",
        ),
    )
    defaults.update(overrides)
    return VesselFinderSnapshot(**defaults)


class StubScraper(VesselFinderScraper):
    """Returns canned snapshots and counts lookups."""

    def __init__(self, snapshots=None, raises=False):
        super().__init__(min_request_interval_sec=0.0)
        self.snapshots = snapshots if snapshots is not None else {}
        self.raises = raises
        self.lookups = []

    async def fetch_snapshot(self, mmsi, enrich_details=True):
        self.lookups.append(mmsi)
        if self.raises:
            raise RuntimeError("boom")
        return self.snapshots.get(mmsi)

    async def aclose(self):
        return None


@pytest.fixture
def db(tmp_path):
    """A throwaway database, closed so Windows can release the file."""
    manager = DatabaseManager(str(tmp_path / "enrich.db"))
    yield manager
    manager.close()


def _drain(enricher, mmsis):
    """Run the worker until the submitted queue is empty."""

    async def _run():
        await enricher.start()
        for mmsi in mmsis:
            enricher.submit(mmsi, is_recording=True)
        await enricher._queue.join()
        await enricher.stop()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Gap-filling policy
# ---------------------------------------------------------------------------


def test_particulars_fill_in_and_override_ais_dimensions():
    """VesselFinder's registered tonnage/dimensions supersede AIS offsets."""
    vessel = Vessel(mmsi=636092932, name="ILONA")
    vessel.particulars.length_overall_m = 297.0  # derived from AIS dimensions

    vessel.update_static(build_gap_filling_static(vessel, _snapshot()))

    assert vessel.particulars.gross_tonnage == 75590
    assert vessel.particulars.deadweight_tonnes == 85890
    assert vessel.particulars.year_built == 2001
    assert vessel.particulars.flag == "Liberia"
    assert vessel.particulars.length_overall_m == 300.0


def test_voyage_data_is_never_taken_from_vesselfinder():
    """AIS is authoritative for voyage/dynamic fields; VF's copy may be stale."""
    vessel = Vessel(mmsi=636092932, name="ILONA")
    vessel.destination = "SGSIN"
    vessel.eta = "10-20 08:00"
    vessel.draught_meters = 11.4

    vessel.update_static(build_gap_filling_static(vessel, _snapshot()))

    assert vessel.destination == "SGSIN"
    assert vessel.eta == "10-20 08:00"
    assert vessel.draught_meters == 11.4


def test_identity_fields_fill_only_when_missing():
    vessel = Vessel(mmsi=636092932, name="REAL AIS NAME", call_sign="XXXX")
    vessel.update_static(build_gap_filling_static(vessel, _snapshot()))

    assert vessel.name == "REAL AIS NAME"
    assert vessel.call_sign == "XXXX"


@pytest.mark.parametrize("placeholder", ["Unknown Vessel", "Vessel-636092932", "636092932", ""])
def test_placeholder_names_are_replaced(placeholder):
    vessel = Vessel(mmsi=636092932, name=placeholder)
    vessel.update_static(build_gap_filling_static(vessel, _snapshot()))
    assert vessel.name == "ILONA"


# ---------------------------------------------------------------------------
# Scope filtering
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "scope,is_recording,is_target,expected",
    [
        ("all", False, False, True),
        ("recording", False, False, False),
        ("recording", True, False, True),
        ("watchlist", True, False, False),
        ("watchlist", True, True, True),
    ],
)
def test_scope_controls_eligibility(db, scope, is_recording, is_target, expected):
    enricher = ParticularsEnricher(db=db, scope=scope, scraper=StubScraper())
    assert enricher.is_eligible(is_recording=is_recording, is_target=is_target) is expected


def test_unknown_scope_falls_back_to_recording(db):
    enricher = ParticularsEnricher(db=db, scope="nonsense", scraper=StubScraper())
    assert enricher.scope is EnrichmentScope.RECORDING


def test_submit_respects_scope_and_dedupes(db):
    enricher = ParticularsEnricher(db=db, scope="recording", scraper=StubScraper())

    assert enricher.submit(636092932, is_recording=False) is False
    assert enricher.submit(636092932, is_recording=True) is True
    # Already queued
    assert enricher.submit(636092932, is_recording=True) is False


def test_submit_is_a_noop_when_disabled(db):
    enricher = ParticularsEnricher(db=db, enabled=False, scope="all", scraper=StubScraper())
    assert enricher.submit(636092932, is_recording=True) is False


# ---------------------------------------------------------------------------
# Persistence, freshness and back-off
# ---------------------------------------------------------------------------


def test_enrich_persists_particulars_and_provenance(db):
    enricher = ParticularsEnricher(db=db, scraper=StubScraper({636092932: _snapshot()}))

    vessel = asyncio.run(enricher.enrich(636092932))
    assert vessel is not None

    stored = db.get_vessel(636092932)
    assert stored.particulars.gross_tonnage == 75590
    assert stored.particulars_source == "vesselfinder"
    assert stored.particulars_updated_utc is not None

    state = db.get_enrichment_state(636092932)
    assert state["status"] == "ok"
    assert db.count_enriched_vessels() == 1


def test_enriched_vessels_are_not_looked_up_again(db):
    scraper = StubScraper({636092932: _snapshot()})
    enricher = ParticularsEnricher(db=db, scope="all", scraper=scraper)

    asyncio.run(enricher.enrich(636092932))
    # A fresh success blocks re-submission (Rule A03)
    assert enricher.submit(636092932, is_recording=True) is False
    assert len(scraper.lookups) == 1


def test_stale_particulars_are_refreshed(db):
    enricher = ParticularsEnricher(
        db=db, scope="all", refresh_after_days=30.0, scraper=StubScraper({636092932: _snapshot()})
    )
    long_ago = datetime.now(timezone.utc) - timedelta(days=45)
    db.mark_enrichment(636092932, "ok", source="vesselfinder", attempted_at=long_ago)

    assert enricher.submit(636092932, is_recording=True) is True


def test_not_found_vessels_back_off_then_retry(db):
    scraper = StubScraper({})  # VesselFinder knows nothing
    enricher = ParticularsEnricher(db=db, scope="all", retry_after_days=7.0, scraper=scraper)

    assert asyncio.run(enricher.enrich(525019876)) is None
    assert db.get_enrichment_state(525019876)["status"] == "not_found"
    # Backed off
    assert enricher.submit(525019876, is_recording=True) is False

    # ...until the retry window passes
    db.mark_enrichment(
        525019876, "not_found", attempted_at=datetime.now(timezone.utc) - timedelta(days=10)
    )
    assert enricher.submit(525019876, is_recording=True) is True


def test_snapshot_without_particulars_counts_as_not_found(db):
    """A record with no master data is not worth storing as a success."""
    bare = _snapshot(525019876, name=None, imo_number=None, particulars=VesselParticulars())
    enricher = ParticularsEnricher(db=db, scope="all", scraper=StubScraper({525019876: bare}))

    assert asyncio.run(enricher.enrich(525019876)) is None
    assert db.get_enrichment_state(525019876)["status"] == "not_found"


def test_lookup_errors_are_recorded_not_raised(db):
    enricher = ParticularsEnricher(db=db, scope="all", scraper=StubScraper(raises=True))

    assert asyncio.run(enricher.enrich(636092932)) is None
    assert db.get_enrichment_state(636092932)["status"] == "error"
    assert enricher.error_count == 1


def test_failed_lookup_does_not_look_like_fresh_data(db):
    db.mark_enrichment(636092932, "error")
    state = db.get_enrichment_state(636092932)

    assert state["attempted_utc"] is not None
    assert state["updated_utc"] is None  # success timestamp untouched


# ---------------------------------------------------------------------------
# Budget
# ---------------------------------------------------------------------------


def test_hourly_budget_caps_outbound_lookups(db):
    snapshots = {m: _snapshot(m) for m in range(636092930, 636092940)}
    scraper = StubScraper(snapshots)
    enricher = ParticularsEnricher(db=db, scope="all", max_per_hour=3, scraper=scraper)

    _drain(enricher, snapshots)

    assert len(scraper.lookups) == 3
    assert enricher.skipped_budget_count == len(snapshots) - 3


def test_worker_drains_the_queue(db):
    snapshots = {636092932: _snapshot(636092932), 232003233: _snapshot(232003233, name="MERCIA")}
    scraper = StubScraper(snapshots)
    enricher = ParticularsEnricher(db=db, scope="all", scraper=scraper)

    _drain(enricher, snapshots)

    assert sorted(scraper.lookups) == sorted(snapshots)
    assert enricher.enriched_count == 2
    assert db.count_enriched_vessels() == 2


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def test_breaker_opens_after_consecutive_failures(db):
    """vesselfinder.com refuses persistent clients; stop calling it."""
    scraper = StubScraper(raises=True)
    enricher = ParticularsEnricher(db=db, scope="all", breaker_threshold=3, scraper=scraper)

    async def _run():
        for mmsi in (111111111, 222222222, 333333333):
            await enricher.enrich(mmsi)

    asyncio.run(_run())

    assert enricher.is_paused is True
    assert enricher.stats["paused"] is True


def test_paused_breaker_stops_the_worker_calling_out(db):
    scraper = StubScraper(raises=True)
    enricher = ParticularsEnricher(db=db, scope="all", breaker_threshold=2, scraper=scraper)

    _drain(enricher, range(111111111, 111111121))

    # Two failures tripped the breaker; the remaining eight were skipped
    assert len(scraper.lookups) == 2


def test_breaker_resets_after_cooldown(db):
    scraper = StubScraper(raises=True)
    enricher = ParticularsEnricher(
        db=db, scope="all", breaker_threshold=1, breaker_cooldown_sec=0.0, scraper=scraper
    )

    asyncio.run(enricher.enrich(111111111))
    # A zero cooldown has already elapsed
    assert enricher.is_paused is False


def test_a_successful_lookup_closes_the_breaker(db):
    scraper = StubScraper({636092932: _snapshot()}, raises=False)
    enricher = ParticularsEnricher(db=db, scope="all", breaker_threshold=3, scraper=scraper)

    scraper.raises = True
    asyncio.run(enricher.enrich(111111111))
    asyncio.run(enricher.enrich(222222222))
    assert enricher._consecutive_errors == 2

    scraper.raises = False
    asyncio.run(enricher.enrich(636092932))
    assert enricher._consecutive_errors == 0
    assert enricher.is_paused is False


def test_not_found_does_not_trip_the_breaker(db):
    """A reachable site with no record is not a transport failure."""
    enricher = ParticularsEnricher(db=db, scope="all", breaker_threshold=2, scraper=StubScraper({}))

    async def _run():
        await enricher.enrich(111111111)
        await enricher.enrich(222222222)
        await enricher.enrich(333333333)

    asyncio.run(_run())

    assert enricher.not_found_count == 3
    assert enricher.is_paused is False


# ---------------------------------------------------------------------------
# Propagation into the live vessel registry
# ---------------------------------------------------------------------------


def test_enrich_notifies_the_live_registry(db):
    enricher = ParticularsEnricher(db=db, scope="all", scraper=StubScraper({636092932: _snapshot()}))
    seen = []
    enricher.on_enriched = lambda mmsi, snap: seen.append((mmsi, snap.name))

    asyncio.run(enricher.enrich(636092932))
    assert seen == [(636092932, "ILONA")]


def test_a_failing_callback_does_not_break_enrichment(db):
    enricher = ParticularsEnricher(db=db, scope="all", scraper=StubScraper({636092932: _snapshot()}))

    def boom(mmsi, snap):
        raise RuntimeError("registry exploded")

    enricher.on_enriched = boom

    assert asyncio.run(enricher.enrich(636092932)) is not None
    assert db.get_enrichment_state(636092932)["status"] == "ok"


def test_recording_manager_applies_enrichment_without_losing_position(db):
    """The enricher works from a DB copy; the live fix must survive."""
    from src.recorder.session import RecordingManager
    from src.sources.simulator import SimulatorSource

    source = SimulatorSource([], interval_sec=1.0)
    enricher = ParticularsEnricher(db=db, scope="all", scraper=StubScraper({636092932: _snapshot()}))
    rm = RecordingManager(db=db, source=source, export_dir=str(db.db_path.parent / "exports"), enricher=enricher)

    # A live vessel with a fresh position but no master data
    live = Vessel(mmsi=636092932, name="ILONA", latitude=1.5, longitude=104.25, speed_knots=12.0)
    rm._live_vessels[636092932] = live

    asyncio.run(enricher.enrich(636092932))

    assert live.particulars.gross_tonnage == 75590
    assert live.particulars.flag == "Liberia"
    assert live.particulars_source == "vesselfinder"
    # Position untouched by enrichment
    assert (live.latitude, live.longitude, live.speed_knots) == (1.5, 104.25, 12.0)


def test_enrichment_is_skipped_when_vesselfinder_is_the_position_source(db):
    """No point scraping particulars from the feed that already supplies them."""
    from src.recorder.session import RecordingManager
    from src.sources.vesselfinder import VesselFinderSource

    enricher = ParticularsEnricher(db=db, scope="all", scraper=StubScraper({636092932: _snapshot()}))
    rm = RecordingManager(
        db=db,
        source=VesselFinderSource(target_mmsis=[636092932]),
        export_dir=str(db.db_path.parent / "exports"),
        enricher=enricher,
    )
    rm.start_recording(target_mmsis=[636092932])

    assert enricher._queue.qsize() == 0


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

LEGACY_SCHEMA = """
    CREATE TABLE vessels (
        mmsi INTEGER PRIMARY KEY, name TEXT, ship_type TEXT,
        imo_number INTEGER, call_sign TEXT, gross_tonnage INTEGER,
        deadweight_tonnes INTEGER, length_overall_m REAL, beam_m REAL,
        year_built INTEGER, flag TEXT, draught_meters REAL,
        destination TEXT, eta TEXT, latest_lat REAL, latest_lon REAL,
        latest_sog REAL, latest_cog REAL, latest_hdg REAL,
        nav_status_code INTEGER, nav_status_label TEXT,
        last_seen_utc TEXT, updated_at_utc TEXT
    );
    INSERT INTO vessels (mmsi, name) VALUES (636092932, 'ILONA');
"""


def test_migration_adds_columns_to_a_preexisting_database(tmp_path):
    """An existing data/rmi_ais.db must gain the columns without losing rows."""
    path = tmp_path / "legacy.db"

    conn = sqlite3.connect(str(path))
    conn.executescript(LEGACY_SCHEMA)
    conn.commit()
    conn.close()

    db = DatabaseManager(str(path))
    try:
        # Pre-existing row survived and the new bookkeeping works
        assert db.get_vessel(636092932).name == "ILONA"
        assert db.get_enrichment_state(636092932) == {
            "source": None,
            "updated_utc": None,
            "attempted_utc": None,
            "status": None,
        }
        db.mark_enrichment(636092932, "ok", source="vesselfinder")
        assert db.get_enrichment_state(636092932)["status"] == "ok"
    finally:
        db.close()

    # Re-opening an already-migrated database is idempotent
    again = DatabaseManager(str(path))
    try:
        assert again.count_enriched_vessels() == 1
    finally:
        again.close()


def test_enrichment_can_precede_the_first_position_report(db):
    db.mark_enrichment(111222333, "ok", source="vesselfinder")
    assert db.get_enrichment_state(111222333)["status"] == "ok"
