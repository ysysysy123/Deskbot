from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import websockets
from aiohttp import web

from voice_server.admin_api import create_admin_app
from voice_server.audio.opus import OpusCodec
from voice_server.auth import build_authenticator
from voice_server.config import AppConfig
from voice_server.memory.service import MemoryService
from voice_server.memory.sqlite import SQLiteMemoryProvider
from voice_server.music import MusicProvider
from voice_server.ota import create_ota_app
from voice_server.providers.edge_tts import EdgeTTSProvider
from voice_server.providers.openai_compatible import OpenAICompatibleLLMProvider
from voice_server.providers.sensevoice import SenseVoiceASRProvider
from voice_server.providers.silero import SileroVADProvider
from voice_server.websocket_server import VoiceWebSocketServer


_LOGGER = logging.getLogger(__name__)
ListenerFactory = Callable[[], Awaitable[Any]]


class ServerApplication:
    def __init__(
        self,
        *,
        config: AppConfig,
        memory_store: Any,
        memory_service: Any,
        websocket_server: Any,
        voice_listener_factory: ListenerFactory,
        ota_listener_factory: ListenerFactory,
        admin_listener_factory: ListenerFactory,
        provider_resources: Sequence[Any] = (),
    ) -> None:
        self.config = config
        self._memory_store = memory_store
        self._memory_service = memory_service
        self._websocket_server = websocket_server
        self._listener_factories = (
            voice_listener_factory,
            ota_listener_factory,
            admin_listener_factory,
        )
        self._provider_resources = tuple(provider_resources)
        self._listeners: list[Any] = []
        self._lifecycle_lock = asyncio.Lock()
        self._started = False
        self._stopping = False
        self._stopped = False
        self._shutdown_task: asyncio.Task[Exception | None] | None = None
        self._shutdown_error_observed = False

    @classmethod
    def from_config(cls, config: AppConfig) -> "ServerApplication":
        memory_store = SQLiteMemoryProvider(Path(config.memory.database_path))
        asr = SenseVoiceASRProvider.from_model_path(
            config.asr.model_path, max_concurrency=config.asr.max_concurrency
        )
        llm = OpenAICompatibleLLMProvider(
            base_url=config.llm.base_url,
            model=config.llm.model,
            api_key=config.llm.api_key,
            temperature=config.llm.temperature,
            max_tokens=config.llm.max_tokens,
            timeout_s=config.llm.timeout_s,
        )
        tts = EdgeTTSProvider(
            voice=config.tts.voice,
            rate=config.tts.rate,
            volume=config.tts.volume,
        )
        music = MusicProvider(
            ffmpeg_path=config.music.ffmpeg_path,
            max_duration_s=config.music.max_duration_s,
            netease_api_url=config.music.netease_api_url,
        ) if config.music.enabled else None
        memory_service = MemoryService(
            memory_store,
            memory_store,
            llm,
            config.memory.summary_threshold,
            config.llm.summary_max_tokens,
        )
        websocket_server = VoiceWebSocketServer(
            config=config,
            authenticator=build_authenticator(config.auth),
            asr=asr,
            llm=llm,
            tts=tts,
            memory=memory_service,
            codec_factory=OpusCodec,
            vad_factory=lambda: SileroVADProvider(
                config.vad.model_path,
                speech_threshold=config.vad.speech_threshold,
                silence_threshold=config.vad.silence_threshold,
            ),
            music=music,
        )
        return cls(
            config=config,
            memory_store=memory_store,
            memory_service=memory_service,
            websocket_server=websocket_server,
            voice_listener_factory=lambda: websockets.serve(
                websocket_server.handle_connection,
                config.server.host,
                config.server.ws_port,
                max_size=None,
            ),
            ota_listener_factory=lambda: _start_aiohttp(
                create_ota_app(config), config.server.ota_host, config.server.ota_port
            ),
            admin_listener_factory=lambda: _start_aiohttp(
                create_admin_app(config, memory_service), config.admin_api.host, config.admin_api.port
            ),
            provider_resources=tuple(item for item in (asr, llm, tts, music) if item is not None),
        )

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._stopped or self._stopping:
                raise RuntimeError("application has been stopped")
            if self._started:
                return
            try:
                await self._memory_store.initialize()
                for factory in self._listener_factories:
                    self._listeners.append(await factory())
            except BaseException:
                await asyncio.shield(self._begin_shutdown())
                raise
            self._started = True

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            shutdown_task = self._shutdown_task
            if shutdown_task is None:
                if self._stopped:
                    return
                shutdown_task = self._begin_shutdown()
        error = await asyncio.shield(shutdown_task)
        if error is not None:
            async with self._lifecycle_lock:
                if self._shutdown_error_observed:
                    return
                self._shutdown_error_observed = True
            raise error

    def _begin_shutdown(self) -> asyncio.Task[Exception | None]:
        if self._shutdown_task is None:
            self._stopping = True
            self._started = False
            self._shutdown_task = asyncio.create_task(self._shutdown())
        return self._shutdown_task

    async def _shutdown(self) -> Exception | None:
        self._started = False
        first_error: Exception | None = None

        async def close(item: Any) -> None:
            nonlocal first_error
            try:
                await _close_resource(item)
            except Exception as error:
                if first_error is None:
                    first_error = error
                _LOGGER.exception("Failed to close server resource")

        for listener in reversed(self._listeners):
            await close(listener)
        self._listeners.clear()
        close_active = getattr(self._websocket_server, "close_active_sessions", None)
        if close_active is not None:
            try:
                await close_active()
            except Exception as error:
                if first_error is None:
                    first_error = error
                _LOGGER.exception("Failed to close active voice sessions")
        await close(self._memory_service)
        for resource in reversed(self._provider_resources):
            await close(resource)
        self._stopping = False
        self._stopped = True
        return first_error


class _AiohttpListener:
    def __init__(self, runner: web.AppRunner) -> None:
        self._runner = runner

    async def close(self) -> None:
        await self._runner.cleanup()


async def _start_aiohttp(app: web.Application, host: str, port: int) -> _AiohttpListener:
    runner = web.AppRunner(app)
    await runner.setup()
    try:
        site = web.TCPSite(runner, host, port)
        await site.start()
    except Exception:
        await runner.cleanup()
        raise
    return _AiohttpListener(runner)


async def _close_resource(resource: Any) -> None:
    close = getattr(resource, "close", None)
    if close is not None:
        result = close()
        if inspect.isawaitable(result):
            await result
    wait_closed = getattr(resource, "wait_closed", None)
    if wait_closed is not None:
        result = wait_closed()
        if inspect.isawaitable(result):
            await result
