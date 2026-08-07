from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import Callable
from typing import Any

from websockets.exceptions import ConnectionClosed

from voice_server.config import AppConfig
from voice_server.compat import wait_for
from voice_server.protocol.messages import HelloMessage, ProtocolError, make_server_hello, parse_client_message
from voice_server.session import SessionLimitError, VoiceSession


_LOGGER = logging.getLogger(__name__)
_VOICE_PATH = "/xiaozhi/v1/"


class WebSocketTransport:
    """The small transport surface used by a voice session."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._send_lock = asyncio.Lock()

    async def send_json(self, message: dict[str, object]) -> None:
        async with self._send_lock:
            await self._connection.send(json.dumps(message, ensure_ascii=False))

    async def send_bytes(self, packet: bytes) -> None:
        async with self._send_lock:
            await self._connection.send(packet)

    async def close(self, code: int) -> None:
        await self._connection.close(code=code)


class VoiceWebSocketServer:
    def __init__(
        self,
        *,
        config: AppConfig,
        authenticator: Any,
        asr: Any,
        llm: Any,
        tts: Any,
        memory: Any,
        codec_factory: Callable[[], Any],
        vad_factory: Callable[[], Any],
        session_factory: Callable[..., Any] = VoiceSession,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.config = config
        self._authenticator = authenticator
        self._asr = asr
        self._llm = llm
        self._tts = tts
        self._memory = memory
        self._codec_factory = codec_factory
        self._vad_factory = vad_factory
        self._session_factory = session_factory
        self._id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._active_sessions: dict[int, Any] = {}

    @property
    def active_session_count(self) -> int:
        return len(self._active_sessions)

    async def handle_connection(self, connection: Any) -> None:
        if connection.request.path != _VOICE_PATH:
            await connection.close(code=1008)
            return

        headers = connection.request.headers
        device_ids = headers.get_all("Device-Id")
        authorizations = headers.get_all("Authorization")
        if len(device_ids) != 1 or len(authorizations) > 1:
            await connection.close(code=1008)
            return
        device_id = device_ids[0]
        if not device_id.strip():
            await connection.close(code=1008)
            return
        authorization = authorizations[0] if authorizations else None
        try:
            authenticated = self._authenticator.authenticate(device_id, authorization)
        except Exception:
            authenticated = False
        if not authenticated:
            await connection.close(code=1008)
            return

        try:
            hello = await self._receive_hello(connection)
        except _CloseConnection as error:
            await connection.close(code=error.code)
            return
        except ConnectionClosed:
            return

        session: Any | None = None
        try:
            transport = WebSocketTransport(connection)
            session_id = self._id_factory()
            session = self._session_factory(
                device_id=device_id,
                session_id=session_id,
                transport=transport,
                codec=self._codec_factory(),
                asr=self._asr,
                llm=self._llm,
                tts=self._tts,
                memory=self._memory,
                vad=self._vad_factory(),
                min_silence_duration_ms=self.config.vad.min_silence_duration_ms,
                recent_limit=self.config.memory.recent_limit,
                asr_timeout_s=self.config.asr.timeout_s,
                llm_timeout_s=self.config.llm.timeout_s,
                tts_timeout_s=self.config.tts.timeout_s,
                max_binary_bytes=self.config.server.max_binary_bytes,
                max_recording_bytes=self.config.server.max_recording_bytes,
                max_recording_seconds=self.config.server.max_recording_seconds,
                error_text=self.config.tts.error_text,
            )
            self._active_sessions[id(session)] = session
            await transport.send_json(make_server_hello(session_id))
            await self._serve_messages(connection, session)
        except _CloseConnection as error:
            await connection.close(code=error.code)
        except ConnectionClosed:
            pass
        except Exception:
            _LOGGER.exception("Unhandled voice WebSocket connection failure")
            try:
                await connection.close(code=1011)
            except ConnectionClosed:
                pass
        finally:
            if session is not None:
                self._active_sessions.pop(id(session), None)
                try:
                    await session.close()
                except Exception:
                    _LOGGER.exception("Failed to close voice session")

    async def close_active_sessions(self) -> None:
        sessions = tuple(self._active_sessions.values())
        self._active_sessions.clear()
        results = await asyncio.gather(*(session.close() for session in sessions), return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                _LOGGER.exception("Failed to close voice session", exc_info=result)

    async def close(self) -> None:
        await self.close_active_sessions()

    async def _receive_hello(self, connection: Any) -> HelloMessage:
        try:
            raw = await wait_for(
                connection.recv(), self.config.server.hello_timeout_s
            )
        except TimeoutError as error:
            raise _CloseConnection(1002) from error
        message = self._parse_text(raw)
        if not isinstance(message, HelloMessage):
            raise _CloseConnection(1002)
        return message

    async def _serve_messages(self, connection: Any, session: Any) -> None:
        while True:
            try:
                raw = await wait_for(
                    connection.recv(), self.config.server.idle_timeout_s
                )
            except TimeoutError as error:
                raise _CloseConnection(1000) from error
            if isinstance(raw, bytes):
                if len(raw) > self.config.server.max_binary_bytes:
                    raise _CloseConnection(1009)
                try:
                    await session.handle_audio(raw)
                except SessionLimitError as error:
                    raise _CloseConnection(1009) from error
                continue
            message = self._parse_text(raw)
            if isinstance(message, HelloMessage):
                raise _CloseConnection(1002)
            try:
                await session.handle_message(message)
            except SessionLimitError as error:
                raise _CloseConnection(1009) from error

    def _parse_text(self, raw: object) -> object:
        if not isinstance(raw, str):
            raise _CloseConnection(1002)
        try:
            encoded = raw.encode("utf-8")
        except UnicodeEncodeError as error:
            raise _CloseConnection(1002) from error
        if len(encoded) > self.config.server.max_text_bytes:
            raise _CloseConnection(1009)
        try:
            return parse_client_message(raw)
        except ProtocolError as error:
            raise _CloseConnection(error.close_code) from error


class _CloseConnection(Exception):
    def __init__(self, code: int) -> None:
        self.code = code
