"""Asyncio helpers shared by the runner and the tool broker."""

from __future__ import annotations

import asyncio
from typing import Any


async def observe_completion(task: asyncio.Task[Any], timeout: float) -> bool:
    """Wait for ``task`` to finish, up to ``timeout`` seconds.

    Returns True when the task finished. Never raises the task's own
    exception and never forwards cancellation to the task itself: callers
    inspect the task afterwards for the outcome. Cancellation of the
    *waiting* coroutine still propagates normally.
    """

    loop = asyncio.get_running_loop()
    waiter: asyncio.Future[None] = loop.create_future()

    def _complete(_task: asyncio.Task[Any]) -> None:
        if not waiter.done():
            waiter.set_result(None)

    task.add_done_callback(_complete)
    try:
        await asyncio.wait_for(asyncio.shield(waiter), timeout)
    except TimeoutError:
        return task.done()
    return True
