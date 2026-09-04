"""Process-local publisher for Puppy's node-wide WebSocket state stream.

The runner owns ordering, revisions and per-viewer backpressure.  This module
owns asynchronous snapshot production and its backend-owned refresh cadence,
so opening more consoles never creates more engine/browser probes.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Iterable, Optional

from aiohttp import web

from puppy import runner

log = logging.getLogger("puppy.state_stream")

SnapshotBuilder = Callable[[web.Application, Optional[set], bool],
                           Awaitable[Iterable[dict]]]
IntervalGetter = Callable[[], float]

_publisher = None


class _Publisher:
    def __init__(self, app: web.Application, builder: SnapshotBuilder,
                 interval: IntervalGetter, snapshot_topics: Iterable[str],
                 periodic_topics: Iterable[str]):
        self.app = app
        self.builder = builder
        self.interval = interval
        self.snapshot_topics = {str(item) for item in snapshot_topics}
        self.periodic_topics = {str(item) for item in periodic_topics}
        self.pending = set()
        self.refresh_all = False
        self.generation = 0
        self.wake_event = asyncio.Event()
        self.task = None

    def wake(self, topics: Iterable[str]) -> None:
        values = {str(item) for item in topics if str(item)}
        if values:
            self.pending.update(values)
        else:
            self.refresh_all = True
        self.wake_event.set()

    def invalidate(self) -> None:
        """Make every in-flight snapshot result obsolete without cancelling I/O."""
        self.generation += 1

    async def refresh(self, topics: Optional[set], probes: bool) -> None:
        generation = self.generation
        selected = self.snapshot_topics if topics is None else {
            str(item) for item in topics if str(item)}

        async def one(topic: str) -> None:
            try:
                payloads = await self.builder(self.app, {topic}, probes)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("node-state %s snapshot refresh failed", topic)
                return
            if generation != self.generation:
                return
            for payload in payloads or ():
                try:
                    runner.publish_state(payload)
                except Exception:
                    log.exception("node-state %s snapshot publish failed", topic)

        if selected:
            await asyncio.gather(*(one(topic) for topic in sorted(selected)))

    async def run(self) -> None:
        # The cheap startup snapshot is installed by the lifecycle before this
        # task begins.  This first pass performs due engine/browser probes.
        self.refresh_all = True
        self.wake_event.set()
        while True:
            if self.refresh_all:
                topics = None
            else:
                topics = set(self.pending)
            self.refresh_all = False
            self.pending.clear()
            self.wake_event.clear()
            await self.refresh(topics, probes=True)
            # A mutation may have arrived while the snapshot was being built.
            if self.refresh_all or self.pending:
                continue
            try:
                delay = max(1.0, float(self.interval()))
            except Exception:
                delay = 60.0
            try:
                await asyncio.wait_for(self.wake_event.wait(), timeout=delay)
            except asyncio.TimeoutError:
                self.pending.update(self.periodic_topics)


def register(app: web.Application, builder: SnapshotBuilder,
             interval: IntervalGetter, snapshot_topics: Iterable[str],
             periodic_topics: Iterable[str]) -> None:
    """Attach exactly one publisher lifecycle to a full or headless runtime."""
    if app.get("puppy_state_stream_registered"):
        return
    app["puppy_state_stream_registered"] = True
    app["puppy_state_stream_builder"] = builder
    app["puppy_state_stream_interval"] = interval
    app["puppy_state_stream_snapshot_topics"] = tuple(snapshot_topics)
    app["puppy_state_stream_periodic_topics"] = tuple(periodic_topics)
    app.cleanup_ctx.append(_lifecycle)


async def _lifecycle(app: web.Application):
    global _publisher
    publisher = _Publisher(
        app, app["puppy_state_stream_builder"],
        app["puppy_state_stream_interval"],
        app["puppy_state_stream_snapshot_topics"],
        app["puppy_state_stream_periodic_topics"])
    _publisher = publisher
    # Populate non-probing snapshots before the listener accepts clients.
    await publisher.refresh(None, probes=False)
    publisher.task = asyncio.create_task(
        publisher.run(), name="puppy-node-state-stream")
    try:
        yield
    finally:
        if _publisher is publisher:
            _publisher = None
        if publisher.task is not None:
            publisher.task.cancel()
            try:
                await publisher.task
            except asyncio.CancelledError:
                pass


def wake(*topics: str) -> None:
    """Refresh named snapshots soon; no arguments requests the whole set."""
    if _publisher is not None:
        _publisher.wake(topics)


def invalidate() -> None:
    """Reject results that began before a durable-state replacement."""
    if _publisher is not None:
        _publisher.invalidate()


def publish(payload: dict, topic: str = "") -> dict:
    """Publish an already-built synchronous snapshot."""
    return runner.publish_state(payload, topic=topic)


def reset_for_tests() -> None:
    global _publisher
    publisher, _publisher = _publisher, None
    if publisher is not None and publisher.task is not None:
        publisher.task.cancel()
