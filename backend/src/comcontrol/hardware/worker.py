"""Serialised hardware access outside the asyncio event loop.

All controller I/O runs on one dedicated thread, so concurrent API requests or
browser windows can never interleave USB commands.  Callers await the result
with a time limit; a slow operation reports `HardwareTimeout` instead of
blocking the API (the thread itself is bounded by the transport timeout).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")


class HardwareTimeout(Exception):
    pass


class HardwareWorker:
    def __init__(self) -> None:
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="comcontrol-hw"
        )

    async def run(self, fn: Callable[..., T], *args: object, timeout: float = 5.0) -> T:
        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(self._executor, fn, *args)
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout)
        except TimeoutError as exc:
            raise HardwareTimeout(f"hardware operation exceeded {timeout:.1f} s") from exc

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)
