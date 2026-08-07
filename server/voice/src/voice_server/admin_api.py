import json
from collections.abc import Mapping

from aiohttp import web

from voice_server.auth import check_admin_token
from voice_server.config import AppConfig
from voice_server.memory.models import MemoryContext, MemoryMessage
from voice_server.memory.service import MemoryService


def create_admin_app(config: AppConfig, memory: MemoryService) -> web.Application:
    @web.middleware
    async def admin_auth(request: web.Request, handler):
        if not check_admin_token(request.headers.get("Authorization"), config.admin_api.token):
            return _error("unauthorized", status=401)
        return await handler(request)

    app = web.Application(middlewares=[admin_auth])

    async def get_memory(request: web.Request) -> web.Response:
        device_id = request.match_info["device_id"]
        if not device_id.strip():
            return _error("device_id must be a non-empty string")
        limit = _parse_limit(request, config.memory.recent_limit)
        if limit is None:
            return _error("limit must be an integer between 1 and 100")
        context = await memory.recall(device_id, "", limit)
        return web.json_response(_context_response(device_id, context))

    async def remember_message(request: web.Request) -> web.Response:
        device_id = request.match_info["device_id"]
        if not device_id.strip():
            return _error("device_id must be a non-empty string")
        payload = await _json_mapping(request)
        if payload is None:
            return _error("request body must be a JSON object")
        session_id = payload.get("session_id")
        role = payload.get("role")
        content = payload.get("content")
        if not isinstance(session_id, str) or not session_id.strip():
            return _error("session_id must be a non-empty string")
        try:
            session_id.encode("utf-8")
        except UnicodeEncodeError:
            return _error("session_id must be valid UTF-8")
        if not isinstance(role, str) or role not in {"user", "assistant"}:
            return _error("role must be user or assistant")
        if not isinstance(content, str) or not content.strip():
            return _error("content must be a non-empty string")
        try:
            content_bytes = content.encode("utf-8")
        except UnicodeEncodeError:
            return _error("content must be valid UTF-8")
        if len(content_bytes) > config.server.max_text_bytes:
            return _error("content exceeds maximum text size")
        await memory.remember(device_id, session_id, role, content)
        return web.json_response({"status": "created"}, status=201)

    async def clear_memory(request: web.Request) -> web.Response:
        device_id = request.match_info["device_id"]
        if not device_id.strip():
            return _error("device_id must be a non-empty string")
        await memory.clear(device_id)
        return web.Response(status=204)

    app.router.add_get("/api/v1/memory/{device_id}", get_memory)
    app.router.add_post("/api/v1/memory/{device_id}/messages", remember_message)
    app.router.add_delete("/api/v1/memory/{device_id}", clear_memory)
    return app


def _parse_limit(request: web.Request, default: int) -> int | None:
    value = request.query.get("limit", default)
    try:
        limit = int(value)
    except (TypeError, ValueError):
        return None
    return limit if 1 <= limit <= 100 else None


async def _json_mapping(request: web.Request) -> Mapping[str, object] | None:
    try:
        payload = await request.json(loads=json.loads)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _context_response(device_id: str, context: MemoryContext) -> dict[str, object]:
    return {
        "device_id": device_id,
        "summary": context.summary,
        "recent_messages": [_message_response(message) for message in context.recent_messages],
        "relevant_memories": [_message_response(message) for message in context.relevant_memories],
    }


def _message_response(message: MemoryMessage) -> dict[str, str]:
    return {
        "role": message.role,
        "content": message.content,
        "session_id": message.session_id,
        "created_at": message.created_at,
    }


def _error(message: str, *, status: int = 400) -> web.Response:
    return web.json_response({"error": message}, status=status)
