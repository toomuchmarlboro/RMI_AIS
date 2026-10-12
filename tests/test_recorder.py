"""Unit tests for RecordingManager and SessionExporter."""
import csv
import json
import asyncio
import tempfile
from pathlib import Path

from src.storage.database import DatabaseManager
from src.sources.simulator import SimulatorSource
from src.recorder.session import RecordingManager
from src.recorder.exporter import SessionExporter


def test_recording_and_export():
    async def _async_test():
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            export_dir = Path(tmpdir) / "exports"

            db = DatabaseManager(str(db_path))
            try:
                vessels_cfg = [
                    {
                        "mmsi": 525019876,
                        "name": "Virgo 18",
                        "ship_type": "Passenger/Ro-Ro Cargo Ship",
                        "initial_lat": -5.9320,
                        "initial_lon": 105.9980,
                        "initial_sog": 13.9,
                        "initial_cog": 107.3,
                        "initial_hdg": 109,
                        "nav_status": 0,
                        "gross_tonnage": 9989,
                        "length": 134.0,
                        "beam": 22.0,
                    }
                ]
                source = SimulatorSource(vessels_cfg, interval_sec=0.05)
                # Pre-seed database with preset vessels (as in main.py)
                for init_v in source.get_initial_vessels():
                    db.upsert_vessel(init_v)

                rm = RecordingManager(db, source, export_dir=str(export_dir))

                # Start ingestion
                await rm.start_ingestion()

                # Start recording session
                sess = rm.start_recording(station_id="Station-Merak-01")
                assert rm.is_recording
                assert rm.active_session.station_id == "Station-Merak-01"

                # Allow a few messages to be recorded
                await asyncio.sleep(0.3)

                # Stop recording
                finished_sess = rm.stop_recording()
                assert not rm.is_recording
                assert finished_sess.message_count > 0

                # Stop ingestion
                await rm.stop_ingestion()

                # Export to CSV
                csv_file = rm.export_session(finished_sess.session_id, fmt="csv")
                assert csv_file.exists()
                with open(csv_file, mode="r", encoding="utf-8") as f:
                    reader = csv.DictReader(f)
                    rows = list(reader)
                    assert len(rows) == finished_sess.message_count
                    assert rows[0]["Ship Name"] == "Virgo 18"
                    assert rows[0]["Gross Tonnage (GT)"] == "9989"
                    assert rows[0]["Length (m)"] == "134.0"
                    assert rows[0]["Beam (m)"] == "22.0"

                # Export to JSON
                json_file = rm.export_session(finished_sess.session_id, fmt="json")
                assert json_file.exists()
                with open(json_file, mode="r", encoding="utf-8") as f:
                    data = json.load(f)
                    assert data["metadata"]["observation_id"] == finished_sess.session_id
                    assert len(data["records"]) == finished_sess.message_count
                    assert data["records"][0]["vessel"]["particulars"]["gross_tonnage_gt"] == 9989
            finally:
                db.close()

    asyncio.run(_async_test())
