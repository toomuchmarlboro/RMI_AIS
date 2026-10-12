"""Decodes raw NMEA AIVDM sentences into normalized Vessel data models."""
from __future__ import annotations

import logging
from typing import Optional, Union, List, Tuple
from datetime import datetime, timezone
import pyais
from pyais import decode

from src.models.vessel import (
    NavigationStatus,
    VesselDynamic,
    VesselStatic,
)

logger = logging.getLogger(__name__)


def _clean_str(val: Optional[str]) -> Optional[str]:
    """Strip padding, @ signs, and null characters from 6-bit ASCII marine strings."""
    if val is None:
        return None
    cleaned = str(val).strip(" @\x00").strip()
    return cleaned if cleaned else None


def _format_eta(month: Optional[int], day: Optional[int], hour: Optional[int], minute: Optional[int]) -> Optional[str]:
    """Format AIS ETA fields into standard MM-DD HH:MM format."""
    if not month or not day or month > 12 or day > 31:
        return None
    h = hour if hour is not None and 0 <= hour <= 23 else 0
    m = minute if minute is not None and 0 <= minute <= 59 else 0
    return f"{month:02d}-{day:02d} {h:02d}:{m:02d}"


class AISDecoder:
    """Decodes single or multi-part AIVDM sentences using pyais."""

    @classmethod
    def decode_sentences(
        cls, sentences: Union[str, List[str]]
    ) -> Tuple[Optional[int], Optional[int], Optional[VesselDynamic], Optional[VesselStatic]]:
        """
        Decodes one or more raw NMEA sentences.
        Returns:
            (msg_type, mmsi, vessel_dynamic, vessel_static)
        """
        if isinstance(sentences, str):
            lines = [sentences]
        else:
            lines = sentences

        try:
            msg = decode(*lines)
        except Exception as e:
            logger.debug(f"Failed to decode AIS sentences: {e}")
            return None, None, None, None

        msg_type = int(getattr(msg, "msg_type", 0))
        mmsi = int(getattr(msg, "mmsi", 0))

        vessel_dynamic: Optional[VesselDynamic] = None
        vessel_static: Optional[VesselStatic] = None

        now_utc = datetime.now(timezone.utc)

        # Message Types 1, 2, 3: Class A Position Report
        # Message Type 18: Class B Position Report
        if msg_type in (1, 2, 3, 18):
            raw_status = getattr(msg, "status", None)
            if raw_status is not None:
                try:
                    nav_code = int(raw_status)
                except (ValueError, TypeError):
                    nav_code = 15
            else:
                nav_code = 15

            nav_label = NavigationStatus.label_for(nav_code)

            speed = float(getattr(msg, "speed", 0.0) or 0.0)
            course = float(getattr(msg, "course", 0.0) or 0.0)
            raw_hdg = getattr(msg, "heading", None)
            heading = float(raw_hdg) if raw_hdg is not None and raw_hdg != 511 else None

            lat = float(getattr(msg, "lat", 0.0) or 0.0)
            lon = float(getattr(msg, "lon", 0.0) or 0.0)
            rot = getattr(msg, "turn", None)
            rot_val = float(rot) if rot is not None else None

            # Filter out invalid sentinel positions (e.g., 91/181)
            if -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0:
                vessel_dynamic = VesselDynamic(
                    mmsi=mmsi,
                    timestamp=now_utc,
                    latitude=round(lat, 6),
                    longitude=round(lon, 6),
                    speed_knots=round(speed, 1),
                    course_deg=round(course, 1),
                    heading_deg=round(heading, 1) if heading is not None else None,
                    nav_status_code=nav_code,
                    nav_status_label=nav_label,
                    rot=rot_val,
                )

        # Message Type 5: Class A Static & Voyage Data
        elif msg_type == 5:
            ship_name = _clean_str(getattr(msg, "shipname", None))
            call_sign = _clean_str(getattr(msg, "callsign", None))
            raw_type = getattr(msg, "ship_type", None)
            ship_type = raw_type.name if hasattr(raw_type, "name") else (str(raw_type) if raw_type else None)

            imo_raw = getattr(msg, "imo", None)
            imo_number = int(imo_raw) if imo_raw and int(imo_raw) > 0 else None

            draught_raw = getattr(msg, "draught", None)
            draught = round(float(draught_raw), 1) if draught_raw is not None and float(draught_raw) > 0 else None

            destination = _clean_str(getattr(msg, "destination", None))
            eta = _format_eta(
                getattr(msg, "month", None),
                getattr(msg, "day", None),
                getattr(msg, "hour", None),
                getattr(msg, "minute", None),
            )

            to_bow = float(getattr(msg, "to_bow", 0) or 0)
            to_stern = float(getattr(msg, "to_stern", 0) or 0)
            to_port = float(getattr(msg, "to_port", 0) or 0)
            to_starboard = float(getattr(msg, "to_starboard", 0) or 0)

            vessel_static = VesselStatic(
                mmsi=mmsi,
                ship_name=ship_name,
                ship_type=ship_type,
                call_sign=call_sign,
                imo_number=imo_number,
                draught_meters=draught,
                destination=destination,
                eta=eta,
                dimension_to_bow_m=to_bow if to_bow > 0 else None,
                dimension_to_stern_m=to_stern if to_stern > 0 else None,
                dimension_to_port_m=to_port if to_port > 0 else None,
                dimension_to_starboard_m=to_starboard if to_starboard > 0 else None,
            )

        # Message Type 24: Class B Static Data
        elif msg_type == 24:
            ship_name = _clean_str(getattr(msg, "shipname", None))
            call_sign = _clean_str(getattr(msg, "callsign", None))
            raw_type = getattr(msg, "ship_type", None)
            ship_type = raw_type.name if hasattr(raw_type, "name") else None

            to_bow = float(getattr(msg, "to_bow", 0) or 0)
            to_stern = float(getattr(msg, "to_stern", 0) or 0)
            to_port = float(getattr(msg, "to_port", 0) or 0)
            to_starboard = float(getattr(msg, "to_starboard", 0) or 0)

            vessel_static = VesselStatic(
                mmsi=mmsi,
                ship_name=ship_name,
                ship_type=ship_type,
                call_sign=call_sign,
                dimension_to_bow_m=to_bow if to_bow > 0 else None,
                dimension_to_stern_m=to_stern if to_stern > 0 else None,
                dimension_to_port_m=to_port if to_port > 0 else None,
                dimension_to_starboard_m=to_starboard if to_starboard > 0 else None,
            )

        return msg_type, mmsi, vessel_dynamic, vessel_static
