"""NMEA sentence parser and multi-fragment sentence reassembly."""
from __future__ import annotations

import logging
from typing import Dict, Optional, Tuple, List
from src.models.nmea import NMEASentence

logger = logging.getLogger(__name__)


class MultiSentenceAssembler:
    """Buffers and reconstructs multi-sentence NMEA AIVDM/AIVDO payloads."""

    def __init__(self, max_buffer_size: int = 500):
        # Buffer keyed by (sequential_message_id, radio_channel)
        self._buffer: Dict[Tuple[str, str], List[NMEASentence]] = {}
        self._max_buffer_size = max_buffer_size

    def process(self, sentence: NMEASentence) -> Optional[List[NMEASentence]]:
        """
        Takes an NMEASentence.
        If single-fragment (total_fragments == 1), returns [sentence].
        If multi-fragment, buffers and returns list of all fragments once complete, or None if awaiting more.
        """
        if sentence.total_fragments <= 1:
            return [sentence]

        msg_id = sentence.message_id or "default"
        channel = sentence.radio_channel or "A"
        key = (msg_id, channel)

        if key not in self._buffer:
            # Enforce max buffer size to avoid unbounded memory
            if len(self._buffer) >= self._max_buffer_size:
                oldest_key = next(iter(self._buffer))
                del self._buffer[oldest_key]
            self._buffer[key] = []

        fragments = self._buffer[key]
        fragments.append(sentence)

        # Check if we have received all fragments
        if len(fragments) == sentence.total_fragments:
            # Verify ordering
            fragments.sort(key=lambda s: s.fragment_number)
            expected_numbers = list(range(1, sentence.total_fragments + 1))
            actual_numbers = [s.fragment_number for s in fragments]
            del self._buffer[key]

            if actual_numbers == expected_numbers:
                return fragments
            else:
                logger.warning(
                    f"Fragments sequence corrupted for message id {msg_id}: expected {expected_numbers}, got {actual_numbers}"
                )
                return None

        return None


class NMEAParser:
    """Validates raw NMEA lines and handles sentence reassembly."""

    def __init__(self):
        self._assembler = MultiSentenceAssembler()

    def parse_line(self, line: str) -> Tuple[Optional[NMEASentence], Optional[List[NMEASentence]]]:
        """
        Parses a single raw NMEA line.
        Returns:
            (single_parsed_sentence, completed_fragment_list_if_ready)
        """
        line = line.strip()
        if not line:
            return None, None

        try:
            sentence = NMEASentence.parse_raw(line)
        except Exception as e:
            logger.debug(f"Failed to parse NMEA sentence '{line}': {e}")
            return None, None

        completed = self._assembler.process(sentence)
        return sentence, completed
