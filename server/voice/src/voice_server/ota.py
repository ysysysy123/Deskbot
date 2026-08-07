import ipaddress
import re
from urllib.parse import urlsplit

from aiohttp import web

from voice_server.config import AppConfig


def create_ota_app(config: AppConfig) -> web.Application:
    app = web.Application()

    async def ota(request: web.Request) -> web.Response:
        try:
            url = _websocket_url(request, config)
        except ValueError:
            return web.json_response({"error": "invalid Host header"}, status=400)
        websocket: dict[str, object] = {
            "url": url,
            "version": 1,
        }
        if _may_include_token(config):
            websocket["token"] = config.auth.token
        return web.json_response({"websocket": websocket})

    async def health(request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    app.router.add_get("/xiaozhi/ota/", ota)
    app.router.add_post("/xiaozhi/ota/", ota)
    app.router.add_get("/health", health)
    return app


def _websocket_url(request: web.Request, config: AppConfig) -> str:
    if config.server.public_websocket_url:
        return config.server.public_websocket_url
    return f"ws://{_host_without_port(request.host)}:{config.server.ws_port}/xiaozhi/v1/"


def _host_without_port(host: str) -> str:
    if (
        not host
        or host.endswith(":")
        or any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in host
        )
    ):
        raise ValueError("invalid Host header")

    try:
        parsed = urlsplit(f"//{host}")
        port = parsed.port
        hostname = parsed.hostname
    except ValueError:
        raise ValueError("invalid Host header") from None

    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or hostname is None
    ):
        raise ValueError("invalid Host header")

    if ":" in hostname:
        match = re.fullmatch(r"\[([^\]]+)\](?::([0-9]+))?", host)
        if match is None:
            raise ValueError("invalid Host header")
        try:
            address = ipaddress.IPv6Address(match.group(1))
        except ipaddress.AddressValueError:
            raise ValueError("invalid Host header") from None
        if port is not None and not 1 <= port <= 65_535:
            raise ValueError("invalid Host header")
        return f"[{address.compressed}]"

    if "[" in host or "]" in host:
        raise ValueError("invalid Host header")

    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        domain = hostname[:-1] if hostname.endswith(".") else hostname
        labels = domain.split(".")
        if (
            not domain
            or len(hostname) > 253
            or any(re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label) is None for label in labels)
        ):
            raise ValueError("invalid Host header")
    else:
        if not isinstance(address, ipaddress.IPv4Address):
            raise ValueError("invalid Host header")

    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("invalid Host header")
    return hostname


def _may_include_token(config: AppConfig) -> bool:
    return (
        not config.server.public_websocket_url
        and config.auth.mode == "bearer"
        and bool(config.auth.token.strip())
        and config.auth.ota_include_token
    )
