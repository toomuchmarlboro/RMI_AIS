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
from src.sources.factory import SUPPORTED_MODES, build_source, load_yaml_config
from src.sources.vesselfinder import POSITION_UNCERTAINTY_DEG

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
        "enrichment": manager.enricher.stats if manager.enricher else None,
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
    vessel.particulars_source = "manual"
    manager.db.upsert_vessel(vessel)
    # Manual entry wins: don't let the background enricher overwrite it.
    manager.db.mark_enrichment(mmsi, "ok", source="manual")
    manager._live_vessels[mmsi] = vessel

    return {"status": "updated", "vessel": vessel.model_dump()}


class SwitchSourceRequest(BaseModel):
    mode: str  # 'simulator', 'aisstream' or 'vesselfinder'
    api_key: Optional[str] = None
    bounding_boxes: Optional[List] = None
    target_mmsis: Optional[List[int]] = None


def _update_env_file(key: str, value: str) -> None:
    """Persist settings to .env file."""
    env_path = Path(".env")
    lines = []
    found = False
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[key] = value


@app.post("/api/source/switch")
async def switch_data_source(req: SwitchSourceRequest):
    """Switch the live data source between Simulator, aisstream.io and VesselFinder."""
    if not manager:
        raise HTTPException(status_code=503, detail="Recording manager uninitialized")

    mode = req.mode.strip().lower()
    if mode not in SUPPORTED_MODES:
        raise HTTPException(status_code=400, detail=f"Unsupported mode: {req.mode}")

    cfg = load_yaml_config()
    try:
        new_source = build_source(
            mode,
            cfg,
            api_key=req.api_key,
            bounding_boxes=req.bounding_boxes,
            target_mmsis=req.target_mmsis,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    _update_env_file("AIS_DATA_SOURCE", mode)
    if mode == "aisstream" and req.api_key:
        _update_env_file("AISSTREAM_API_KEY", req.api_key.strip())
    if mode == "vesselfinder":
        _update_env_file("VESSELFINDER_TARGET_MMSIS", ",".join(str(m) for m in new_source.target_mmsis))

    await manager.switch_source(new_source)

    result = {"status": "switched", "source": mode}
    if mode == "vesselfinder":
        result["target_mmsis"] = new_source.target_mmsis
        result["position_precision"] = (
            "VesselFinder's public pages round coordinates to whole degrees "
            f"(+/-{POSITION_UNCERTAINTY_DEG} deg). Positions recorded from this source are "
            "approximate and tagged POS:APPROX."
        )
    return result


@app.post("/api/vessels/{mmsi}/enrich")
async def enrich_vessel_from_vesselfinder(mmsi: int):
    """Fill in a vessel's particulars from its public VesselFinder page, now.

    Complements manual entry (Rule D08) and the background enricher. Bypasses
    the scope filter and freshness check - an explicit request is always
    honoured - but shares the enricher's HTTP client, cache and bookkeeping.
    """
    if not manager:
        raise HTTPException(status_code=503, detail="Recording manager uninitialized")

    enricher = manager.enricher
    if enricher is None:
        raise HTTPException(status_code=503, detail="Particulars enrichment is not configured")

    vessel = await enricher.enrich(mmsi)
    if vessel is None:
        # Distinguish "VesselFinder doesn't know this ship" from "we couldn't reach it".
        state = manager.db.get_enrichment_state(mmsi) or {}
        if state.get("status") == "error":
            raise HTTPException(
                status_code=502,
                detail=f"VesselFinder lookup for MMSI {mmsi} failed; see server log. Retry shortly.",
            )
        raise HTTPException(status_code=404, detail=f"VesselFinder has no particulars for MMSI {mmsi}")

    manager._live_vessels[mmsi] = vessel
    return {
        "status": "enriched",
        "source": enricher.SOURCE_NAME,
        "particulars": vessel.particulars.model_dump(exclude_none=True),
        "vessel": vessel.model_dump(),
    }

