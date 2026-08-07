import os


_OPUS_DLL_DIR = os.environ.get("VOICE_OPUS_DLL_DIR")
if _OPUS_DLL_DIR:
    os.environ["PATH"] = _OPUS_DLL_DIR + os.pathsep + os.environ.get("PATH", "")

import opuslib_next


class AudioCodecError(RuntimeError):
    pass


class OpusCodec:
    """Stateful raw-Opus encoder and decoder for the v1 wire format."""

    _INPUT_FRAME_SAMPLES = 960
    _OUTPUT_FRAME_SAMPLES = 1440
    _OUTPUT_FRAME_BYTES = _OUTPUT_FRAME_SAMPLES * 2

    def __init__(self) -> None:
        self._input_decoder = opuslib_next.Decoder(16000, 1)
        self._output_encoder = opuslib_next.Encoder(
            24000, 1, opuslib_next.APPLICATION_AUDIO
        )

    def decode_input(self, packet: bytes) -> bytes:
        try:
            return self._input_decoder.decode(packet, self._INPUT_FRAME_SAMPLES)
        except opuslib_next.OpusError as error:
            raise AudioCodecError(str(error)) from error

    def encode_output(self, pcm: bytes) -> list[bytes]:
        if not pcm:
            return []

        packets = []
        for offset in range(0, len(pcm), self._OUTPUT_FRAME_BYTES):
            frame = pcm[offset : offset + self._OUTPUT_FRAME_BYTES]
            if len(frame) < self._OUTPUT_FRAME_BYTES:
                frame += b"\x00" * (self._OUTPUT_FRAME_BYTES - len(frame))
            try:
                packets.append(self._output_encoder.encode(frame, self._OUTPUT_FRAME_SAMPLES))
            except opuslib_next.OpusError as error:
                raise AudioCodecError(str(error)) from error
        return packets
