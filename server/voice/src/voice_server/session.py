from __future__ import annotations

import asyncio
import logging
import re
import sys
from typing import Any

from voice_server.audio.sentences import SentenceBuffer
from voice_server.compat import wait_for
from voice_server.protocol.messages import AbortMessage, ListenMessage, make_llm, make_stt, make_tts
from voice_server.protocol.state import SessionState, SessionStateMachine


_LOGGER = logging.getLogger(__name__)


class SessionLimitError(ValueError):
    close_code = 1009


def extract_music_query(text: str) -> str | None:
    """Return a song title when the utterance is an explicit play request."""
    cleaned = re.sub(r"[，。！？、,.!?；;：:]+$", "", text.strip())
    patterns = (
        r"^(?:请)?(?:播放|放)(?:歌曲|音乐)?(?P<query>.+)$",
        r"^(?:我想听|我想要听|我要听|想听|听一下|来一首|来点)(?:歌曲|音乐)?(?P<query>.+)$",
        r"^(?:请)?搜索(?:歌曲|音乐)?(?P<query>.+)$",
    )
    for pattern in patterns:
        match = re.match(pattern, cleaned, re.IGNORECASE)
        if match is None:
            continue
        query = match.group("query").strip().strip(" \"'“”‘’")
        query = re.sub(r"(?:这首歌|这首音乐)$", "", query).strip()
        if query and query not in {"音乐", "歌曲", "一首歌"}:
            return query
    return None


