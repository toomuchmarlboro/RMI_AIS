"""NMEA sentence models and validation utilities."""
from __future__ import annotations

import re
from typing import Optional
from datetime import datetime, timezone
from pydantic import BaseModel, Field


class NMEASentence(BaseModel):
    """Encapsulates a single NMEA 0183 AIVDM / AIVDO sentence."""
    raw_sentence: str
    talker: str = Field(..., description="Sentence talker prefix, e.g. !AIVDM or !AIVDO")
    total_fragments: int = 1
    fragment_number: int = 1
    message_id: Optional[str] = None
    radio_channel: Optional[str] = None
    payload: str
    fill_bits: int = 0
    checksum: str
    received_at_utc: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @staticmethod
    def calculate_checksum(sentence_body: str) -> str:
        """Calculate standard NMEA XOR checksum for content between '!' or '$' and '*'."""
        # Strip leading ! or $ and trailing *XX if present
        clean = sentence_body.strip()
        if clean.startswith(("!", "$")):
            clean = clean[1:]
        if "*" in clean:
            clean = clean.split("*")[0]

        csum = 0
        for char in clean:
            csum ^= ord(char)
        return f"{csum:02X}"

    @classmethod
    def parse_raw(cls, raw: str) -> NMEASentence:
        """Parse and validate a raw NMEA sentence string."""
        raw = raw.strip()
        if not raw:
            raise ValueError("Empty NMEA sentence")

        if not (raw.startswith("!") or raw.startswith("$")):
            raise ValueError(f"Invalid NMEA start character: {raw[:1]}")

        if "*" not in raw:
            raise ValueError("Missing NMEA checksum asterisk delimiter")

        body, checksum = raw.split("*", 1)
        expected_cs = cls.calculate_checksum(body)
        if checksum.strip().upper() != expected_cs:
            raise ValueError(f"NMEA Checksum mismatch: expected {expected_cs}, got {checksum.strip()}")

        fields = body[1:].split(",")
        if len(fields) < 6:
            raise ValueError(f"Insufficient NMEA fields: {len(fields)}")

        talker = body[:6]
        total_fragments = int(fields[1]) if fields[1] else 1
        fragment_number = int(fields[2]) if fields[2] else 1
        message_id = fields[3] if fields[3] else None
        radio_channel = fields[4] if fields[4] else "A"
        payload = fields[5]
        fill_bits = int(fields[6]) if len(fields) > 6 and fields[6] else 0

        return cls(
            raw_sentence=raw,
            talker=talker,
            total_fragments=total_fragments,
            fragment_number=fragment_number,
            message_id=message_id,
            radio_channel=radio_channel,
            payload=payload,
            fill_bits=fill_bits,
            checksum=checksum.strip().upper(),
        )
