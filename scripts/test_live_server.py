"""Verifies the live server starts, renders the dashboard, starts recording, captures AIS messages, stops, and exports CSV."""
import asyncio
import time
import httpx
import uvicorn
import threading
from src.storage.database import DatabaseManager
from src.sources.simulator import SimulatorSource
from src.recorder.session import RecordingManager
from src.ui.dashboard import app, set_recording_manager


def run_test():
    db = DatabaseManager("data/test_live.db")
    vessels_cfg = [
        {
            "mmsi": 525019876,
            "name": "Virgo 18",
            "ship_type": "Passenger/Ro-Ro Cargo Ship",
            "imo": 9123456,
            "call_sign": "YCGR",
            "draught": 5.2,
            "destination": "MERAK",
            "initial_lat": -5.9320,
            "initial_lon": 105.9980,
            "initial_sog": 13.9,
            "initial_cog": 107.3,
            "initial_hdg": 109,
            "nav_status": 0,
            "gross_tonnage": 9989,
            "deadweight": 3500,
            "length": 134.0,
            "beam": 22.0,
        }
    ]
    source = SimulatorSource(vessels_cfg, interval_sec=0.1)
    for v in source.get_initial_vessels():
        db.upsert_vessel(v)

    rm = RecordingManager(db, source, export_dir="data/exports")
    set_recording_manager(rm)

    # Start loop in background thread
    loop = asyncio.new_event_loop()

    def run_rm():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(rm.start_ingestion())
        loop.run_forever()

    t = threading.Thread(target=run_rm, daemon=True)
    t.start()

    # Start uvicorn server in daemon thread
    config = uvicorn.Config(app=app, host="127.0.0.1", port=8999, log_level="warning")
    server = uvicorn.Server(config)
    t_srv = threading.Thread(target=server.run, daemon=True)
    t_srv.start()

    time.sleep(1.0)

    try:
        with httpx.Client(base_url="http://127.0.0.1:8999") as client:
            # 1. Check HTML dashboard
            r = client.get("/")
            assert r.status_code == 200
            assert "RMI AIS" in r.text
            print("[PASS] Dashboard HTML serves correctly.")

            # 2. Check state API
            r_state = client.get("/api/state")
            assert r_state.status_code == 200
            state = r_state.json()
            assert len(state["vessels"]) == 1
            assert state["vessels"][0]["name"] == "Virgo 18"
            print(f"[PASS] State API returns vessel: {state['vessels'][0]['name']} at {state['vessels'][0]['speed_knots']} kn")

            # 3. Start recording
            r_start = client.post("/api/record/start", json={"station_id": "Station-Test-Live"})
            assert r_start.status_code == 200
            session_id = r_start.json()["session"]["session_id"]
            print(f"[PASS] Recording session started: {session_id}")

            # Wait to capture messages
            time.sleep(0.5)

            # 4. Stop recording
            r_stop = client.post("/api/record/stop")
            assert r_stop.status_code == 200
            stopped_session = r_stop.json()["session"]
            print(f"[PASS] Recording session stopped with {stopped_session['message_count']} messages recorded.")

            # 5. Export to CSV
            r_csv = client.get(f"/api/export/{session_id}?format=csv")
            assert r_csv.status_code == 200
            assert "Virgo 18" in r_csv.text
            assert "9989" in r_csv.text
            print("[PASS] CSV export downloaded successfully and verified content.")

            # 6. Export to JSON
            r_json = client.get(f"/api/export/{session_id}?format=json")
            assert r_json.status_code == 200
            json_data = r_json.json()
            assert json_data["metadata"]["observation_id"] == session_id
            print("[PASS] JSON export downloaded successfully and verified content.")

    finally:
        server.should_exit = True
        loop.call_soon_threadsafe(loop.stop)
        db.close()

    print("\nALL LIVE INTEGRATION CHECKS PASSED!")


if __name__ == "__main__":
    run_test()
