from __future__ import annotations

import asyncio
from typing import Awaitable, TypeVar


_T = TypeVar("_T")


async def wait_for(awaitable: Awaitable[_T], timeout: float) -> _T:
    """Run an awaitable with a timeout on Python 3.10 and newer."""
    timeout_context = getattr(asyncio, "timeout", None)
    if timeout_context is None:
        try:
            return await asyncio.wait_for(awaitable, timeout)
        except asyncio.TimeoutError as error:
            raise TimeoutError() from error
    async with timeout_context(timeout):
        return await awaitable
