"""Unit tests for NMEA parser and AIS decoder."""
from src.decoder.nmea_parser import NMEAParser, MultiSentenceAssembler
from src.decoder.ais_decoder import AISDecoder
from src.models.nmea import NMEASentence


def test_nmea_parser_single_sentence():
    parser = NMEAParser()
    raw = "!AIVDM,1,1,,B,19NWrrP02sbuuuuhM86hA0=n2<0:,0*12"
    sentence, completed = parser.parse_line(raw)

    assert sentence is not None
    assert completed is not None
    assert len(completed) == 1
    assert completed[0].raw_sentence == raw


def test_multi_sentence_assembler():
    assembler = MultiSentenceAssembler()
    # Create 2-part message
    s1 = NMEASentence(
        raw_sentence="!AIVDM,2,1,9,B,PART1,0*00",
        talker="!AIVDM",
        total_fragments=2,
        fragment_number=1,
        message_id="9",
        radio_channel="B",
        payload="PART1",
        checksum="00",
    )
    s2 = NMEASentence(
        raw_sentence="!AIVDM,2,2,9,B,PART2,2*00",
        talker="!AIVDM",
        total_fragments=2,
        fragment_number=2,
        message_id="9",
        radio_channel="B",
        payload="PART2",
        checksum="00",
    )

    res1 = assembler.process(s1)
    assert res1 is None  # Waiting for part 2

    res2 = assembler.process(s2)
    assert res2 is not None
    assert len(res2) == 2
    assert res2[0].fragment_number == 1
    assert res2[1].fragment_number == 2


def test_ais_decoder_type1():
    # Authentic Type 1 position report
    raw = "!AIVDM,1,1,,B,19NWrrP02sbuuuuhM86hA0=n2<0:,0*12"
    msg_type, mmsi, dyn, st = AISDecoder.decode_sentences([raw])

    assert msg_type == 1
    assert mmsi == 636091114
    assert dyn is not None
    assert st is None
    assert dyn.speed_knots == 18.7
    assert dyn.course_deg == 6.8
    assert dyn.nav_status_code == 0
    assert dyn.nav_status_label == "Under way using engine"
