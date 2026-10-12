"""Observation recording session and observation record models."""
from __future__ import annotations

import uuid
from typing import Optional, List
from datetime import datetime, timezone
from pydantic import BaseModel, Field


def generate_session_id() -> str:
    """Generate a readable session identifier formatted with timestamp and short hex."""
    now_str = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    short_uuid = uuid.uuid4().hex[:6].upper()
    return f"OBS-{now_str}-{short_uuid}"


class ObservationSession(BaseModel):
    """An active or completed recording session."""
    session_id: str = Field(default_factory=generate_session_id)
    station_id: str = "Station-Desktop-01"
    start_time_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    end_time_utc: Optional[datetime] = None
    recording_mode: str = "simulation"  # 'simulation', 'aisstream', 'marinetraffic', 'manual'
    target_mmsis: List[int] = Field(default_factory=list, description="Target vessels to filter or empty for all")
    message_count: int = 0
    notes: Optional[str] = None

    @property
    def is_active(self) -> bool:
        return self.end_time_utc is None

    @property
    def window_label(self) -> str:
        """Observation window formatted as e.g. 23:00 — 23:18 or 23:00 — In Progress."""
        start_fmt = self.start_time_utc.strftime("%H:%M:%S")
        if self.end_time_utc:
            end_fmt = self.end_time_utc.strftime("%H:%M:%S")
            return f"{start_fmt} — {end_fmt}"
        return f"{start_fmt} — (Recording...)"


class ObservationRecord(BaseModel):
    """Individual captured vessel telemetry record matching VesselFinder annotation schema."""
    id: Optional[int] = None
    session_id: str
    mmsi: int
    received_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    ais_timestamp_utc: Optional[datetime] = None
    raw_nmea: Optional[str] = None
    message_type: int = 1

    # Vessel details matching VesselFinder layout
    ship_name: Optional[str] = None
    ship_type: Optional[str] = None
    imo_number: Optional[int] = None
    call_sign: Optional[str] = None

    # Dynamic metrics
    speed_knots: Optional[float] = None
    course_deg: Optional[float] = None
    heading_deg: Optional[float] = None
    draught_meters: Optional[float] = None
    nav_status_code: Optional[int] = None
    nav_status_label: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

    # Static / Particulars
    gross_tonnage: Optional[int] = None
    deadweight_tonnes: Optional[int] = None
    length_overall_m: Optional[float] = None
    beam_m: Optional[float] = None
    year_built: Optional[int] = None
    flag: Optional[str] = None
    destination: Optional[str] = None
    eta: Optional[str] = None

    # Observation metadata
    observation_window: Optional[str] = None
    observer_station: Optional[str] = None
