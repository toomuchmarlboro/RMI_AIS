"""Unit tests for AIS Simulator."""
import asyncio
from src.sources.simulator import SimulatedVessel, SimulatorSource


def test_simulated_vessel_movement():
    config = {
        "mmsi": 525019876,
        "name": "Virgo 18",
        "initial_lat": -5.9320,
        "initial_lon": 105.9980,
        "initial_sog": 13.9,
        "initial_cog": 107.3,
        "initial_hdg": 109,
        "nav_status": 0,
        "gross_tonnage": 9989,
        "length": 134.0,
        "beam": 22.0,
    }
    v = SimulatedVessel(config)
    initial_lat = v.lat
    initial_lon = v.lon

    # Step simulation forward by 10 seconds
    v.step(dt_seconds=10.0)

    # Coordinates must have moved
    assert v.lat != initial_lat
    assert v.lon != initial_lon

    model = v.to_vessel_model()
    assert model.mmsi == 525019876
    assert model.particulars.gross_tonnage == 9989
    assert model.particulars.length_overall_m == 134.0


def test_simulator_stream():
    async def _async_test():
        vessels_cfg = [
            {
                "mmsi": 525019876,
                "name": "Virgo 18",
                "initial_lat": -5.9320,
                "initial_lon": 105.9980,
                "initial_sog": 13.9,
                "initial_cog": 107.3,
                "initial_hdg": 109,
                "nav_status": 0,
            }
        ]
        source = SimulatorSource(vessels_config=vessels_cfg, interval_sec=0.05)
        await source.start()

        messages = []
        async for raw_nmea, dyn, st in source.stream():
            messages.append((raw_nmea, dyn, st))
            if len(messages) >= 3:
                break

        await source.stop()

        assert len(messages) >= 3
        # Check that raw NMEA starts with standard !AIVDM
        raw_sentence = messages[0][0]
        assert raw_sentence.startswith("!AIVDM")
        # Check dynamic telemetry
        dyn = messages[0][1]
        assert dyn is not None
        assert dyn.mmsi == 525019876
        assert dyn.speed_knots == 13.9

    asyncio.run(_async_test())
