"""Async argv execution helpers. Commands never go through a shell."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from pathlib import Path

from reprocase.environments.protocol import CommandResult


class CommandTimeoutError(Exception):
    def __init__(self, argv: Sequence[str], timeout_seconds: float) -> None:
        super().__init__(f"Command timed out after {timeout_seconds}s: {list(argv)}")
        self.argv = list(argv)
        self.timeout_seconds = timeout_seconds


async def run_argv(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float = 60.0,
) -> CommandResult:
    started = time.monotonic_ns()
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise CommandTimeoutError(argv, timeout_seconds) from exc
    duration_ms = max(0, (time.monotonic_ns() - started) // 1_000_000)
    return CommandResult(
        exit_code=process.returncode if process.returncode is not None else -1,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
        duration_ms=duration_ms,
    )
