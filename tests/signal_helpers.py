"""Deliver test signals at an explicit event-loop boundary, without timer threads."""

from __future__ import annotations

import asyncio
import os
import signal


def _interrupt_from_callback() -> None:
    # Deliver the signal, then spin in pure Python: CPython raises the pending
    # KeyboardInterrupt at the first eval-loop check, which now lands inside
    # this callback's frame (loop machinery) instead of inside whichever task
    # coroutine step would otherwise have run next. asyncio's Task.__step
    # records a KeyboardInterrupt raised mid-step as *that task's* exception,
    # which would replace the modelled "runner was interrupted" shape the
    # tests assert on; padding keeps the landing site deterministic.
    os.kill(os.getpid(), signal.SIGINT)
    for _ in range(1000):
        pass


async def interrupt_next_loop_turn() -> None:
    # Deliver outside a task's coroutine step. Raising KeyboardInterrupt from
    # inside the teardown coroutine would fail that task rather than model a
    # Ctrl-C received by the runner while waiting for teardown.
    asyncio.get_running_loop().call_soon(_interrupt_from_callback)
    await asyncio.sleep(0)
