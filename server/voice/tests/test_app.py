import asyncio

import pytest

import voice_server.__main__ as cli
import voice_server.app as app_module
from voice_server.app import ServerApplication
from voice_server.config import AppConfig, ConfigError


class Recorder:
    def __init__(self):
        self.events = []


class FakeStore:
    def __init__(self, recorder, *, fail=False):
        self.recorder = recorder
        self.fail = fail

    async def initialize(self):
        self.recorder.events.append("store.initialize")
        if self.fail:
            raise RuntimeError("database unavailable")


class FakeResource:
    def __init__(self, recorder, name, *, fail=False):
        self.recorder = recorder
        self.name = name
        self.fail = fail

    async def close(self):
        self.recorder.events.append(f"{self.name}.close")
        if self.fail:
            raise RuntimeError(self.name)


class FakeVoiceServer(FakeResource):
    async def close_active_sessions(self):
        self.recorder.events.append("voice.sessions.close")


def _factory(recorder, name, resource):
    async def start():
        recorder.events.append(f"{name}.start")
        return resource

    return start


async def test_start_initializes_store_before_listeners_and_stop_reverses_lifecycle():
    recorder = Recorder()
    voice = FakeResource(recorder, "voice")
    ota = FakeResource(recorder, "ota")
    admin = FakeResource(recorder, "admin")
    app = ServerApplication(
        config=AppConfig(),
        memory_store=FakeStore(recorder),
        memory_service=FakeResource(recorder, "memory"),
        websocket_server=FakeVoiceServer(recorder, "voice_server"),
        voice_listener_factory=_factory(recorder, "voice", voice),
        ota_listener_factory=_factory(recorder, "ota", ota),
        admin_listener_factory=_factory(recorder, "admin", admin),
        provider_resources=(FakeResource(recorder, "asr"), FakeResource(recorder, "tts")),
    )

    await app.start()
    assert recorder.events == ["store.initialize", "voice.start", "ota.start", "admin.start"]

    await app.stop()
    assert recorder.events == [
        "store.initialize",
        "voice.start",
        "ota.start",
        "admin.start",
        "admin.close",
        "ota.close",
        "voice.close",
        "voice.sessions.close",
        "memory.close",
        "tts.close",
        "asr.close",
    ]


async def test_start_failure_rolls_back_started_resources_and_memory_service():
    recorder = Recorder()
    voice = FakeResource(recorder, "voice")

    async def broken_ota():
        recorder.events.append("ota.start")
        raise RuntimeError("cannot bind")

    app = ServerApplication(
        config=AppConfig(),
        memory_store=FakeStore(recorder),
        memory_service=FakeResource(recorder, "memory"),
        websocket_server=FakeVoiceServer(recorder, "voice_server"),
        voice_listener_factory=_factory(recorder, "voice", voice),
        ota_listener_factory=broken_ota,
        admin_listener_factory=_factory(recorder, "admin", FakeResource(recorder, "admin")),
    )

    with pytest.raises(RuntimeError, match="cannot bind"):
        await app.start()
    assert recorder.events == [
        "store.initialize",
        "voice.start",
        "ota.start",
        "voice.close",
        "voice.sessions.close",
        "memory.close",
    ]


async def test_initialize_failure_still_closes_memory_service_and_providers():
    recorder = Recorder()
    app = ServerApplication(
        config=AppConfig(),
        memory_store=FakeStore(recorder, fail=True),
        memory_service=FakeResource(recorder, "memory"),
        websocket_server=FakeVoiceServer(recorder, "voice_server"),
        voice_listener_factory=_factory(recorder, "voice", FakeResource(recorder, "voice")),
        ota_listener_factory=_factory(recorder, "ota", FakeResource(recorder, "ota")),
        admin_listener_factory=_factory(recorder, "admin", FakeResource(recorder, "admin")),
        provider_resources=(FakeResource(recorder, "llm"),),
    )

    with pytest.raises(RuntimeError, match="database unavailable"):
        await app.start()
    assert recorder.events == [
        "store.initialize",
        "voice.sessions.close",
        "memory.close",
        "llm.close",
    ]


async def test_stop_attempts_every_close_when_one_resource_fails_and_is_idempotent():
    recorder = Recorder()
    voice = FakeResource(recorder, "voice")
    ota = FakeResource(recorder, "ota", fail=True)
    admin = FakeResource(recorder, "admin")
    app = ServerApplication(
        config=AppConfig(),
        memory_store=FakeStore(recorder),
        memory_service=FakeResource(recorder, "memory"),
        websocket_server=FakeVoiceServer(recorder, "voice_server"),
        voice_listener_factory=_factory(recorder, "voice", voice),
        ota_listener_factory=_factory(recorder, "ota", ota),
        admin_listener_factory=_factory(recorder, "admin", admin),
        provider_resources=(FakeResource(recorder, "llm"),),
    )
    await app.start()

    with pytest.raises(RuntimeError, match="ota"):
        await app.stop()
    await app.stop()

    assert recorder.events.count("admin.close") == 1
    assert recorder.events.count("ota.close") == 1
    assert recorder.events.count("voice.close") == 1
    assert recorder.events.count("voice.sessions.close") == 1
    assert recorder.events.count("memory.close") == 1
    assert recorder.events.count("llm.close") == 1


