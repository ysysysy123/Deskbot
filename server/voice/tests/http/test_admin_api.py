from dataclasses import replace

from voice_server.admin_api import create_admin_app
from voice_server.config import AppConfig
from voice_server.memory.models import MemoryMessage
from tests.fakes import FakeMemory


def config_with(*, token="", recent_limit=10, max_text_bytes=16_384):
    base = AppConfig()
    return replace(
        base,
        admin_api=replace(base.admin_api, token=token),
        memory=replace(base.memory, recent_limit=recent_limit),
        server=replace(base.server, max_text_bytes=max_text_bytes),
    )


async def test_get_memory_returns_context_and_uses_configured_default_limit(aiohttp_client):
    message = MemoryMessage(1, "device-a", "session-1", "user", "hello", "2026-08-06T00:00:00Z")
    memory = FakeMemory(summary="summary", recent=[message], relevant=[message])
    client = await aiohttp_client(create_admin_app(config_with(recent_limit=7), memory))

    response = await client.get("/api/v1/memory/device-a")

    assert response.status == 200
    assert await response.json() == {
        "device_id": "device-a",
        "summary": "summary",
        "recent_messages": [{"role": "user", "content": "hello", "session_id": "session-1", "created_at": "2026-08-06T00:00:00Z"}],
        "relevant_memories": [{"role": "user", "content": "hello", "session_id": "session-1", "created_at": "2026-08-06T00:00:00Z"}],
    }
    assert memory.recall_calls == [("device-a", "", 7)]


async def test_get_memory_validates_limit(aiohttp_client):
    memory = FakeMemory()
    client = await aiohttp_client(create_admin_app(AppConfig(), memory))

    responses = [
        await client.get("/api/v1/memory/device-a?limit=0"),
        await client.get("/api/v1/memory/device-a?limit=101"),
        await client.get("/api/v1/memory/device-a?limit=one"),
    ]

    assert [response.status for response in responses] == [400, 400, 400]
    assert all("error" in body for body in [await response.json() for response in responses])
    assert memory.recall_calls == []


async def test_get_memory_rejects_invalid_configured_default_limit(aiohttp_client):
    memory = FakeMemory()
    client = await aiohttp_client(create_admin_app(config_with(recent_limit=101), memory))

    response = await client.get("/api/v1/memory/device-a")

    assert response.status == 400
    assert await response.json() == {"error": "limit must be an integer between 1 and 100"}
    assert memory.recall_calls == []


async def test_get_memory_accepts_explicit_limit_boundaries(aiohttp_client):
    memory = FakeMemory()
    client = await aiohttp_client(create_admin_app(AppConfig(), memory))

    first = await client.get("/api/v1/memory/device-a?limit=1")
    last = await client.get("/api/v1/memory/device-a?limit=100")

    assert [first.status, last.status] == [200, 200]
    assert memory.recall_calls == [("device-a", "", 1), ("device-a", "", 100)]


async def test_post_message_remembers_valid_payload_and_returns_created(aiohttp_client):
    memory = FakeMemory()
    client = await aiohttp_client(create_admin_app(AppConfig(), memory))

    response = await client.post("/api/v1/memory/device-a/messages", json={"session_id": "session-1", "role": "assistant", "content": "reply"})

    assert response.status == 201
    assert await response.json() == {"status": "created"}
    assert memory.saved == [("device-a", "session-1", "assistant", "reply")]


async def test_post_message_rejects_invalid_json_and_payloads(aiohttp_client):
    memory = FakeMemory()
    client = await aiohttp_client(create_admin_app(config_with(max_text_bytes=4), memory))

    malformed = await client.post("/api/v1/memory/device-a/messages", data=b"{")
    non_object = await client.post("/api/v1/memory/device-a/messages", json=[])
    invalid_role = await client.post("/api/v1/memory/device-a/messages", json={"session_id": "s", "role": "system", "content": "x"})
    non_string_role = await client.post("/api/v1/memory/device-a/messages", json={"session_id": "s", "role": [], "content": "x"})
    oversize = await client.post("/api/v1/memory/device-a/messages", json={"session_id": "s", "role": "user", "content": "你你"})

    responses = (malformed, non_object, invalid_role, non_string_role, oversize)
    assert [response.status for response in responses] == [400, 400, 400, 400, 400]
    assert all("error" in body for body in [await response.json() for response in responses])
    assert memory.saved == []


