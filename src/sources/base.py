"""Abstract base class for all AIS Data Sources."""
from __future__ import annotations

import abc
from typing import AsyncGenerator, Optional, Tuple, List
from src.models.vessel import VesselDynamic, VesselStatic


class DataSource(abc.ABC):
    """Common abstraction for AIS data ingestion (Simulator, Aisstream, VesselFinder)."""

    def __init__(self, name: str):
        self.name = name
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @abc.abstractmethod
    async def start(self) -> None:
        """Initialize connections, websockets, or internal simulation timers."""
        pass

    @abc.abstractmethod
    async def stop(self) -> None:
        """Gracefully terminate any active connections."""
        pass

    @abc.abstractmethod
    async def stream(self) -> AsyncGenerator[Tuple[str, Optional[VesselDynamic], Optional[VesselStatic]], None]:
        """
        Yields tuples of:
            (raw_nmea_sentence, optional_dynamic_telemetry, optional_static_voyage_data)
        """
        pass
