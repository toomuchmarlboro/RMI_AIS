"""aisstream.io WebSocket live client."""
from __future__ import annotations

import json
import asyncio
import logging
from typing import AsyncGenerator, List, Optional, Tuple
from datetime import datetime, timezone
import websockets

from src.sources.base import DataSource
from src.models.vessel import VesselDynamic, VesselStatic, NavigationStatus

logger = logging.getLogger(__name__)


class AisStreamSource(DataSource):
    """Connects to aisstream.io real-time WebSocket service (Free API key)."""

    def __init__(
        self,
        api_key: str,
        bounding_boxes: Optional[List[List[List[float]]]] = None,
        target_mmsis: Optional[List[int]] = None,
        url: str = "wss://stream.aisstream.io/v0/stream",
    ):
        super().__init__(name="aisstream")
        self.api_key = api_key
        self.url = url
        self.bounding_boxes = bounding_boxes or [[[-90.0, -180.0], [90.0, 180.0]]]
        self.target_mmsis = target_mmsis or []
        self._ws: Optional[websockets.WebSocketClientProtocol] = None

    async def start(self) -> None:
        self._running = True
        logger.info("AisStreamSource starting...")

    async def stop(self) -> None:
        self._running = False
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        logger.info("AisStreamSource stopped.")

    async def stream(self) -> AsyncGenerator[Tuple[str, Optional[VesselDynamic], Optional[VesselStatic]], None]:
        """Maintains persistent WebSocket connection with exponential backoff on reconnect."""
        if not self.api_key:
            logger.warning("No AISSTREAM_API_KEY provided. AisStreamSource cannot connect.")
            return

        reconnect_delay = 2.0
        while self._running:
            try:
                logger.info(f"Connecting to {self.url}...")
                async with websockets.connect(self.url, ping_interval=20, ping_timeout=20) as ws:
                    self._ws = ws
                    reconnect_delay = 2.0  # Reset delay on successful connection

                    # Send subscription message
                    subscription = {
                        "APIKey": self.api_key,
                        "BoundingBoxes": self.bounding_boxes,
                        "FilterMessageTypes": ["PositionReport", "ShipStaticData", "StandardClassBPositionReport"],
                    }
                    if self.target_mmsis:
                        subscription["FiltersShipMMSI"] = [str(m) for m in self.target_mmsis]

                    await ws.send(json.dumps(subscription))
                    logger.info("Subscribed to aisstream.io feed.")

                    async for raw_msg in ws:
                        if not self._running:
                            break

                        try:
                            data = json.loads(raw_msg)
                            msg_type_name = data.get("MessageType")
                            meta = data.get("MetaData", {})
                            mmsi = int(meta.get("MMSI", 0))

                            if not mmsi:
                                continue

                            now_utc = datetime.now(timezone.utc)
                            raw_summary = f"[aisstream] {msg_type_name} MMSI:{mmsi}"

                            if msg_type_name in ("PositionReport", "StandardClassBPositionReport"):
                                report = data.get("Message", {}).get(msg_type_name, {})
                                lat = float(report.get("Latitude", meta.get("latitude", 0.0)))
                                lon = float(report.get("Longitude", meta.get("longitude", 0.0)))
                                sog = float(report.get("Sog", 0.0))
                                cog = float(report.get("Cog", 0.0))
                                hdg_raw = report.get("TrueHeading")
                                hdg = float(hdg_raw) if hdg_raw is not None and hdg_raw != 511 else None
                                nav_code = int(report.get("NavigationalStatus", 15))

                                dyn = VesselDynamic(
                                    mmsi=mmsi,
                                    timestamp=now_utc,
                                    latitude=round(lat, 6),
                                    longitude=round(lon, 6),
                                    speed_knots=round(sog, 1),
                                    course_deg=round(cog, 1),
                                    heading_deg=round(hdg, 1) if hdg is not None else None,
                                    nav_status_code=nav_code,
                                    nav_status_label=NavigationStatus.label_for(nav_code),
                                )
                                yield raw_summary, dyn, None

                            elif msg_type_name == "ShipStaticData":
                                static_data = data.get("Message", {}).get("ShipStaticData", {})
                                name = meta.get("ShipName") or static_data.get("Name")
                                draught = static_data.get("MaximumStaticDraught")
                                destination = static_data.get("Destination")
                                imo = static_data.get("ImoNumber")
                                callsign = static_data.get("CallSign")
                                dim = static_data.get("Dimension", {})

                                st = VesselStatic(
                                    mmsi=mmsi,
                                    ship_name=name.strip() if name else None,
                                    call_sign=callsign.strip() if callsign else None,
                                    imo_number=int(imo) if imo else None,
                                    draught_meters=float(draught) if draught else None,
                                    destination=destination.strip() if destination else None,
                                    dimension_to_bow_m=float(dim.get("A", 0)) if dim.get("A") else None,
                                    dimension_to_stern_m=float(dim.get("B", 0)) if dim.get("B") else None,
                                    dimension_to_port_m=float(dim.get("C", 0)) if dim.get("C") else None,
                                    dimension_to_starboard_m=float(dim.get("D", 0)) if dim.get("D") else None,
                                )
                                yield raw_summary, None, st

                        except Exception as parse_err:
                            logger.debug(f"Failed to process aisstream payload: {parse_err}")

            except (websockets.ConnectionClosed, OSError) as conn_err:
                if self._running:
                    logger.warning(f"Aisstream connection lost: {conn_err}. Reconnecting in {reconnect_delay}s...")
                    await asyncio.sleep(reconnect_delay)
                    reconnect_delay = min(reconnect_delay * 1.5, 60.0)
            except Exception as e:
                if self._running:
                    logger.error(f"Unexpected error in AisstreamSource: {e}")
                    await asyncio.sleep(5.0)
