"""Vessel data models, navigation status definitions, and particulars."""
from __future__ import annotations

from enum import IntEnum
from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class NavigationStatus(IntEnum):
    """AIS Navigation Status codes (ITU-R M.1371)."""
    UNDER_WAY_USING_ENGINE = 0
    AT_ANCHOR = 1
    NOT_UNDER_COMMAND = 2
    RESTRICTED_MANOEUVRABILITY = 3
    CONSTRAINED_BY_DRAUGHT = 4
    MOORED = 5
    AGROUND = 6
    ENGAGED_IN_FISHING = 7
    UNDER_WAY_SAILING = 8
    RESERVED_HSC = 9
    RESERVED_WIG = 10
    TOWING_ASTERN = 11
    PUSHING_OR_TOWING = 12
    RESERVED_13 = 13
    AIS_SART_MOB_EPIRB = 14
    UNDEFINED = 15

    @classmethod
    def label_for(cls, code: int | None) -> str:
        """Return human-readable status text matching VesselFinder / marine standards."""
        if code is None:
            return "Undefined"
        labels = {
            0: "Under way using engine",
            1: "At anchor",
            2: "Not under command",
            3: "Restricted manoeuvrability",
            4: "Constrained by draught",
            5: "Moored",
            6: "Aground",
            7: "Engaged in fishing",
            8: "Sailing",
            9: "Reserved (HSC)",
            10: "Reserved (WIG)",
            11: "Towing astern",
            12: "Pushing or towing",
            13: "Reserved",
            14: "AIS-SART / MOB / EPIRB",
            15: "Undefined",
        }
        return labels.get(code, "Undefined")


class VesselParticulars(BaseModel):
    """Static vessel master data / physical specifications."""
    gross_tonnage: Optional[int] = Field(None, description="Gross Tonnage (GT, volume)")
    deadweight_tonnes: Optional[int] = Field(None, description="Deadweight (DWT in metric tonnes)")
    length_overall_m: Optional[float] = Field(None, description="Length Overall (LOA) in meters")
    beam_m: Optional[float] = Field(None, description="Beam / Width in meters")
    year_built: Optional[int] = Field(None, description="Year vessel was built")
    flag: Optional[str] = Field(None, description="Flag state / country of registration")


class VesselDynamic(BaseModel):
    """High-frequency telemetry received via AIS Message Types 1, 2, 3, 18."""
    mmsi: int = Field(..., description="Maritime Mobile Service Identity (9-digit)")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="AIS or message UTC timestamp")
    latitude: float = Field(..., ge=-90.0, le=90.0, description="Latitude in decimal degrees")
    longitude: float = Field(..., ge=-180.0, le=180.0, description="Longitude in decimal degrees")
    speed_knots: float = Field(..., ge=0.0, description="Speed Over Ground (SOG) in knots")
    course_deg: float = Field(..., ge=0.0, le=360.0, description="Course Over Ground (COG) relative to True North")
    heading_deg: Optional[float] = Field(None, ge=0.0, le=360.0, description="True Heading (HDG) in degrees")
    nav_status_code: int = Field(default=15, description="Raw Navigation Status code (0-15)")
    nav_status_label: str = Field(default="Undefined", description="Human-readable status label")
    rot: Optional[float] = Field(None, description="Rate of Turn (deg/min)")


class VesselStatic(BaseModel):
    """Voyage and identity attributes received via AIS Message Type 5 / 24."""
    mmsi: int
    ship_name: Optional[str] = None
    ship_type: Optional[str] = None
    call_sign: Optional[str] = None
    imo_number: Optional[int] = None
    draught_meters: Optional[float] = Field(None, ge=0.0, description="Current Draught in meters")
    destination: Optional[str] = None
    eta: Optional[str] = None
    dimension_to_bow_m: Optional[float] = None
    dimension_to_stern_m: Optional[float] = None
    dimension_to_port_m: Optional[float] = None
    dimension_to_starboard_m: Optional[float] = None

    # Master data, when the source supplies it (e.g. the VesselFinder scraper).
    # AIS itself never transmits tonnage, year built or flag.
    particulars: Optional[VesselParticulars] = None


class Vessel(BaseModel):
    """Consolidated vessel state matching VesselFinder interface."""
    mmsi: int
    name: str = "Unknown Vessel"
    ship_type: str = "Unspecified"
    imo_number: Optional[int] = None
    call_sign: Optional[str] = None

    # Latest dynamic position & movement
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    speed_knots: Optional[float] = None
    course_deg: Optional[float] = None
    heading_deg: Optional[float] = None
    nav_status_code: int = 15
    nav_status_label: str = "Undefined"
    last_position_utc: Optional[datetime] = None

    # Latest voyage data
    draught_meters: Optional[float] = None
    destination: Optional[str] = None
    eta: Optional[str] = None

    # Vessel particulars
    particulars: VesselParticulars = Field(default_factory=VesselParticulars)

    # Provenance of the particulars above: 'vesselfinder', 'manual', 'simulator', ...
    particulars_source: Optional[str] = None
    particulars_updated_utc: Optional[datetime] = None

    def update_dynamic(self, dyn: VesselDynamic) -> None:
        """Update live telemetry from position report."""
        self.latitude = dyn.latitude
        self.longitude = dyn.longitude
        self.speed_knots = dyn.speed_knots
        self.course_deg = dyn.course_deg
        self.heading_deg = dyn.heading_deg
        self.nav_status_code = dyn.nav_status_code
        self.nav_status_label = dyn.nav_status_label
        self.last_position_utc = dyn.timestamp

    def update_static(self, st: VesselStatic) -> None:
        """Update voyage & static details from voyage report."""
        if st.ship_name:
            self.name = st.ship_name.strip()
        if st.ship_type:
            self.ship_type = st.ship_type.strip()
        if st.call_sign:
            self.call_sign = st.call_sign.strip()
        if st.imo_number:
            self.imo_number = st.imo_number
        if st.draught_meters is not None:
            self.draught_meters = st.draught_meters
        if st.destination:
            self.destination = st.destination.strip()
        if st.eta:
            self.eta = st.eta.strip()

        # Fold in any master data the source resolved, keeping existing values
        # for fields it left blank.
        if st.particulars:
            for field_name, value in st.particulars.model_dump(exclude_none=True).items():
                setattr(self.particulars, field_name, value)

        # Update dimensions if LOA / beam not already specified
        if st.dimension_to_bow_m is not None and st.dimension_to_stern_m is not None:
            calculated_loa = st.dimension_to_bow_m + st.dimension_to_stern_m
            if calculated_loa > 0 and self.particulars.length_overall_m is None:
                self.particulars.length_overall_m = calculated_loa

        if st.dimension_to_port_m is not None and st.dimension_to_starboard_m is not None:
            calculated_beam = st.dimension_to_port_m + st.dimension_to_starboard_m
            if calculated_beam > 0 and self.particulars.beam_m is None:
                self.particulars.beam_m = calculated_beam
