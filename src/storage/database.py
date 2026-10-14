"""SQLite storage manager for vessel particulars, sessions, and observations."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timezone

from src.models.vessel import Vessel, VesselParticulars
from src.models.observation import ObservationSession, ObservationRecord


def _parse_utc(value: Optional[str]) -> Optional[datetime]:
    """Parse a stored ISO-8601 timestamp, tolerating legacy/bad values."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None


class DatabaseManager:
    """Manages SQLite storage with thread safety and WAL mode."""

    def __init__(self, db_path: str = "data/rmi_ais.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Returns a thread-local SQLite connection configured with WAL and row factory."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                str(self.db_path),
                timeout=30.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            self._local.conn = conn
        return self._local.conn

    def close(self) -> None:
        """Close the thread-local SQLite connection if open."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _init_schema(self) -> None:
        """Initialize database tables and indexes."""
        conn = self._get_connection()
        with conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS vessels (
                    mmsi INTEGER PRIMARY KEY,
                    name TEXT,
                    ship_type TEXT,
                    imo_number INTEGER,
                    call_sign TEXT,
                    gross_tonnage INTEGER,
                    deadweight_tonnes INTEGER,
                    length_overall_m REAL,
                    beam_m REAL,
                    year_built INTEGER,
                    flag TEXT,
                    draught_meters REAL,
                    destination TEXT,
                    eta TEXT,
                    latest_lat REAL,
                    latest_lon REAL,
                    latest_sog REAL,
                    latest_cog REAL,
                    latest_hdg REAL,
                    nav_status_code INTEGER,
                    nav_status_label TEXT,
                    last_seen_utc TEXT,
                    updated_at_utc TEXT
                );

                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    station_id TEXT NOT NULL,
                    start_time_utc TEXT NOT NULL,
                    end_time_utc TEXT,
                    recording_mode TEXT NOT NULL,
                    target_mmsis TEXT,
                    message_count INTEGER DEFAULT 0,
                    notes TEXT
                );

                CREATE TABLE IF NOT EXISTS observation_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    mmsi INTEGER NOT NULL,
                    received_at_utc TEXT NOT NULL,
                    ais_timestamp_utc TEXT,
                    raw_nmea TEXT,
                    message_type INTEGER,
                    ship_name TEXT,
                    ship_type TEXT,
                    imo_number INTEGER,
                    call_sign TEXT,
                    speed_knots REAL,
                    course_deg REAL,
                    heading_deg REAL,
                    draught_meters REAL,
                    nav_status_code INTEGER,
                    nav_status_label TEXT,
                    latitude REAL,
                    longitude REAL,
                    gross_tonnage INTEGER,
                    deadweight_tonnes INTEGER,
                    length_overall_m REAL,
                    beam_m REAL,
                    year_built INTEGER,
                    flag TEXT,
                    destination TEXT,
                    eta TEXT,
                    observation_window TEXT,
                    observer_station TEXT,
                    FOREIGN KEY(session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_obs_session_id ON observation_records(session_id);
                CREATE INDEX IF NOT EXISTS idx_obs_mmsi ON observation_records(mmsi);
                CREATE INDEX IF NOT EXISTS idx_obs_received_at ON observation_records(received_at_utc);
                """
            )
        self._migrate_schema()

    #: Columns added after the initial schema, applied in place on existing databases.
    _ADDED_VESSEL_COLUMNS = (
        ("particulars_source", "TEXT"),
        ("particulars_updated_utc", "TEXT"),
        ("enrichment_attempted_utc", "TEXT"),
        ("enrichment_status", "TEXT"),
    )

    def _migrate_schema(self) -> None:
        """Add columns introduced after the first release, without losing data."""
        conn = self._get_connection()
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(vessels)")}

        with self._lock, conn:
            for column, col_type in self._ADDED_VESSEL_COLUMNS:
                if column not in existing:
                    conn.execute(f"ALTER TABLE vessels ADD COLUMN {column} {col_type}")

    # -------------------------------------------------------------
    # Vessel Management
    # -------------------------------------------------------------
    def upsert_vessel(self, vessel: Vessel) -> None:
        """Insert or update vessel record."""
        conn = self._get_connection()
        now_utc = datetime.now(timezone.utc).isoformat()
        last_seen = vessel.last_position_utc.isoformat() if vessel.last_position_utc else now_utc

        with self._lock, conn:
            conn.execute(
                """
                INSERT INTO vessels (
                    mmsi, name, ship_type, imo_number, call_sign,
                    gross_tonnage, deadweight_tonnes, length_overall_m, beam_m, year_built, flag,
                    draught_meters, destination, eta,
                    latest_lat, latest_lon, latest_sog, latest_cog, latest_hdg,
                    nav_status_code, nav_status_label, last_seen_utc, updated_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(mmsi) DO UPDATE SET
                    name = coalesce(excluded.name, vessels.name),
                    ship_type = coalesce(excluded.ship_type, vessels.ship_type),
                    imo_number = coalesce(excluded.imo_number, vessels.imo_number),
                    call_sign = coalesce(excluded.call_sign, vessels.call_sign),
                    gross_tonnage = coalesce(excluded.gross_tonnage, vessels.gross_tonnage),
                    deadweight_tonnes = coalesce(excluded.deadweight_tonnes, vessels.deadweight_tonnes),
                    length_overall_m = coalesce(excluded.length_overall_m, vessels.length_overall_m),
                    beam_m = coalesce(excluded.beam_m, vessels.beam_m),
                    year_built = coalesce(excluded.year_built, vessels.year_built),
                    flag = coalesce(excluded.flag, vessels.flag),
                    draught_meters = coalesce(excluded.draught_meters, vessels.draught_meters),
                    destination = coalesce(excluded.destination, vessels.destination),
                    eta = coalesce(excluded.eta, vessels.eta),
                    latest_lat = coalesce(excluded.latest_lat, vessels.latest_lat),
                    latest_lon = coalesce(excluded.latest_lon, vessels.latest_lon),
                    latest_sog = coalesce(excluded.latest_sog, vessels.latest_sog),
                    latest_cog = coalesce(excluded.latest_cog, vessels.latest_cog),
                    latest_hdg = coalesce(excluded.latest_hdg, vessels.latest_hdg),
                    nav_status_code = coalesce(excluded.nav_status_code, vessels.nav_status_code),
                    nav_status_label = coalesce(excluded.nav_status_label, vessels.nav_status_label),
                    last_seen_utc = excluded.last_seen_utc,
                    updated_at_utc = excluded.updated_at_utc
                """,
                (
                    vessel.mmsi,
                    vessel.name,
                    vessel.ship_type,
                    vessel.imo_number,
                    vessel.call_sign,
                    vessel.particulars.gross_tonnage,
                    vessel.particulars.deadweight_tonnes,
                    vessel.particulars.length_overall_m,
                    vessel.particulars.beam_m,
                    vessel.particulars.year_built,
                    vessel.particulars.flag,
                    vessel.draught_meters,
                    vessel.destination,
                    vessel.eta,
                    vessel.latitude,
                    vessel.longitude,
                    vessel.speed_knots,
                    vessel.course_deg,
                    vessel.heading_deg,
                    vessel.nav_status_code,
                    vessel.nav_status_label,
                    last_seen,
                    now_utc,
                ),
            )

    def get_vessel(self, mmsi: int) -> Optional[Vessel]:
        """Fetch vessel by MMSI."""
        conn = self._get_connection()
        cur = conn.execute("SELECT * FROM vessels WHERE mmsi = ?", (mmsi,))
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_vessel(row)

    def list_vessels(self) -> List[Vessel]:
        """Fetch all known vessels ordered by last seen."""
        conn = self._get_connection()
        cur = conn.execute("SELECT * FROM vessels ORDER BY last_seen_utc DESC")
        return [self._row_to_vessel(row) for row in cur.fetchall()]

    # -------------------------------------------------------------
    # Particulars Enrichment State
    # -------------------------------------------------------------
    def get_enrichment_state(self, mmsi: int) -> Optional[dict]:
        """Return the enrichment bookkeeping for a vessel, or None if unknown."""
        conn = self._get_connection()
        cur = conn.execute(
            """
            SELECT particulars_source, particulars_updated_utc,
                   enrichment_attempted_utc, enrichment_status
            FROM vessels WHERE mmsi = ?
            """,
            (mmsi,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {
            "source": row["particulars_source"],
            "updated_utc": _parse_utc(row["particulars_updated_utc"]),
            "attempted_utc": _parse_utc(row["enrichment_attempted_utc"]),
            "status": row["enrichment_status"],
        }

    def mark_enrichment(
        self,
        mmsi: int,
        status: str,
        source: Optional[str] = None,
        attempted_at: Optional[datetime] = None,
    ) -> None:
        """Record the outcome of an enrichment attempt.

        ``status`` is ``'ok'``, ``'not_found'`` or ``'error'``. The success
        timestamp is only advanced for ``'ok'`` so failures do not look like
        fresh data, while ``enrichment_attempted_utc`` always advances to drive
        retry back-off.
        """
        conn = self._get_connection()
        stamp = (attempted_at or datetime.now(timezone.utc)).isoformat()

        with self._lock, conn:
            # Ensure a row exists so enrichment can precede the first position.
            conn.execute("INSERT OR IGNORE INTO vessels (mmsi) VALUES (?)", (mmsi,))
            if status == "ok":
                conn.execute(
                    """
                    UPDATE vessels
                    SET enrichment_status = ?, enrichment_attempted_utc = ?,
                        particulars_source = coalesce(?, particulars_source),
                        particulars_updated_utc = ?
                    WHERE mmsi = ?
                    """,
                    (status, stamp, source, stamp, mmsi),
                )
            else:
                conn.execute(
                    """
                    UPDATE vessels
                    SET enrichment_status = ?, enrichment_attempted_utc = ?
                    WHERE mmsi = ?
                    """,
                    (status, stamp, mmsi),
                )

    def count_enriched_vessels(self) -> int:
        """Number of vessels carrying successfully scraped particulars."""
        conn = self._get_connection()
        cur = conn.execute("SELECT COUNT(*) AS n FROM vessels WHERE enrichment_status = 'ok'")
        return cur.fetchone()["n"]

    def _row_to_vessel(self, row: sqlite3.Row) -> Vessel:
        last_pos = _parse_utc(row["last_seen_utc"])

        part = VesselParticulars(
            gross_tonnage=row["gross_tonnage"],
            deadweight_tonnes=row["deadweight_tonnes"],
            length_overall_m=row["length_overall_m"],
            beam_m=row["beam_m"],
            year_built=row["year_built"],
            flag=row["flag"],
        )

        return Vessel(
            mmsi=row["mmsi"],
            name=row["name"] or f"Vessel-{row['mmsi']}",
            ship_type=row["ship_type"] or "Unspecified",
            imo_number=row["imo_number"],
            call_sign=row["call_sign"],
            latitude=row["latest_lat"],
            longitude=row["latest_lon"],
            speed_knots=row["latest_sog"],
            course_deg=row["latest_cog"],
            heading_deg=row["latest_hdg"],
            nav_status_code=row["nav_status_code"] if row["nav_status_code"] is not None else 15,
            nav_status_label=row["nav_status_label"] or "Undefined",
            last_position_utc=last_pos,
            draught_meters=row["draught_meters"],
            destination=row["destination"],
            eta=row["eta"],
            particulars=part,
            particulars_source=row["particulars_source"],
            particulars_updated_utc=_parse_utc(row["particulars_updated_utc"]),
        )

    # -------------------------------------------------------------
    # Session Management
    # -------------------------------------------------------------
    def create_session(self, session: ObservationSession) -> None:
        """Store new recording session."""
        conn = self._get_connection()
        targets_json = json.dumps(session.target_mmsis)
        with self._lock, conn:
            conn.execute(
                """
                INSERT INTO sessions (
                    session_id, station_id, start_time_utc, end_time_utc,
                    recording_mode, target_mmsis, message_count, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.station_id,
                    session.start_time_utc.isoformat(),
                    session.end_time_utc.isoformat() if session.end_time_utc else None,
                    session.recording_mode,
                    targets_json,
                    session.message_count,
                    session.notes,
                ),
            )

    def close_session(self, session_id: str, end_time_utc: datetime, count: int) -> None:
        """Close an active recording session."""
        conn = self._get_connection()
        with self._lock, conn:
            conn.execute(
                """
                UPDATE sessions
                SET end_time_utc = ?, message_count = ?
                WHERE session_id = ?
                """,
                (end_time_utc.isoformat(), count, session_id),
            )

    def get_session(self, session_id: str) -> Optional[ObservationSession]:
        """Retrieve a session by ID."""
        conn = self._get_connection()
        cur = conn.execute("SELECT * FROM sessions WHERE session_id = ?", (session_id,))
        row = cur.fetchone()
        if not row:
            return None
        return self._row_to_session(row)

    def list_sessions(self) -> List[ObservationSession]:
        """List all observation sessions sorted descending by start time."""
        conn = self._get_connection()
        cur = conn.execute("SELECT * FROM sessions ORDER BY start_time_utc DESC")
        return [self._row_to_session(row) for row in cur.fetchall()]

    def _row_to_session(self, row: sqlite3.Row) -> ObservationSession:
        start_t = datetime.fromisoformat(row["start_time_utc"])
        end_t = datetime.fromisoformat(row["end_time_utc"]) if row["end_time_utc"] else None
        target_mmsis = json.loads(row["target_mmsis"]) if row["target_mmsis"] else []
        return ObservationSession(
            session_id=row["session_id"],
            station_id=row["station_id"],
            start_time_utc=start_t,
            end_time_utc=end_t,
            recording_mode=row["recording_mode"],
            target_mmsis=target_mmsis,
            message_count=row["message_count"] or 0,
            notes=row["notes"],
        )

    # -------------------------------------------------------------
    # Observation Records
    # -------------------------------------------------------------
    def insert_record(self, record: ObservationRecord) -> int:
        """Insert a single timestamped observation record."""
        conn = self._get_connection()
        with self._lock, conn:
            cur = conn.execute(
                """
                INSERT INTO observation_records (
                    session_id, mmsi, received_at_utc, ais_timestamp_utc, raw_nmea, message_type,
                    ship_name, ship_type, imo_number, call_sign,
                    speed_knots, course_deg, heading_deg, draught_meters,
                    nav_status_code, nav_status_label, latitude, longitude,
                    gross_tonnage, deadweight_tonnes, length_overall_m, beam_m,
                    year_built, flag, destination, eta,
                    observation_window, observer_station
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.session_id,
                    record.mmsi,
                    record.received_at_utc.isoformat(),
                    record.ais_timestamp_utc.isoformat() if record.ais_timestamp_utc else None,
                    record.raw_nmea,
                    record.message_type,
                    record.ship_name,
                    record.ship_type,
                    record.imo_number,
                    record.call_sign,
                    record.speed_knots,
                    record.course_deg,
                    record.heading_deg,
                    record.draught_meters,
                    record.nav_status_code,
                    record.nav_status_label,
                    record.latitude,
                    record.longitude,
                    record.gross_tonnage,
                    record.deadweight_tonnes,
                    record.length_overall_m,
                    record.beam_m,
                    record.year_built,
                    record.flag,
                    record.destination,
                    record.eta,
                    record.observation_window,
                    record.observer_station,
                ),
            )
            # Update session message count
            conn.execute(
                "UPDATE sessions SET message_count = message_count + 1 WHERE session_id = ?",
                (record.session_id,),
            )
            return cur.lastrowid

    def get_records_for_session(self, session_id: str) -> List[ObservationRecord]:
        """Fetch all records recorded during a specific session."""
        conn = self._get_connection()
        cur = conn.execute(
            "SELECT * FROM observation_records WHERE session_id = ? ORDER BY id ASC",
            (session_id,),
        )
        return [self._row_to_record(row) for row in cur.fetchall()]

    def _row_to_record(self, row: sqlite3.Row) -> ObservationRecord:
        recv_t = datetime.fromisoformat(row["received_at_utc"])
        ais_t = datetime.fromisoformat(row["ais_timestamp_utc"]) if row["ais_timestamp_utc"] else None

        return ObservationRecord(
            id=row["id"],
            session_id=row["session_id"],
            mmsi=row["mmsi"],
            received_at_utc=recv_t,
            ais_timestamp_utc=ais_t,
            raw_nmea=row["raw_nmea"],
            message_type=row["message_type"],
            ship_name=row["ship_name"],
            ship_type=row["ship_type"],
            imo_number=row["imo_number"],
            call_sign=row["call_sign"],
            speed_knots=row["speed_knots"],
            course_deg=row["course_deg"],
            heading_deg=row["heading_deg"],
            draught_meters=row["draught_meters"],
            nav_status_code=row["nav_status_code"],
            nav_status_label=row["nav_status_label"],
            latitude=row["latitude"],
            longitude=row["longitude"],
            gross_tonnage=row["gross_tonnage"],
            deadweight_tonnes=row["deadweight_tonnes"],
            length_overall_m=row["length_overall_m"],
            beam_m=row["beam_m"],
            year_built=row["year_built"],
            flag=row["flag"],
            destination=row["destination"],
            eta=row["eta"],
            observation_window=row["observation_window"],
            observer_station=row["observer_station"],
        )
