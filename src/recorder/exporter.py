"""Export observation sessions to CSV and structured JSON files."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import List
from src.models.observation import ObservationSession, ObservationRecord


class SessionExporter:
    """Exports observation records in standard maritime and data analysis formats."""

    @staticmethod
    def export_to_csv(session: ObservationSession, records: List[ObservationRecord], output_path: str | Path) -> Path:
        """Export session records to CSV matching VesselFinder field specifications."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        fieldnames = [
            "Observation ID",
            "Observer Station",
            "Observation Window",
            "Timestamp UTC",
            "AIS Timestamp UTC",
            "MMSI",
            "Ship Name",
            "Ship Type / Class",
            "IMO Number",
            "Call Sign",
            "Navigation Status",
            "Navigation Status Code",
            "Speed (kn)",
            "Course (°)",
            "Heading (°)",
            "Latitude",
            "Longitude",
            "Draught (m)",
            "Destination",
            "ETA",
            "Gross Tonnage (GT)",
            "Deadweight (tonnes)",
            "Length (m)",
            "Beam (m)",
            "Year Built",
            "Flag State",
            "Raw NMEA",
        ]

        with open(path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            for r in records:
                writer.writerow(
                    {
                        "Observation ID": r.session_id,
                        "Observer Station": r.observer_station or session.station_id,
                        "Observation Window": r.observation_window or session.window_label,
                        "Timestamp UTC": r.received_at_utc.isoformat(),
                        "AIS Timestamp UTC": r.ais_timestamp_utc.isoformat() if r.ais_timestamp_utc else "",
                        "MMSI": r.mmsi,
                        "Ship Name": r.ship_name or "",
                        "Ship Type / Class": r.ship_type or "",
                        "IMO Number": r.imo_number or "",
                        "Call Sign": r.call_sign or "",
                        "Navigation Status": r.nav_status_label or "Undefined",
                        "Navigation Status Code": r.nav_status_code if r.nav_status_code is not None else "",
                        "Speed (kn)": f"{r.speed_knots:.1f}" if r.speed_knots is not None else "",
                        "Course (°)": f"{r.course_deg:.1f}" if r.course_deg is not None else "",
                        "Heading (°)": f"{r.heading_deg:.0f}" if r.heading_deg is not None else "",
                        "Latitude": f"{r.latitude:.6f}" if r.latitude is not None else "",
                        "Longitude": f"{r.longitude:.6f}" if r.longitude is not None else "",
                        "Draught (m)": f"{r.draught_meters:.1f}" if r.draught_meters is not None else "",
                        "Destination": r.destination or "",
                        "ETA": r.eta or "",
                        "Gross Tonnage (GT)": r.gross_tonnage or "",
                        "Deadweight (tonnes)": r.deadweight_tonnes or "",
                        "Length (m)": r.length_overall_m or "",
                        "Beam (m)": r.beam_m or "",
                        "Year Built": r.year_built or "",
                        "Flag State": r.flag or "",
                        "Raw NMEA": r.raw_nmea or "",
                    }
                )

        return path

    @staticmethod
    def export_to_json(session: ObservationSession, records: List[ObservationRecord], output_path: str | Path) -> Path:
        """Export complete session with metadata and record payload to structured JSON."""
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "metadata": {
                "observation_id": session.session_id,
                "observer_station": session.station_id,
                "start_time_utc": session.start_time_utc.isoformat(),
                "end_time_utc": session.end_time_utc.isoformat() if session.end_time_utc else None,
                "observation_window": session.window_label,
                "recording_mode": session.recording_mode,
                "message_count": len(records),
                "target_mmsis": session.target_mmsis,
                "notes": session.notes,
            },
            "records": [
                {
                    "id": r.id,
                    "mmsi": r.mmsi,
                    "timestamp_utc": r.received_at_utc.isoformat(),
                    "ais_timestamp_utc": r.ais_timestamp_utc.isoformat() if r.ais_timestamp_utc else None,
                    "raw_nmea": r.raw_nmea,
                    "message_type": r.message_type,
                    "vessel": {
                        "name": r.ship_name,
                        "type": r.ship_type,
                        "imo": r.imo_number,
                        "call_sign": r.call_sign,
                        "particulars": {
                            "gross_tonnage_gt": r.gross_tonnage,
                            "deadweight_tonnes": r.deadweight_tonnes,
                            "length_m": r.length_overall_m,
                            "beam_m": r.beam_m,
                            "year_built": r.year_built,
                            "flag": r.flag,
                        },
                    },
                    "telemetry": {
                        "latitude": r.latitude,
                        "longitude": r.longitude,
                        "speed_knots": r.speed_knots,
                        "course_deg": r.course_deg,
                        "heading_deg": r.heading_deg,
                        "navigation_status": r.nav_status_label,
                        "navigation_status_code": r.nav_status_code,
                    },
                    "voyage": {
                        "draught_m": r.draught_meters,
                        "destination": r.destination,
                        "eta": r.eta,
                    },
                }
                for r in records
            ],
        }

        with open(path, mode="w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)

        return path
