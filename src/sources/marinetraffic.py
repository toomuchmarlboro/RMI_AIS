"""MarineTraffic REST API Client (Phase 3 Integration)."""
from __future__ import annotations

import re
import logging
from typing import AsyncGenerator, Dict, List, Optional, Tuple
from datetime import datetime, timezone
import httpx

from src.sources.base import DataSource
from src.models.vessel import VesselDynamic, VesselStatic, VesselParticulars, NavigationStatus

logger = logging.getLogger(__name__)


class MarineTrafficSource(DataSource):
    """
    Client for MarineTraffic Commercial REST API.
    Interacts with endpoints PS07 (Single Position), PS06 (Area), and VD02 (Particulars).
    """

    BASE_URL = "https://services.marinetraffic.com/api"

    def __init__(
        self,
        api_key: str,
        target_mmsis: Optional[List[int]] = None,
        poll_interval_sec: float = 60.0,
    ):
        super().__init__(name="marinetraffic")
        self.api_key = api_key.strip() if api_key else ""
        self.target_mmsis = target_mmsis or []
        self.poll_interval_sec = poll_interval_sec
        self._validate_api_key()

    def _validate_api_key(self) -> bool:
        """Rule A06: Validate 40-character hexadecimal format."""
        if not self.api_key:
            return False
        pattern = r"^[0-9a-fA-F]{40}$"
        is_valid = bool(re.match(pattern, self.api_key))
        if not is_valid:
            logger.warning("MarineTraffic API Key does not match standard 40-hex format.")
        return is_valid

    async def start(self) -> None:
        self._running = True
        logger.info(f"MarineTrafficSource started (tracking {len(self.target_mmsis)} vessels).")

    async def stop(self) -> None:
        self._running = False
        logger.info("MarineTrafficSource stopped.")

    async def fetch_vessel_particulars(self, mmsi: int) -> Optional[VesselParticulars]:
        """Fetch vessel master data via VD02 endpoint."""
        if not self._validate_api_key():
            return None

        url = f"{self.BASE_URL}/exportvessel/v:5/{self.api_key}/mmsi:{mmsi}/protocol:jsono"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    # Parse VD02 particulars
                    row = data[0] if isinstance(data, list) and data else data
                    return VesselParticulars(
                        gross_tonnage=int(row.get("GROSS_TONNAGE")) if row.get("GROSS_TONNAGE") else None,
                        deadweight_tonnes=int(row.get("DWT")) if row.get("DWT") else None,
                        length_overall_m=float(row.get("LENGTH")) if row.get("LENGTH") else None,
                        beam_m=float(row.get("WIDTH")) if row.get("WIDTH") else None,
                        year_built=int(row.get("YEAR_BUILT")) if row.get("YEAR_BUILT") else None,
                        flag=row.get("FLAG"),
                    )
        except Exception as e:
            logger.error(f"Error fetching VD02 particulars for MMSI {mmsi}: {e}")
        return None

    async def stream(self) -> AsyncGenerator[Tuple[str, Optional[VesselDynamic], Optional[VesselStatic]], None]:
        """Polls positions via PS07 for watched vessels."""
        if not self._validate_api_key():
            logger.warning("MarineTrafficSource requires a valid 40-hex API key.")
            return

        while self._running:
            for mmsi in self.target_mmsis:
                try:
                    url = f"{self.BASE_URL}/exportvessels/v:8/timespan:10/msgtype:simple/protocol:jsono/{self.api_key}/mmsi:{mmsi}"
                    async with httpx.AsyncClient(timeout=10.0) as client:
                        resp = await client.get(url)
                        if resp.status_code == 200:
                            rows = resp.json()
                            if rows and isinstance(rows, list):
                                r = rows[0]
                                now_utc = datetime.now(timezone.utc)
                                nav_code = int(r.get("STATUS", 15))
                                speed_raw = float(r.get("SPEED", 0.0))
                                # MT often returns speed * 10
                                sog = speed_raw / 10.0 if speed_raw > 100 else speed_raw

                                dyn = VesselDynamic(
                                    mmsi=mmsi,
                                    timestamp=now_utc,
                                    latitude=float(r.get("LAT", 0.0)),
                                    longitude=float(r.get("LON", 0.0)),
                                    speed_knots=round(sog, 1),
                                    course_deg=float(r.get("COURSE", 0.0)),
                                    heading_deg=float(r.get("HEADING")) if r.get("HEADING") is not None else None,
                                    nav_status_code=nav_code,
                                    nav_status_label=NavigationStatus.label_for(nav_code),
                                )
                                raw = f"[marinetraffic:PS07] MMSI:{mmsi} SOG:{sog} LAT:{dyn.latitude} LON:{dyn.longitude}"
                                yield raw, dyn, None

                except Exception as e:
                    logger.error(f"Error polling MarineTraffic for MMSI {mmsi}: {e}")

            import asyncio
            await asyncio.sleep(self.poll_interval_sec)
