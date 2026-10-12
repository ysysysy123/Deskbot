from dataclasses import replace

from voice_server.config import AppConfig
from voice_server.ota import create_ota_app


def config_with(*, public_websocket_url="", auth_mode="none", auth_token="", ota_include_token=False):
    base = AppConfig()
    return replace(
        base,
        server=replace(base.server, public_websocket_url=public_websocket_url),
        auth=replace(
            base.auth,
            mode=auth_mode,
            token=auth_token,
            ota_include_token=ota_include_token,
        ),
    )


async def test_health_returns_ok_json(aiohttp_client):
    client = await aiohttp_client(create_ota_app(AppConfig()))

    response = await client.get("/health")

    assert response.status == 200
    assert await response.json() == {"status": "ok"}


async def test_readyz_reports_startup_state(aiohttp_client):
    state = {"status": "starting", "providers": {"asr": "loaded"}}
    client = await aiohttp_client(create_ota_app(AppConfig(), readiness=lambda: state))

    starting = await client.get("/readyz")
    assert starting.status == 503
    assert await starting.json() == state

    state["status"] = "ready"
    ready = await client.get("/readyz")
    assert ready.status == 200
    assert await ready.json() == state


async def test_public_ota_omits_token_by_default(aiohttp_client):
    config = config_with(
        public_websocket_url="wss://voice.example/xiaozhi/v1/",
        auth_mode="bearer",
        auth_token="secret",
    )
    client = await aiohttp_client(create_ota_app(config))

    body = await (await client.get("/xiaozhi/ota/")).json()

    assert body == {"websocket": {"url": "wss://voice.example/xiaozhi/v1/", "version": 1}}


async def test_local_ota_replaces_domain_port_and_ignores_post_body(aiohttp_client):
    config = config_with()
    client = await aiohttp_client(create_ota_app(config))

    body = await (await client.post("/xiaozhi/ota/", data=b"not-json", headers={"Host": "voice.example:9123"})).json()

    assert body == {"websocket": {"url": "ws://voice.example:8000/xiaozhi/v1/", "version": 1}}


async def test_local_ota_replaces_ipv4_port(aiohttp_client):
    client = await aiohttp_client(create_ota_app(AppConfig()))

    body = await (await client.get("/xiaozhi/ota/", headers={"Host": "192.0.2.7:9123"})).json()

    assert body["websocket"]["url"] == "ws://192.0.2.7:8000/xiaozhi/v1/"


async def test_local_ota_replaces_bracketed_ipv6_port(aiohttp_client):
    client = await aiohttp_client(create_ota_app(AppConfig()))

    body = await (await client.get("/xiaozhi/ota/", headers={"Host": "[::1]:8003"})).json()

    assert body["websocket"]["url"] == "ws://[::1]:8000/xiaozhi/v1/"


async def test_local_ota_accepts_bracketed_ipv6_without_port(aiohttp_client):
    client = await aiohttp_client(create_ota_app(AppConfig()))

    response = await client.get("/xiaozhi/ota/", headers={"Host": "[::1]"})

    assert response.status == 200
    assert (await response.json())["websocket"]["url"] == "ws://[::1]:8000/xiaozhi/v1/"


async def test_local_ota_rejects_ipv6_port_and_authority_suffix_errors(aiohttp_client):
    client = await aiohttp_client(create_ota_app(AppConfig()))

    for host in (
        "[::1]:0",
        "[::1]:65536",
        "[::1]:",
        "[::1]junk",
        "[::1].evil.example",
    ):
        response = await client.get("/xiaozhi/ota/", headers={"Host": host})

        assert response.status == 400
        assert await response.json() == {"error": "invalid Host header"}


async def test_local_ota_rejects_malformed_host_headers_without_echo(aiohttp_client):
    client = await aiohttp_client(create_ota_app(AppConfig()))

    for host in (
        "example.com@evil.example",
        "evil.example/#x",
        "::1",
        ":8003",
        "",
        "host:",
        "host:bad",
    ):
        response = await client.get("/xiaozhi/ota/", headers={"Host": host})

        assert response.status == 400
        body = await response.json()
        assert body == {"error": "invalid Host header"}
        if host:
            assert host not in str(body)


async def test_public_url_does_not_parse_malicious_host_header(aiohttp_client):
    config = config_with(public_websocket_url="wss://voice.example/xiaozhi/v1/")
    client = await aiohttp_client(create_ota_app(config))

    response = await client.get(
        "/xiaozhi/ota/",
        headers={"Host": "example.com@evil.example"},
    )

    assert response.status == 200
    assert await response.json() == {
        "websocket": {"url": "wss://voice.example/xiaozhi/v1/", "version": 1}
    }


async def test_trusted_lan_provisioning_can_include_token(aiohttp_client):
    config = config_with(auth_mode="bearer", auth_token="secret", ota_include_token=True)
    client = await aiohttp_client(create_ota_app(config))

    body = await (await client.post("/xiaozhi/ota/", json={"device": "info"})).json()

    assert body["websocket"]["token"] == "secret"


async def test_public_url_never_includes_token_even_if_switch_is_misconfigured(aiohttp_client):
    config = config_with(
        public_websocket_url="wss://voice.example/xiaozhi/v1/",
        auth_mode="bearer",
        auth_token="secret",
        ota_include_token=True,
    )
    client = await aiohttp_client(create_ota_app(config))

    body = await (await client.get("/xiaozhi/ota/")).json()

    assert "token" not in body["websocket"]


async def test_device_photo_upload_matches_firmware_multipart(aiohttp_client):
    from aiohttp import FormData
    calls = []

    class Vision:
        async def describe(self, *args):
            calls.append(args)
            return "桌上有一本书。"

    client = await aiohttp_client(create_ota_app(AppConfig(), vision=Vision()))
    form = FormData()
    form.add_field("question", "桌上有什么？")
    form.add_field("file", b"JPEG_BYTES", filename="camera.jpg", content_type="image/jpeg")
    response = await client.post("/mcp/vision/explain", data=form, headers={"Device-Id": "test-device"})
    assert response.status == 200
    assert await response.json() == {"success": True, "photo_captured": True, "vision_description": "桌上有一本书。"}
    assert calls == [("SlBFR19CWVRFUw==", "image/jpeg", "桌上有什么？")]


async def test_device_photo_requires_configured_vision_and_auth(aiohttp_client):
    disabled = await aiohttp_client(create_ota_app(AppConfig()))
    assert (await disabled.post("/mcp/vision/explain")).status == 503
    protected = await aiohttp_client(create_ota_app(config_with(auth_mode="bearer", auth_token="test-only")))
    assert (await protected.post("/mcp/vision/explain")).status == 401
