"""Builds DataSource instances from config.yaml + environment (Rule A01).

Shared by the CLI entry point and the dashboard's live source switcher so both
construct sources identically.
"""
from __future__ import annotations

import os
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from src.sources.base import DataSource
from src.sources.simulator import SimulatorSource
from src.sources.aisstream import AisStreamSource
from src.sources.vesselfinder import VesselFinderSource

logger = logging.getLogger(__name__)

SUPPORTED_MODES = ("simulator", "aisstream", "vesselfinder")


def load_yaml_config(config_path: str | Path = "config.yaml") -> Dict[str, Any]:
    """Load config.yaml, returning an empty dict when absent."""
    path = Path(config_path)
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def parse_mmsi_list(raw: Any) -> List[int]:
    """Coerce a comma/space separated string or sequence into valid MMSIs."""
    if not raw:
        return []
    items = raw.replace(",", " ").split() if isinstance(raw, str) else list(raw)

    mmsis: List[int] = []
    for item in items:
        try:
            mmsi = int(str(item).strip())
        except (TypeError, ValueError):
            logger.warning(f"Ignoring non-numeric MMSI entry: {item!r}")
            continue
        if 100_000_000 <= mmsi <= 999_999_999:
            mmsis.append(mmsi)
        else:
            logger.warning(f"Ignoring out-of-range MMSI (expected 9 digits): {mmsi}")
    return mmsis


def build_simulator(cfg: Dict[str, Any]) -> SimulatorSource:
    sim_cfg = cfg.get("simulator", {}) or {}
    return SimulatorSource(
        vessels_config=sim_cfg.get("vessels", []),
        interval_sec=float(sim_cfg.get("update_interval_sec", 3.0)),
    )


def build_aisstream(
    cfg: Dict[str, Any],
    api_key: Optional[str] = None,
    bounding_boxes: Optional[List] = None,
) -> AisStreamSource:
    ais_cfg = cfg.get("aisstream", {}) or {}
    key = (api_key or os.getenv("AISSTREAM_API_KEY", "")).strip()
    if not key:
        raise ValueError("aisstream.io requires an API key (AISSTREAM_API_KEY).")

    boxes = bounding_boxes or ais_cfg.get("bounding_boxes") or [[[-90.0, -180.0], [90.0, 180.0]]]
    return AisStreamSource(
        api_key=key,
        bounding_boxes=boxes,
        url=ais_cfg.get("url", "wss://stream.aisstream.io/v0/stream"),
    )


def build_vesselfinder(
    cfg: Dict[str, Any],
    target_mmsis: Optional[List[int]] = None,
) -> VesselFinderSource:
    """Build the VesselFinder scraper source.

    MMSI watchlist precedence: explicit argument, then
    ``VESSELFINDER_TARGET_MMSIS``, then ``vesselfinder.target_mmsis``, then the
    simulator preset fleet (so the dashboard has something to poll out of the box).
    """
    vf_cfg = cfg.get("vesselfinder", {}) or {}

    mmsis = parse_mmsi_list(target_mmsis)
    if not mmsis:
        mmsis = parse_mmsi_list(os.getenv("VESSELFINDER_TARGET_MMSIS", ""))
    if not mmsis:
        mmsis = parse_mmsi_list(vf_cfg.get("target_mmsis"))
    if not mmsis:
        mmsis = parse_mmsi_list([v.get("mmsi") for v in (cfg.get("simulator", {}) or {}).get("vessels", [])])
        if mmsis:
            logger.info("No VesselFinder watchlist configured; falling back to the preset fleet MMSIs.")

    interval = float(os.getenv("VESSELFINDER_POLL_INTERVAL_SEC", 0) or vf_cfg.get("poll_interval_sec", 120.0))

    return VesselFinderSource(
        target_mmsis=mmsis,
        poll_interval_sec=interval,
        enrich_details=bool(vf_cfg.get("enrich_details", True)),
        include_approximate_position=bool(vf_cfg.get("include_approximate_position", True)),
        min_request_interval_sec=float(vf_cfg.get("min_request_interval_sec", 5.0)),
    )


def build_source(
    mode: str,
    cfg: Optional[Dict[str, Any]] = None,
    *,
    api_key: Optional[str] = None,
    bounding_boxes: Optional[List] = None,
    target_mmsis: Optional[List[int]] = None,
) -> DataSource:
    """Instantiate the DataSource named by ``mode``.

    Raises ``ValueError`` for an unknown mode or missing credentials.
    """
    cfg = cfg if cfg is not None else load_yaml_config()
    normalized = (mode or "").strip().lower()

    if normalized == "simulator":
        return build_simulator(cfg)
    if normalized == "aisstream":
        return build_aisstream(cfg, api_key=api_key, bounding_boxes=bounding_boxes)
    if normalized == "vesselfinder":
        return build_vesselfinder(cfg, target_mmsis=target_mmsis)

    raise ValueError(f"Unsupported data source mode: {mode!r}. Expected one of {', '.join(SUPPORTED_MODES)}.")
