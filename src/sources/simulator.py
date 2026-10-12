"""Realistic NMEA AIS simulator generating authentic AIVDM sentences and vessel movement."""
from __future__ import annotations

import asyncio
import math
import logging
from typing import AsyncGenerator, Dict, List, Optional, Tuple
from datetime import datetime, timezone

from pyais.messages import MessageType1, MessageType5
from src.sources.base import DataSource
from src.models.vessel import Vessel, VesselParticulars, VesselDynamic, VesselStatic, NavigationStatus
from src.models.nmea import NMEASentence
from src.decoder.ais_decoder import AISDecoder

logger = logging.getLogger(__name__)


class SimulatedVessel:
    """Internal state tracker for a simulated vessel moving through water."""

    def __init__(self, config: dict):
        self.mmsi = int(config["mmsi"])
        self.name = config.get("name", f"Vessel-{self.mmsi}")
        self.ship_type = config.get("ship_type", "Passenger/Ro-Ro Cargo Ship")
        self.imo = config.get("imo", 0)
        self.call_sign = config.get("call_sign", "TEST")
        self.draught = float(config.get("draught", 5.0))
        self.destination = config.get("destination", "PORT")
        self.eta = config.get("eta", "10-13 06:00")

        self.lat = float(config.get("initial_lat", 0.0))
        self.lon = float(config.get("initial_lon", 0.0))
        self.sog = float(config.get("initial_sog", 10.0))
        self.cog = float(config.get("initial_cog", 90.0))
        self.hdg = float(config.get("initial_hdg", self.cog))
        self.nav_status = int(config.get("nav_status", 0))

        # Particulars
        self.gross_tonnage = config.get("gross_tonnage", 5000)
        self.deadweight = config.get("deadweight", 3000)
        self.length = float(config.get("length", 100.0))
        self.beam = float(config.get("beam", 20.0))
        self.year_built = config.get("year_built", 2015)
        self.flag = config.get("flag", "Indonesia")

        self.ticks = 0

    def step(self, dt_seconds: float) -> None:
        """Update vessel coordinates based on course and speed."""
        # Convert knots to nautical miles per second (1 knot = 1 nm / hour = 1/3600 nm/s)
        # 1 nautical mile = 1 minute of latitude = 1/60 degrees
        dist_nm = (self.sog * dt_seconds) / 3600.0
        rad_course = math.radians(self.cog)

        delta_lat = (dist_nm * math.cos(rad_course)) / 60.0
        # Longitude scaling by cosine of latitude
        avg_lat_rad = math.radians(self.lat + delta_lat / 2.0)
        cos_lat = max(math.cos(avg_lat_rad), 0.001)
        delta_lon = (dist_nm * math.sin(rad_course)) / (60.0 * cos_lat)

        self.lat += delta_lat
        self.lon += delta_lon

        # Minor realistic drift in heading/course (+/- 0.2 deg)
        self.ticks += 1
        drift = 0.3 * math.sin(self.ticks * 0.1)
        self.cog = (self.cog + drift) % 360.0
        self.hdg = round(self.cog)

    def to_vessel_model(self) -> Vessel:
        """Create consolidated Vessel model with static particulars and latest dynamic state."""
        part = VesselParticulars(
            gross_tonnage=self.gross_tonnage,
            deadweight_tonnes=self.deadweight,
            length_overall_m=self.length,
            beam_m=self.beam,
            year_built=self.year_built,
            flag=self.flag,
        )
        return Vessel(
            mmsi=self.mmsi,
            name=self.name,
            ship_type=self.ship_type,
            imo_number=self.imo,
            call_sign=self.call_sign,
            latitude=round(self.lat, 6),
            longitude=round(self.lon, 6),
            speed_knots=round(self.sog, 1),
            course_deg=round(self.cog, 1),
            heading_deg=round(self.hdg, 1),
            nav_status_code=self.nav_status,
            nav_status_label=NavigationStatus.label_for(self.nav_status),
            last_position_utc=datetime.now(timezone.utc),
            draught_meters=self.draught,
            destination=self.destination,
            eta=self.eta,
            particulars=part,
        )


class SimulatorSource(DataSource):
    """Generates synthetic AIS data stream emitting authentic NMEA AIVDM sentences."""

    def __init__(self, vessels_config: List[dict], interval_sec: float = 3.0):
        super().__init__(name="simulator")
        self.interval_sec = interval_sec
        self.vessels = [SimulatedVessel(cfg) for cfg in vessels_config]
        self._step_counter = 0

    async def start(self) -> None:
        self._running = True
        logger.info(f"AIS Simulator started with {len(self.vessels)} vessels.")

    async def stop(self) -> None:
        self._running = False
        logger.info("AIS Simulator stopped.")

    def get_initial_vessels(self) -> List[Vessel]:
        """Return initial vessel states for pre-populating database."""
        return [v.to_vessel_model() for v in self.vessels]

    async def stream(self) -> AsyncGenerator[Tuple[str, Optional[VesselDynamic], Optional[VesselStatic]], None]:
        """Continuously yield simulated NMEA sentences and decoded telemetry."""
        while self._running:
            for v in self.vessels:
                v.step(self.interval_sec)

                # 1. Generate Position Report (Type 1)
                try:
                    t1_msg = MessageType1.create(
                        mmsi=v.mmsi,
                        speed=round(v.sog, 1),
                        course=round(v.cog, 1),
                        heading=int(round(v.hdg)),
                        lat=v.lat,
                        lon=v.lon,
                        status=v.nav_status,
                    )
                    payload, fill = t1_msg.encode()
                    body = f"AIVDM,1,1,,A,{payload},{fill}"
                    csum = NMEASentence.calculate_checksum(body)
                    raw_nmea = f"!{body}*{csum}"

                    dyn = VesselDynamic(
                        mmsi=v.mmsi,
                        timestamp=datetime.now(timezone.utc),
                        latitude=round(v.lat, 6),
                        longitude=round(v.lon, 6),
                        speed_knots=round(v.sog, 1),
                        course_deg=round(v.cog, 1),
                        heading_deg=round(v.hdg, 1),
                        nav_status_code=v.nav_status,
                        nav_status_label=NavigationStatus.label_for(v.nav_status),
                    )

                    yield raw_nmea, dyn, None

                except Exception as e:
                    logger.error(f"Error encoding simulated Type 1 message for {v.name}: {e}")

                # 2. Periodically emit Static / Voyage Data (Type 5) every 10 cycles
                if self._step_counter % 10 == 0:
                    st = VesselStatic(
                        mmsi=v.mmsi,
                        ship_name=v.name,
                        ship_type=v.ship_type,
                        call_sign=v.call_sign,
                        imo_number=v.imo,
                        draught_meters=v.draught,
                        destination=v.destination,
                        eta=v.eta,
                        dimension_to_bow_m=v.length * 0.7,
                        dimension_to_stern_m=v.length * 0.3,
                        dimension_to_port_m=v.beam * 0.5,
                        dimension_to_starboard_m=v.beam * 0.5,
                    )
                    # Simulated raw comment for static data audit
                    type5_raw = f"!AIVDM,1,1,,A,SIMULATED_STATIC_DATA_MMSI_{v.mmsi},0*00"
                    yield type5_raw, None, st

            self._step_counter += 1
            await asyncio.sleep(self.interval_sec)
