import math
import struct

import pytest

from voice_server.audio.opus import AudioCodecError, OpusCodec

import opuslib_next


def pcm_sine(sample_rate: int, samples: int) -> bytes:
    return b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * index / sample_rate)))
        for index in range(samples)
    )


def test_decodes_one_v1_input_packet():
    """Would fail if raw v1 input was decoded at the wrong frame size or rate."""
    pcm = pcm_sine(16000, 960)
    packet = opuslib_next.Encoder(16000, 1, opuslib_next.APPLICATION_AUDIO).encode(pcm, 960)

    decoded = OpusCodec().decode_input(packet)

    assert len(decoded) == 960 * 2


def test_encodes_24k_pcm_in_60ms_packets():
    """Would fail if 24 kHz output was not split into 60 ms Opus frames."""
    pcm = pcm_sine(24000, 2880)

    packets = OpusCodec().encode_output(pcm)

    assert len(packets) == 2
    decoder = opuslib_next.Decoder(24000, 1)
    assert all(len(decoder.decode(packet, 1440)) == 2880 for packet in packets)


def test_pads_only_a_nonempty_final_output_frame():
    """Would fail if a partial PCM tail were dropped instead of encoded."""
    codec = OpusCodec()
    packets = codec.encode_output(pcm_sine(24000, 1441))

    assert len(packets) == 2


def test_empty_output_pcm_emits_no_packet():
    """Would fail if silence was emitted for an absent output payload."""
    assert OpusCodec().encode_output(b"") == []


def test_invalid_input_packet_raises_audio_codec_error():
    """Would fail if native Opus decode errors leaked through the audio boundary."""
    with pytest.raises(AudioCodecError):
        OpusCodec().decode_input(b"not an opus packet")
