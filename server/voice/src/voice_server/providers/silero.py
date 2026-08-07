from pathlib import Path
from typing import Any

import numpy as np


class SileroVADProvider:
    def __init__(
        self,
        model_path: str | Path,
        *,
        speech_threshold: float = 0.5,
        silence_threshold: float = 0.3,
        inference_session: Any | None = None,
    ) -> None:
        self._speech_threshold = speech_threshold
        self._silence_threshold = silence_threshold
        self._session = inference_session or self._create_session(model_path)
        self._buffer = np.empty(0, dtype=np.float32)
        self._context = np.zeros(64, dtype=np.float32)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._is_speaking = False

    @staticmethod
    def _create_session(model_path: str | Path) -> Any:
        import onnxruntime

        options = onnxruntime.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        onnx_path = Path(model_path) / "src" / "silero_vad" / "data" / "silero_vad.onnx"
        return onnxruntime.InferenceSession(str(onnx_path), sess_options=options)

    async def is_speech(self, pcm_chunk: bytes, sample_rate: int) -> bool:
        if sample_rate != 16000:
            raise ValueError("Silero VAD requires 16000 Hz PCM audio")
        if len(pcm_chunk) % 2:
            raise ValueError("int16 PCM must contain an even number of bytes")

        samples = np.frombuffer(pcm_chunk, dtype="<i2").astype(np.float32) / 32768
        self._buffer = np.concatenate((self._buffer, samples))

        while len(self._buffer) >= 512:
            chunk, self._buffer = self._buffer[:512], self._buffer[512:]
            model_input = np.concatenate((self._context, chunk))[None, :]
            probability, self._state = self._session.run(
                None,
                {
                    "input": model_input,
                    "state": self._state,
                    "sr": np.array(sample_rate, dtype=np.int64),
                },
            )
            self._context = chunk[-64:].copy()
            score = float(np.asarray(probability).reshape(-1)[0])
            if score >= self._speech_threshold:
                self._is_speaking = True
            elif score <= self._silence_threshold or np.isclose(
                score, self._silence_threshold, rtol=0.0, atol=1e-7
            ):
                self._is_speaking = False

        return self._is_speaking