async def test_post_message_rejects_whitespace_only_session_and_content(aiohttp_client):
    memory = FakeMemory()
    client = await aiohttp_client(create_admin_app(AppConfig(), memory))

    blank_session = await client.post(
        "/api/v1/memory/device-a/messages",
        json={"session_id": " \t", "role": "user", "content": "hello"},
    )
    blank_content = await client.post(
        "/api/v1/memory/device-a/messages",
        json={"session_id": "session-1", "role": "user", "content": " \n"},
    )

    assert [blank_session.status, blank_content.status] == [400, 400]
    assert memory.saved == []


async def test_post_message_preserves_valid_original_whitespace(aiohttp_client):
    memory = FakeMemory()
    client = await aiohttp_client(create_admin_app(AppConfig(), memory))

    response = await client.post(
        "/api/v1/memory/device-a/messages",
        json={"session_id": " session-1 ", "role": "user", "content": " hello "},
    )

    assert response.status == 201
    assert memory.saved == [("device-a", " session-1 ", "user", " hello ")]


async def test_post_message_validates_session_id_utf8_and_preserves_valid_unicode(aiohttp_client):
    memory = FakeMemory()
    client = await aiohttp_client(create_admin_app(AppConfig(), memory))

    invalid = await client.post(
        "/api/v1/memory/device-a/messages",
        json={"session_id": "\ud800", "role": "user", "content": "hello"},
    )
    valid = await client.post(
        "/api/v1/memory/device-a/messages",
        json={"session_id": " \u4f1a\u8bdd ", "role": "user", "content": "hello"},
    )

    assert [invalid.status, valid.status] == [400, 201]
    assert "error" in await invalid.json()
    assert memory.saved == [("device-a", " \u4f1a\u8bdd ", "user", "hello")]


async def test_post_message_handles_utf8_boundaries_and_unpaired_surrogate(aiohttp_client):
    memory = FakeMemory()
    exact_client = await aiohttp_client(create_admin_app(config_with(max_text_bytes=6), memory))
    over_client = await aiohttp_client(create_admin_app(config_with(max_text_bytes=5), memory))

    exact = await exact_client.post(
        "/api/v1/memory/device-a/messages",
        json={"session_id": "s", "role": "user", "content": "\u4f60\u597d"},
    )
    over = await over_client.post(
        "/api/v1/memory/device-a/messages",
        json={"session_id": "s", "role": "user", "content": "123456"},
    )
    surrogate = await exact_client.post(
        "/api/v1/memory/device-a/messages",
        json={"session_id": "s", "role": "user", "content": "\ud800"},
    )

    assert [exact.status, over.status, surrogate.status] == [201, 400, 400]
    assert "error" in await over.json()
    assert "error" in await surrogate.json()
    assert memory.saved == [("device-a", "s", "user", "\u4f60\u597d")]


async def test_whitespace_device_id_is_rejected_before_all_memory_operations(aiohttp_client):
    memory = FakeMemory()
    client = await aiohttp_client(create_admin_app(AppConfig(), memory))

    get_response = await client.get("/api/v1/memory/%20")
    post_response = await client.post(
        "/api/v1/memory/%20/messages",
        json={"session_id": "s", "role": "user", "content": "hello"},
    )
    delete_response = await client.delete("/api/v1/memory/%20")

    assert [get_response.status, post_response.status, delete_response.status] == [400, 400, 400]
    assert all(
        "error" in body
        for body in [
            await get_response.json(),
            await post_response.json(),
            await delete_response.json(),
        ]
    )
    assert memory.recall_calls == []
    assert memory.saved == []
    assert memory.clear_calls == []


async def test_delete_memory_clears_only_addressed_device_once(aiohttp_client):
    memory = FakeMemory()
    client = await aiohttp_client(create_admin_app(AppConfig(), memory))

    response = await client.delete("/api/v1/memory/device-b")

    assert response.status == 204
    assert await response.read() == b""
    assert memory.clear_calls == ["device-b"]


async def test_admin_token_requires_exact_bearer_token(aiohttp_client):
    memory = FakeMemory()
    client = await aiohttp_client(create_admin_app(config_with(token="admin-secret"), memory))

    missing = await client.get("/api/v1/memory/device-a")
    wrong = await client.get("/api/v1/memory/device-a", headers={"Authorization": "Bearer wrong"})
    valid = await client.get("/api/v1/memory/device-a", headers={"Authorization": "Bearer admin-secret"})

    assert [response.status for response in (missing, wrong, valid)] == [401, 401, 200]
    assert "error" in await missing.json()
    assert "error" in await wrong.json()
    assert memory.recall_calls == [("device-a", "", 10)]


async def test_empty_admin_token_allows_loopback_default_without_authorization(aiohttp_client):
    memory = FakeMemory()
    client = await aiohttp_client(create_admin_app(config_with(token=""), memory))

    response = await client.get("/api/v1/memory/device-a")

    assert response.status == 200
