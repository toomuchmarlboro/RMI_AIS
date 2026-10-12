"""FastAPI Web Dashboard and REST Endpoints for RMI AIS Vessel Tracker."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from src.recorder.session import RecordingManager
from src.models.vessel import VesselParticulars

app = FastAPI(title="RMI AIS Vessel Tracker", version="1.0.0")

# Reference to active manager
manager: Optional[RecordingManager] = None
TEMPLATES_DIR = Path(__file__).parent / "templates"


def set_recording_manager(rm: RecordingManager) -> None:
    """Set the singleton recording manager instance."""
    global manager
    manager = rm


class StartRecordRequest(BaseModel):
    station_id: Optional[str] = "Station-Desktop-01"
    target_mmsis: Optional[List[int]] = None
    notes: Optional[str] = None


@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    """Serve the primary interactive dashboard."""
    template_file = TEMPLATES_DIR / "index.html"
    if not template_file.exists():
        raise HTTPException(status_code=404, detail="Dashboard template missing")
    return HTMLResponse(content=template_file.read_text(encoding="utf-8"))


@app.get("/api/state")
async def get_dashboard_state():
    """Retrieve full live telemetry state, tracked fleet, and recording status."""
    if not manager:
        raise HTTPException(status_code=503, detail="Recording manager uninitialized")

    vessels = manager.get_live_vessels()
    recent = manager.get_recent_records()
    sessions = manager.db.list_sessions()

    return {
        "source_mode": manager.source.name,
        "is_recording": manager.is_recording,
        "active_session": manager.active_session.model_dump() if manager.active_session else None,
        "vessels": [v.model_dump() for v in vessels],
        "recent_records": [r.model_dump() for r in recent],
        "sessions": [s.model_dump() for s in sessions],
    }


@app.post("/api/record/start")
async def start_recording(req: StartRecordRequest):
    """Start an AIS observation recording session."""
    if not manager:
        raise HTTPException(status_code=503, detail="Recording manager uninitialized")

    session = manager.start_recording(
        station_id=req.station_id,
        target_mmsis=req.target_mmsis,
        notes=req.notes,
    )
    return {"status": "started", "session": session.model_dump()}


@app.post("/api/record/stop")
async def stop_recording():
    """Stop active recording session and finalize window timestamp."""
    if not manager:
        raise HTTPException(status_code=503, detail="Recording manager uninitialized")

    session = manager.stop_recording()
    if not session:
        return {"status": "no_active_session"}
    return {"status": "stopped", "session": session.model_dump()}


@app.get("/api/export/{session_id}")
async def export_session(session_id: str, format: str = Query("csv", pattern="^(csv|json)$")):
    """Export and download recorded session in CSV or JSON format."""
    if not manager:
        raise HTTPException(status_code=503, detail="Recording manager uninitialized")

    try:
        path = manager.export_session(session_id, fmt=format)
        media_type = "text/csv" if format.lower() == "csv" else "application/json"
        return FileResponse(
            path=str(path),
            filename=path.name,
            media_type=media_type,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/api/vessels/{mmsi}/particulars")
async def update_vessel_particulars(mmsi: int, particulars: VesselParticulars):
    """Manual entry or enrichment of vessel master particulars (Rule D08)."""
    if not manager:
        raise HTTPException(status_code=503, detail="Recording manager uninitialized")

    vessel = manager.db.get_vessel(mmsi)
    if not vessel:
        raise HTTPException(status_code=404, detail=f"Vessel with MMSI {mmsi} not found")

    vessel.particulars = particulars
    manager.db.upsert_vessel(vessel)
    if mmsi in manager._live_vessels:
        manager._live_vessels[mmsi].particulars = particulars

    return {"status": "updated", "vessel": vessel.model_dump()}
