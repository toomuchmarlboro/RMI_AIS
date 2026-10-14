"""Application entry point for RMI AIS Vessel Tracker."""
from __future__ import annotations

import os
import sys
import asyncio
import logging
from dotenv import load_dotenv
import uvicorn

from src.storage.database import DatabaseManager
from src.sources.factory import build_source, load_yaml_config
from src.sources.simulator import SimulatorSource
from src.recorder.session import RecordingManager
from src.recorder.enricher import ParticularsEnricher
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
    return load_yaml_config()


async def run_server() -> None:
    """Initialize system components and start web dashboard."""
    cfg = load_config()

    db_path = cfg.get("storage", {}).get("database_path", "data/rmi_ais.db")
    export_dir = cfg.get("storage", {}).get("export_dir", "data/exports")
    default_station = os.getenv("DEFAULT_STATION_ID", cfg.get("app", {}).get("default_station", "Station-Desktop-01"))

    # Select DataSource (Rule A01)
    source_mode = os.getenv("AIS_DATA_SOURCE", "simulator").strip().lower()
    db = DatabaseManager(db_path=db_path)

    try:
        source = build_source(source_mode, cfg)
        logger.info(f"Initializing data source '{source.name}'...")
    except ValueError as e:
        logger.warning(f"{e} Falling back to the offline simulator.")
        source = build_source("simulator", cfg)

    if isinstance(source, SimulatorSource):
        # Pre-seed database with preset vessels
        for v in source.get_initial_vessels():
            db.upsert_vessel(v)

    # Particulars enrichment: aisstream for positions, VesselFinder for master data
    enrich_cfg = cfg.get("enrichment", {}) or {}
    enricher = ParticularsEnricher(
        db=db,
        enabled=bool(enrich_cfg.get("enabled", True)),
        scope=str(enrich_cfg.get("scope", "recording")),
        max_per_hour=int(enrich_cfg.get("max_per_hour", 60)),
        refresh_after_days=float(enrich_cfg.get("refresh_after_days", 30.0)),
        retry_after_days=float(enrich_cfg.get("retry_after_days", 7.0)),
        breaker_threshold=int(enrich_cfg.get("breaker_threshold", 5)),
        breaker_cooldown_sec=float(enrich_cfg.get("breaker_cooldown_sec", 900.0)),
    )

    # Initialize Recording Manager
    rm = RecordingManager(
        db=db,
        source=source,
        default_station_id=default_station,
        export_dir=export_dir,
        enricher=enricher,
    )
    set_recording_manager(rm)

    # Start live feed ingestion
    await enricher.start()
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
        await enricher.stop()


def main():
    """Main CLI entrypoint."""
    try:
        asyncio.run(run_server())
    except KeyboardInterrupt:
        logger.info("RMI AIS stopped by user.")


if __name__ == "__main__":
    main()