class VoiceSession:
    def __init__(
        self,
        *,
        device_id: str,
        session_id: str,
        transport: Any,
        codec: Any,
        asr: Any,
        llm: Any,
        tts: Any,
        memory: Any,
        vad: Any,
        min_silence_duration_ms: int,
        recent_limit: int,
        asr_timeout_s: float,
        llm_timeout_s: float,
        tts_timeout_s: float,
        max_binary_bytes: int,
        max_recording_bytes: int,
        max_recording_seconds: float,
        error_text: str,
        music: Any | None = None,
        music_search_timeout_s: float = 20.0,
    ) -> None:
        self.device_id = device_id
        self.session_id = session_id
        self._transport = transport
        self._codec = codec
        self._asr = asr
        self._llm = llm
        self._tts = tts
        self._memory = memory
        self._vad = vad
        self._min_silence_duration_ms = min_silence_duration_ms
        self._recent_limit = recent_limit
        self._asr_timeout_s = asr_timeout_s
        self._llm_timeout_s = llm_timeout_s
        self._tts_timeout_s = tts_timeout_s
        self._max_binary_bytes = max_binary_bytes
        self._max_recording_bytes = max_recording_bytes
        self._max_recording_seconds = max_recording_seconds
        self._error_text = error_text
        self._music = music
        self._music_search_timeout_s = music_search_timeout_s
        self._state_machine = SessionStateMachine()
        self._state_machine.transition(SessionState.IDLE)
        self._pcm = bytearray()
        self._recording_bytes = 0
        self._recording_seconds = 0.0
        self._listen_mode: str | None = None
        self._heard_speech = False
        self._silence_ms = 0.0
        self._pipeline_task: asyncio.Task[None] | None = None
        self._tts_started = False
        self._turn_generation = 0
        self._closed = False

    @property
    def state(self) -> SessionState:
        return self._state_machine.current

    async def handle_message(self, message: object) -> None:
        if self._closed:
            return
        if isinstance(message, AbortMessage):
            await self.abort()
            return
        if not isinstance(message, ListenMessage):
            return
        if message.state == "start":
            if message.mode == "manual":
                if self.state is SessionState.IDLE:
                    await self._prepare_turn(message.mode)
            else:
                await self.abort()
                if not self._closed:
                    await self._prepare_turn(message.mode)
        elif message.state == "stop" and self.state is SessionState.LISTENING:
            self._start_pipeline()

    async def handle_audio(self, packet: bytes) -> None:
        if self._closed or self.state is not SessionState.LISTENING:
            return
        generation = self._turn_generation
        if len(packet) > self._max_binary_bytes:
            raise SessionLimitError("audio packet exceeds limit")
        if self._recording_bytes + len(packet) > self._max_recording_bytes:
            raise SessionLimitError("recording exceeds byte limit")
        pcm = self._codec.decode_input(packet)
        duration = len(pcm) / (16_000 * 2)
        if self._recording_seconds + duration > self._max_recording_seconds:
            raise SessionLimitError("recording exceeds duration limit")
        self._pcm.extend(pcm)
        self._recording_bytes += len(packet)
        self._recording_seconds += duration
        if self._listen_mode not in {"auto", "realtime"}:
            return
        is_speech = await self._vad.is_speech(pcm, 16_000)
        if not self._is_current(generation) or self.state is not SessionState.LISTENING:
            return
        if is_speech:
            self._heard_speech = True
            self._silence_ms = 0.0
            return
        if not self._heard_speech:
            return
        self._silence_ms += duration * 1_000
        if self._silence_ms >= self._min_silence_duration_ms and self.state is SessionState.LISTENING:
            self._start_pipeline()

    async def abort(self) -> None:
        self._turn_generation += 1
        await self._cancel_pipeline()
        self._clear_recording()
        self._state_machine.abort()
        if not self._closed:
            await self._stop_lingering_tts()

    async def wait_until_idle(self) -> None:
        task = self._pipeline_task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._turn_generation += 1
        self._tts_started = False
        await self._cancel_pipeline()
        self._clear_recording()
        if self.state is not SessionState.CLOSED:
            self._state_machine.transition(SessionState.CLOSED)

    async def _run_pipeline(self, pcm: bytes, generation: int) -> None:
        tts_started = False
        tts_failed = False
        try:
            text = await wait_for(
                self._asr.transcribe(pcm, 16_000), self._asr_timeout_s
            )
            if not self._is_current(generation):
                return
            text = text.strip()
            if not text:
                return
            if not await self._send_json(generation, make_stt(self.session_id, text)):
                return
            await self._memory.remember(self.device_id, self.session_id, "user", text)
            if not self._is_current(generation):
                return
            music_query = extract_music_query(text)
            if music_query is not None and self._music is not None:
                _LOGGER.info("Music request from %s: %s", self.device_id, music_query)
                track = await wait_for(
                    self._music.search(music_query), self._music_search_timeout_s
                )
                if track is None:
                    raise RuntimeError(f"Music not found: {music_query}")
                await self._play_music(track, generation)
                if self._is_current(generation):
                    await self._memory.remember(
                        self.device_id, self.session_id, "assistant", f"正在播放：{track.title}"
                    )
                    self._memory.schedule_summary(self.device_id)
                return
            context = await self._memory.recall(self.device_id, text, self._recent_limit)
            if not self._is_current(generation):
                return
            self._state_machine.transition(SessionState.THINKING)
            messages = self._make_messages(context, text)
            sentences = SentenceBuffer()
            assistant_chunks: list[str] = []
            iterator = self._llm.stream(messages).__aiter__()
            remaining_llm_s = self._llm_timeout_s
            loop = asyncio.get_running_loop()
            try:
                while True:
                    wait_started = loop.time()
                    try:
                        chunk = await wait_for(anext(iterator), remaining_llm_s)
                    except StopAsyncIteration:
                        break
                    finally:
                        remaining_llm_s -= loop.time() - wait_started
                    if not self._is_current(generation):
                        return
                    assistant_chunks.append(chunk)
                    for sentence in sentences.feed(chunk):
                        try:
                            tts_started = await self._speak_sentence(sentence, tts_started, generation, display=True)
                        except Exception:
                            tts_failed = True
                            raise
                        if not self._is_current(generation):
                            return
            finally:
                primary_error = sys.exc_info()[1]
                close_iterator = getattr(iterator, "aclose", None)
                if close_iterator is not None:
                    try:
                        await wait_for(close_iterator(), self._llm_timeout_s)
                    except BaseException:
                        if primary_error is None:
                            raise
            for sentence in sentences.flush():
                try:
                    tts_started = await self._speak_sentence(sentence, tts_started, generation, display=True)
                except Exception:
                    tts_failed = True
                    raise
                if not self._is_current(generation):
                    return
            assistant_text = "".join(assistant_chunks)
            if tts_started and self._is_current(generation):
                stop_is_current = await self._send_json(generation, make_tts(self.session_id, "stop"))
                self._tts_started = False
                tts_started = False
                if not stop_is_current:
                    return
                if not self._is_current(generation):
                    return
                await self._memory.remember(self.device_id, self.session_id, "assistant", assistant_text)
                self._memory.schedule_summary(self.device_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            if not tts_failed and self._is_current(generation):
                try:
                    if self.state is SessionState.RECOGNIZING:
                        self._state_machine.transition(SessionState.THINKING)
                    tts_started = await self._speak_sentence(self._error_text, tts_started, generation, display=False)
                    if tts_started and self._is_current(generation):
                        await self._send_json(generation, make_tts(self.session_id, "stop"))
                        self._tts_started = False
                        tts_started = False
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
        finally:
            if self._tts_started and self._is_current(generation):
                try:
                    await self._send_json(generation, make_tts(self.session_id, "stop"))
                except Exception:
                    pass
                else:
                    self._tts_started = False
            if self._is_current(generation):
                self._state_machine.abort()
                self._clear_recording()

    def _make_messages(self, context: Any, current_text: str) -> list[dict[str, str]]:
        summary = context.summary.strip() if context.summary else ""
        messages = [{"role": "system", "content": summary or "No conversation summary is available."}]
        recent = list(context.recent_messages)
        if recent and (
            recent[-1].session_id == self.session_id
            and recent[-1].role == "user"
            and recent[-1].content == current_text
        ):
            recent.pop()
        messages.extend({"role": item.role, "content": item.content} for item in recent)
        messages.append({"role": "user", "content": current_text})
        return messages

    async def _speak_sentence(self, sentence: str, started: bool, generation: int, *, display: bool) -> bool:
        if not self._is_current(generation):
            return started
        if not started:
            if self.state is not SessionState.SPEAKING:
                self._state_machine.transition(SessionState.SPEAKING)
            if display and not await self._send_json(generation, make_llm(self.session_id, "\U0001f642", "happy")):
                return started
            self._tts_started = True
            if not await self._send_json(generation, make_tts(self.session_id, "start")):
                return started
            started = True
        if not await self._send_json(generation, make_tts(self.session_id, "sentence_start", sentence)):
            return started
        sent_packets = 0
        iterator = self._tts.synthesize(sentence).__aiter__()
        while True:
            try:
                pcm = await wait_for(anext(iterator), self._tts_timeout_s)
            except StopAsyncIteration:
                break
            for packet in self._codec.encode_output(pcm):
                if not await self._send_bytes(generation, packet):
                    return started
                sent_packets += 1
        if sent_packets == 0:
            raise RuntimeError("TTS produced no audio packets")
        return started

    async def _play_music(self, track: Any, generation: int) -> None:
        if not self._is_current(generation):
            return
        self._state_machine.transition(SessionState.SPEAKING)
        if not await self._send_json(generation, make_llm(self.session_id, track.title, "relaxed")):
            return
        if not await self._send_json(generation, make_tts(self.session_id, "start")):
            return
        self._tts_started = True
        if not await self._send_json(
            generation, make_tts(self.session_id, "sentence_start", f"正在播放：{track.title}")
        ):
            return

        sent_packets = 0
        async for pcm in self._music.stream_pcm(track):
            if not self._is_current(generation):
                return
            for packet in self._codec.encode_output(pcm):
                if not await self._send_bytes(generation, packet):
                    return
                sent_packets += 1
        if sent_packets == 0:
            raise RuntimeError("Music provider produced no audio")
        if await self._send_json(generation, make_tts(self.session_id, "stop")):
            self._tts_started = False

    async def _cancel_pipeline(self) -> None:
        task = self._pipeline_task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _clear_recording(self) -> None:
        self._pcm.clear()
        self._recording_bytes = 0
        self._recording_seconds = 0.0
        self._listen_mode = None
        self._heard_speech = False
        self._silence_ms = 0.0

    def _start_pipeline(self) -> None:
        self._state_machine.transition(SessionState.RECOGNIZING)
        self._pipeline_task = asyncio.create_task(self._run_pipeline(bytes(self._pcm), self._turn_generation))

    def _open_turn(self, mode: str) -> None:
        assert not self._tts_started
        self._turn_generation += 1
        self._clear_recording()
        self._listen_mode = mode
        self._state_machine.transition(SessionState.LISTENING)

    async def _prepare_turn(self, mode: str) -> None:
        if await self._stop_lingering_tts():
            self._open_turn(mode)

    async def _stop_lingering_tts(self) -> bool:
        if not self._tts_started:
            return True
        if not await self._send_json(self._turn_generation, make_tts(self.session_id, "stop")):
            return False
        self._tts_started = False
        return True

    def _is_current(self, generation: int) -> bool:
        return not self._closed and generation == self._turn_generation

    async def _send_json(self, generation: int, message: dict[str, object]) -> bool:
        if not self._is_current(generation):
            return False
        await self._transport.send_json(message)
        return self._is_current(generation)

    async def _send_bytes(self, generation: int, packet: bytes) -> bool:
        if not self._is_current(generation):
            return False
        await self._transport.send_bytes(packet)
        return self._is_current(generation)
