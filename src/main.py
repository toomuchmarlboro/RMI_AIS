"""Application entry point for RMI AIS Vessel Tracker."""
from __future__ import annotations

import os
import sys
import yaml
import asyncio
import logging
from pathlib import Path
from dotenv import load_dotenv
import uvicorn

from src.storage.database import DatabaseManager
from src.sources.simulator import SimulatorSource
from src.sources.aisstream import AisStreamSource
from src.recorder.session import RecordingManager
from src.ui.dashboard import app, set_recording_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("RMI_AIS")


def load_config() -> dict:
    """Load configuration from config.yaml and environment."""
    load_dotenv()
    config_file = Path("config.yaml")
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    return {}


async def run_server() -> None:
    """Initialize system components and start web dashboard."""
    cfg = load_config()

    db_path = cfg.get("storage", {}).get("database_path", "data/rmi_ais.db")
    export_dir = cfg.get("storage", {}).get("export_dir", "data/exports")
    default_station = os.getenv("DEFAULT_STATION_ID", cfg.get("app", {}).get("default_station", "Station-Desktop-01"))

    # Select DataSource
    source_mode = os.getenv("AIS_DATA_SOURCE", "simulator").lower()
    aisstream_key = os.getenv("AISSTREAM_API_KEY", "")

    db = DatabaseManager(db_path=db_path)

    if source_mode == "aisstream" and aisstream_key:
        logger.info("Initializing AisStreamSource (Real-Time Global WebSocket)...")
        boxes = cfg.get("aisstream", {}).get("bounding_boxes", [])
        source = AisStreamSource(api_key=aisstream_key, bounding_boxes=boxes)
    else:
        logger.info("Initializing SimulatorSource (Realistic MarineTraffic/VesselFinder mock)...")
        vessels_cfg = cfg.get("simulator", {}).get("vessels", [])
        interval = float(cfg.get("simulator", {}).get("update_interval_sec", 3.0))
        source = SimulatorSource(vessels_config=vessels_cfg, interval_sec=interval)

        # Pre-seed database with preset vessels
        for v in source.get_initial_vessels():
            db.upsert_vessel(v)

    # Initialize Recording Manager
    rm = RecordingManager(
        db=db,
        source=source,
        default_station_id=default_station,
        export_dir=export_dir,
    )
    set_recording_manager(rm)

    # Start live feed ingestion
    await rm.start_ingestion()

    # Launch Web Dashboard
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    logger.info(f"Dashboard available at: http://{host}:{port}")

    config = uvicorn.Config(app=app, host=host, port=port, log_level="info", loop="asyncio")
    server = uvicorn.Server(config)

    try:
        await server.serve()
    finally:
        logger.info("Shutting down AIS Ingestion...")
        await rm.stop_ingestion()


def main():
    """Main CLI entrypoint."""
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("RMI AIS stopped by user.")


if __name__ == "__main__":
    main()
