"""Session manager coordinating data ingestion, database recording, and active sessions."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Dict, List, Optional
from datetime import datetime, timezone

from src.models.vessel import Vessel, VesselDynamic, VesselStatic
from src.models.observation import ObservationSession, ObservationRecord
from src.storage.database import DatabaseManager
from src.sources.base import DataSource
from src.recorder.exporter import SessionExporter

logger = logging.getLogger(__name__)


class RecordingManager:
    """Coordinates live ingestion from a DataSource into SQLite and manages recording sessions."""

    def __init__(
        self,
        db: DatabaseManager,
        source: DataSource,
        default_station_id: str = "Station-Desktop-01",
        export_dir: str = "data/exports",
    ):
        self.db = db
        self.source = source
        self.default_station_id = default_station_id
        self.export_dir = Path(export_dir)
        self.export_dir.mkdir(parents=True, exist_ok=True)

        self._active_session: Optional[ObservationSession] = None
        self._live_vessels: Dict[int, Vessel] = {}
        self._ingest_task: Optional[asyncio.Task] = None
        self._recent_records: List[ObservationRecord] = []
        self._max_recent = 100

        # Pre-populate live vessels from database
        for v in self.db.list_vessels():
            self._live_vessels[v.mmsi] = v

    @property
    def active_session(self) -> Optional[ObservationSession]:
        return self._active_session

    @property
    def is_recording(self) -> bool:
        return self._active_session is not None and self._active_session.is_active

    def get_live_vessels(self) -> List[Vessel]:
        """Return list of all actively tracked vessels."""
        return list(self._live_vessels.values())

    def get_recent_records(self) -> List[ObservationRecord]:
        """Return most recently recorded observation records for live dashboard feed."""
        return list(reversed(self._recent_records))

    async def start_ingestion(self) -> None:
        """Starts background task consuming from data source."""
        if self._ingest_task is None or self._ingest_task.done():
            await self.source.start()
            self._ingest_task = asyncio.create_task(self._consume_source())
            logger.info(f"Ingestion started from source '{self.source.name}'.")

    async def stop_ingestion(self) -> None:
        """Stops background data consumption."""
        if self.is_recording:
            self.stop_recording()

        await self.source.stop()
        if self._ingest_task and not self._ingest_task.done():
            self._ingest_task.cancel()
            try:
                await self._ingest_task
            except asyncio.CancelledError:
                pass
        self._ingest_task = None
        logger.info("Ingestion stopped.")

    async def switch_source(self, new_source: DataSource) -> None:
        """Dynamically switch to a different DataSource while keeping state."""
        logger.info(f"Switching active data source from '{self.source.name}' to '{new_source.name}'...")
        await self.stop_ingestion()
        self.source = new_source
        await self.start_ingestion()
        logger.info(f"Data source successfully switched to '{new_source.name}'.")

    def start_recording(
        self,
        station_id: Optional[str] = None,
        target_mmsis: Optional[List[int]] = None,
        notes: Optional[str] = None,
    ) -> ObservationSession:
        """Start a new recording session."""
        if self.is_recording:
            logger.warning("A recording session is already active. Stopping current session first.")
            self.stop_recording()

        session = ObservationSession(
            station_id=station_id or self.default_station_id,
            start_time_utc=datetime.now(timezone.utc),
            recording_mode=self.source.name,
            target_mmsis=target_mmsis or [],
            notes=notes,
        )
        self.db.create_session(session)
        self._active_session = session
        logger.info(f"Started recording session: {session.session_id} (Station: {session.station_id})")
        return session

    def stop_recording(self) -> Optional[ObservationSession]:
        """Stop and finalize current active recording session."""
        if not self._active_session:
            return None

        now_utc = datetime.now(timezone.utc)
        self._active_session.end_time_utc = now_utc
        self.db.close_session(
            self._active_session.session_id,
            now_utc,
            self._active_session.message_count,
        )
        finished_session = self._active_session
        self._active_session = None
        logger.info(
            f"Finished recording session: {finished_session.session_id} with {finished_session.message_count} records."
        )
        return finished_session

    async def _consume_source(self) -> None:
        """Internal worker reading tuples from source and writing to DB."""
        try:
            async for raw_nmea, dyn, st in self.source.stream():
                mmsi = dyn.mmsi if dyn else (st.mmsi if st else None)
                if not mmsi:
                    continue

                # Retrieve or initialize vessel model
                vessel = self._live_vessels.get(mmsi)
                if not vessel:
                    vessel = self.db.get_vessel(mmsi) or Vessel(mmsi=mmsi)
                    self._live_vessels[mmsi] = vessel

                # Apply updates
                if dyn:
                    vessel.update_dynamic(dyn)
                if st:
                    vessel.update_static(st)

                # Persist vessel state
                self.db.upsert_vessel(vessel)

                # Record observation if session is active
                if self.is_recording and self._active_session:
                    targets = self._active_session.target_mmsis
                    if not targets or mmsi in targets:
                        record = ObservationRecord(
                            session_id=self._active_session.session_id,
                            mmsi=mmsi,
                            received_at_utc=datetime.now(timezone.utc),
                            ais_timestamp_utc=dyn.timestamp if dyn else None,
                            raw_nmea=raw_nmea,
                            message_type=1 if dyn else 5,
                            ship_name=vessel.name,
                            ship_type=vessel.ship_type,
                            imo_number=vessel.imo_number,
                            call_sign=vessel.call_sign,
                            speed_knots=vessel.speed_knots,
                            course_deg=vessel.course_deg,
                            heading_deg=vessel.heading_deg,
                            draught_meters=vessel.draught_meters,
                            nav_status_code=vessel.nav_status_code,
                            nav_status_label=vessel.nav_status_label,
                            latitude=vessel.latitude,
                            longitude=vessel.longitude,
                            gross_tonnage=vessel.particulars.gross_tonnage,
                            deadweight_tonnes=vessel.particulars.deadweight_tonnes,
                            length_overall_m=vessel.particulars.length_overall_m,
                            beam_m=vessel.particulars.beam_m,
                            year_built=vessel.particulars.year_built,
                            flag=vessel.particulars.flag,
                            destination=vessel.destination,
                            eta=vessel.eta,
                            observation_window=self._active_session.window_label,
                            observer_station=self._active_session.station_id,
                        )
                        record_id = self.db.insert_record(record)
                        record.id = record_id

                        self._active_session.message_count += 1
                        self._recent_records.append(record)
                        if len(self._recent_records) > self._max_recent:
                            self._recent_records.pop(0)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Error in recording manager consumption: {e}", exc_info=True)

    def export_session(self, session_id: str, fmt: str = "csv") -> Path:
        """Export session to file in data/exports/."""
        session = self.db.get_session(session_id)
        if not session:
            raise ValueError(f"Session '{session_id}' not found.")

        records = self.db.get_records_for_session(session_id)
        out_filename = f"{session_id}.{fmt.lower()}"
        out_path = self.export_dir / out_filename

        if fmt.lower() == "csv":
            return SessionExporter.export_to_csv(session, records, out_path)
        elif fmt.lower() == "json":
            return SessionExporter.export_to_json(session, records, out_path)
        else:
            raise ValueError(f"Unsupported format: {fmt}. Use 'csv' or 'json'.")