async def test_duplicate_start_is_idempotent():
    recorder = Recorder()
    app = ServerApplication(
        config=AppConfig(),
        memory_store=FakeStore(recorder),
        memory_service=FakeResource(recorder, "memory"),
        websocket_server=FakeVoiceServer(recorder, "voice_server"),
        voice_listener_factory=_factory(recorder, "voice", FakeResource(recorder, "voice")),
        ota_listener_factory=_factory(recorder, "ota", FakeResource(recorder, "ota")),
        admin_listener_factory=_factory(recorder, "admin", FakeResource(recorder, "admin")),
    )

    await app.start()
    await app.start()

    assert recorder.events.count("store.initialize") == 1
    assert recorder.events.count("voice.start") == 1
    assert recorder.events.count("ota.start") == 1
    assert recorder.events.count("admin.start") == 1
    await app.stop()


async def test_stop_racing_start_waits_and_closes_every_started_listener():
    recorder = Recorder()
    initialize_started = asyncio.Event()
    allow_initialize = asyncio.Event()

    class GatedStore(FakeStore):
        async def initialize(self):
            self.recorder.events.append("store.initialize")
            initialize_started.set()
            await allow_initialize.wait()

    app = ServerApplication(
        config=AppConfig(),
        memory_store=GatedStore(recorder),
        memory_service=FakeResource(recorder, "memory"),
        websocket_server=FakeVoiceServer(recorder, "voice_server"),
        voice_listener_factory=_factory(recorder, "voice", FakeResource(recorder, "voice")),
        ota_listener_factory=_factory(recorder, "ota", FakeResource(recorder, "ota")),
        admin_listener_factory=_factory(recorder, "admin", FakeResource(recorder, "admin")),
    )

    start_task = asyncio.create_task(app.start())
    await initialize_started.wait()
    stop_task = asyncio.create_task(app.stop())
    await asyncio.sleep(0)
    allow_initialize.set()
    await start_task
    await stop_task

    assert recorder.events.count("voice.close") == 1
    assert recorder.events.count("ota.close") == 1
    assert recorder.events.count("admin.close") == 1
    with pytest.raises(RuntimeError, match="stopped"):
        await app.start()


async def test_cancelled_start_rolls_back_and_cannot_be_restarted():
    recorder = Recorder()
    listener_gate_entered = asyncio.Event()
    release_listener = asyncio.Event()
    voice = FakeResource(recorder, "voice")

    async def gated_ota_start():
        recorder.events.append("ota.start")
        listener_gate_entered.set()
        await release_listener.wait()
        return FakeResource(recorder, "ota")

    app = ServerApplication(
        config=AppConfig(),
        memory_store=FakeStore(recorder),
        memory_service=FakeResource(recorder, "memory"),
        websocket_server=FakeVoiceServer(recorder, "voice_server"),
        voice_listener_factory=_factory(recorder, "voice", voice),
        ota_listener_factory=gated_ota_start,
        admin_listener_factory=_factory(recorder, "admin", FakeResource(recorder, "admin")),
        provider_resources=(FakeResource(recorder, "llm"),),
    )

    start_task = asyncio.create_task(app.start())
    await listener_gate_entered.wait()
    start_task.cancel()
    try:
        with pytest.raises(asyncio.CancelledError):
            await start_task
        assert recorder.events.count("voice.close") == 1
        assert recorder.events.count("voice.sessions.close") == 1
        assert recorder.events.count("memory.close") == 1
        assert recorder.events.count("llm.close") == 1
        with pytest.raises(RuntimeError, match="stopped"):
            await app.start()
    finally:
        release_listener.set()
        await app.stop()


async def test_cancelled_stop_can_be_awaited_again_to_finish_cleanup():
    recorder = Recorder()
    close_entered = asyncio.Event()
    release_close = asyncio.Event()

    class GatedListener(FakeResource):
        async def close(self):
            self.recorder.events.append(f"{self.name}.close")
            close_entered.set()
            await release_close.wait()

    app = ServerApplication(
        config=AppConfig(),
        memory_store=FakeStore(recorder),
        memory_service=FakeResource(recorder, "memory"),
        websocket_server=FakeVoiceServer(recorder, "voice_server"),
        voice_listener_factory=_factory(recorder, "voice", GatedListener(recorder, "voice")),
        ota_listener_factory=_factory(recorder, "ota", FakeResource(recorder, "ota")),
        admin_listener_factory=_factory(recorder, "admin", FakeResource(recorder, "admin")),
        provider_resources=(FakeResource(recorder, "llm"),),
    )
    await app.start()

    stop_task = asyncio.create_task(app.stop())
    await close_entered.wait()
    stop_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await stop_task
    release_close.set()
    await app.stop()
    await app.stop()

    assert recorder.events.count("voice.close") == 1
    assert recorder.events.count("voice.sessions.close") == 1
    assert recorder.events.count("memory.close") == 1
    assert recorder.events.count("llm.close") == 1


