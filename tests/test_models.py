"""Unit tests for core models and NMEA validation."""
import pytest
from datetime import datetime, timezone
from src.models.vessel import NavigationStatus, VesselParticulars, VesselDynamic, VesselStatic, Vessel
from src.models.nmea import NMEASentence
from src.models.observation import ObservationSession, ObservationRecord


def test_navigation_status_labels():
    assert NavigationStatus.label_for(0) == "Under way using engine"
    assert NavigationStatus.label_for(5) == "Moored"
    assert NavigationStatus.label_for(8) == "Sailing"
    assert NavigationStatus.label_for(15) == "Undefined"
    assert NavigationStatus.label_for(None) == "Undefined"


def test_vessel_particulars():
    p = VesselParticulars(
        gross_tonnage=9989,
        deadweight_tonnes=3500,
        length_overall_m=134.0,
        beam_m=22.0,
        year_built=2015,
        flag="Indonesia",
    )
    assert p.gross_tonnage == 9989
    assert p.length_overall_m == 134.0
    assert p.beam_m == 22.0


def test_vessel_dynamic_and_static_update():
    v = Vessel(mmsi=525019876, name="Virgo 18")
    dyn = VesselDynamic(
        mmsi=525019876,
        latitude=-5.9320,
        longitude=105.9980,
        speed_knots=13.9,
        course_deg=107.3,
        heading_deg=109.0,
        nav_status_code=0,
        nav_status_label="Under way using engine",
    )
    v.update_dynamic(dyn)

    assert v.latitude == -5.9320
    assert v.speed_knots == 13.9
    assert v.course_deg == 107.3
    assert v.heading_deg == 109.0
    assert v.nav_status_label == "Under way using engine"

    st = VesselStatic(
        mmsi=525019876,
        ship_name="VIRGO 18",
        ship_type="Passenger/Ro-Ro Cargo Ship",
        draught_meters=5.2,
        destination="MERAK",
        dimension_to_bow_m=100.0,
        dimension_to_stern_m=34.0,
        dimension_to_port_m=10.0,
        dimension_to_starboard_m=12.0,
    )
    v.update_static(st)

    assert v.name == "VIRGO 18"
    assert v.draught_meters == 5.2
    assert v.destination == "MERAK"
    assert v.particulars.length_overall_m == 134.0
    assert v.particulars.beam_m == 22.0


def test_nmea_sentence_checksum():
    body = "AIVDM,1,1,,B,19NWrrP02sbuuuuhM86hA0=n2<0:,0"
    expected_cs = NMEASentence.calculate_checksum(body)
    raw = f"!{body}*{expected_cs}"

    sentence = NMEASentence.parse_raw(raw)
    assert sentence.talker == "!AIVDM"
    assert sentence.checksum == expected_cs
    assert sentence.total_fragments == 1
    assert sentence.fragment_number == 1


def test_nmea_sentence_bad_checksum():
    with pytest.raises(ValueError, match="Checksum mismatch"):
        NMEASentence.parse_raw("!AIVDM,1,1,,B,19NWrrP02sbuuuuhM86hA0=n2<0:,0*99")


def test_observation_session_window():
    sess = ObservationSession(
        session_id="OBS-20261012-001",
        station_id="Station-Merak-01",
        start_time_utc=datetime(2026, 10, 12, 23, 0, 0, tzinfo=timezone.utc),
        end_time_utc=datetime(2026, 10, 12, 23, 18, 0, tzinfo=timezone.utc),
    )
    assert sess.window_label == "23:00:00 — 23:18:00"
    assert not sess.is_active
