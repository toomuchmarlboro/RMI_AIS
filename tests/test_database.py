"""Unit tests for SQLite database manager."""
import tempfile
from pathlib import Path
from datetime import datetime, timezone
from src.storage.database import DatabaseManager
from src.models.vessel import Vessel, VesselParticulars
from src.models.observation import ObservationSession, ObservationRecord


def test_database_crud():
    with tempfile.TemporaryDirectory() as tmpdir:
        db_file = Path(tmpdir) / "test_ais.db"
        db = DatabaseManager(str(db_file))

        # 1. Upsert and fetch vessel
        v = Vessel(
            mmsi=525019876,
            name="Virgo 18",
            ship_type="Passenger/Ro-Ro Cargo Ship",
            imo_number=9123456,
            call_sign="YCGR",
            latitude=-5.9320,
            longitude=105.9980,
            speed_knots=13.9,
            course_deg=107.3,
            heading_deg=109.0,
            nav_status_code=0,
            nav_status_label="Under way using engine",
            draught_meters=5.2,
            destination="MERAK",
            particulars=VesselParticulars(
                gross_tonnage=9989,
                deadweight_tonnes=3500,
                length_overall_m=134.0,
                beam_m=22.0,
                year_built=2015,
                flag="Indonesia",
            ),
        )
        db.upsert_vessel(v)

        fetched = db.get_vessel(525019876)
        assert fetched is not None
        assert fetched.name == "Virgo 18"
        assert fetched.speed_knots == 13.9
        assert fetched.particulars.gross_tonnage == 9989
        assert fetched.particulars.length_overall_m == 134.0

        # 2. Create and close session
        sess = ObservationSession(
            session_id="OBS-TEST-001",
            station_id="Station-Desktop-01",
            start_time_utc=datetime.now(timezone.utc),
            recording_mode="simulation",
        )
        db.create_session(sess)

        fetched_sess = db.get_session("OBS-TEST-001")
        assert fetched_sess is not None
        assert fetched_sess.is_active

        # 3. Insert observation record
        rec = ObservationRecord(
            session_id="OBS-TEST-001",
            mmsi=525019876,
            received_at_utc=datetime.now(timezone.utc),
            speed_knots=13.9,
            course_deg=107.3,
            nav_status_code=0,
            nav_status_label="Under way using engine",
            latitude=-5.9320,
            longitude=105.9980,
            gross_tonnage=9989,
        )
        rec_id = db.insert_record(rec)
        assert rec_id > 0

        # Verify session message count updated
        updated_sess = db.get_session("OBS-TEST-001")
        assert updated_sess.message_count == 1

        records = db.get_records_for_session("OBS-TEST-001")
        assert len(records) == 1
        assert records[0].mmsi == 525019876
        assert records[0].gross_tonnage == 9989
        db.close()