async def test_cancelled_stop_does_not_consume_later_cleanup_error():
    recorder = Recorder()
    close_entered = asyncio.Event()
    release_close = asyncio.Event()
    cleanup_finished = asyncio.Event()

    class GatedFailingAdmin(FakeResource):
        async def close(self):
            self.recorder.events.append("admin.close")
            close_entered.set()
            await release_close.wait()
            raise RuntimeError("admin")

    class CompletionResource(FakeResource):
        async def close(self):
            self.recorder.events.append(f"{self.name}.close")
            cleanup_finished.set()

    app = ServerApplication(
        config=AppConfig(),
        memory_store=FakeStore(recorder),
        memory_service=FakeResource(recorder, "memory"),
        websocket_server=FakeVoiceServer(recorder, "voice_server"),
        voice_listener_factory=_factory(recorder, "voice", FakeResource(recorder, "voice")),
        ota_listener_factory=_factory(recorder, "ota", FakeResource(recorder, "ota")),
        admin_listener_factory=_factory(recorder, "admin", GatedFailingAdmin(recorder, "admin")),
        provider_resources=(CompletionResource(recorder, "llm"),),
    )
    await app.start()

    cancelled_stop = asyncio.create_task(app.stop())
    await close_entered.wait()
    cancelled_stop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_stop
    release_close.set()
    await cleanup_finished.wait()

    with pytest.raises(RuntimeError) as cleanup_error:
        await app.stop()
    assert cleanup_error.value.args == ("admin",)
    await app.stop()


async def test_only_one_concurrent_stop_reports_cleanup_error():
    recorder = Recorder()
    close_entered = asyncio.Event()
    release_close = asyncio.Event()

    class GatedFailingAdmin(FakeResource):
        async def close(self):
            self.recorder.events.append("admin.close")
            close_entered.set()
            await release_close.wait()
            raise RuntimeError("admin")

    app = ServerApplication(
        config=AppConfig(),
        memory_store=FakeStore(recorder),
        memory_service=FakeResource(recorder, "memory"),
        websocket_server=FakeVoiceServer(recorder, "voice_server"),
        voice_listener_factory=_factory(recorder, "voice", FakeResource(recorder, "voice")),
        ota_listener_factory=_factory(recorder, "ota", FakeResource(recorder, "ota")),
        admin_listener_factory=_factory(recorder, "admin", GatedFailingAdmin(recorder, "admin")),
    )
    await app.start()

    first_stop = asyncio.create_task(app.stop())
    await close_entered.wait()
    second_stop = asyncio.create_task(app.stop())
    release_close.set()
    results = await asyncio.gather(first_stop, second_stop, return_exceptions=True)

    assert sum(isinstance(result, RuntimeError) for result in results) == 1
    assert sum(result is None for result in results) == 1


def test_cli_config_error_is_nonzero_and_never_logs_environment_secret(monkeypatch, caplog):
    monkeypatch.setattr(cli, "load_config", lambda path: (_ for _ in ()).throw(ConfigError("secret-from-env")))

    assert cli.main(["--config", "bad.yaml"]) != 0
    assert "secret-from-env" not in caplog.text


async def test_run_application_stops_when_cancelled_during_start():
    start_entered = asyncio.Event()

    class Application:
        def __init__(self):
            self.stop_calls = 0

        async def start(self):
            start_entered.set()
            await asyncio.Future()

        async def stop(self):
            self.stop_calls += 1

    application = Application()
    task = asyncio.create_task(cli.run_application(application))
    await start_entered.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert application.stop_calls == 1


def test_from_config_constructs_dependencies_without_loading_real_models(monkeypatch):
    constructed = []

    class Store:
        def __init__(self, path):
            constructed.append(("store", path))

    class ASR:
        @classmethod
        def from_model_path(cls, path, *, max_concurrency):
            instance = cls()
            constructed.append(("asr", path, max_concurrency))
            return instance

    class LLM:
        def __init__(self, **kwargs):
            constructed.append(("llm", kwargs["model"]))

    class TTS:
        def __init__(self, **kwargs):
            constructed.append(("tts", kwargs["voice"]))

    monkeypatch.setattr(app_module, "SQLiteMemoryProvider", Store)
    monkeypatch.setattr(app_module, "SenseVoiceASRProvider", ASR)
    monkeypatch.setattr(app_module, "OpenAICompatibleLLMProvider", LLM)
    monkeypatch.setattr(app_module, "EdgeTTSProvider", TTS)

    application = ServerApplication.from_config(AppConfig())

    assert [item[0] for item in constructed] == ["store", "asr", "llm", "tts"]
    assert application.config == AppConfig()
