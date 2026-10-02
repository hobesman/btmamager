"""The background loop: read torrents, decide, apply, remember what happened."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .config import ConfigStore, Settings
from .policy import Action, decide
from .qbit import QBit

log = logging.getLogger(__name__)
HISTORY = 20


@dataclass
class RunResult:
    started: float
    finished: float = 0.0
    ok: bool = False
    error: str = ""
    dry_run: bool = True
    counts: dict = field(default_factory=dict)
    actions: list[Action] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return round(self.finished - self.started, 1)


class Manager:
    def __init__(self, store: ConfigStore, client_factory: Callable[[Settings], QBit] = QBit.from_settings):
        self.store = store
        self.client_factory = client_factory
        self.history: deque[RunResult] = deque(maxlen=HISTORY)
        self.next_run: float | None = None
        self._client = None
        self._client_key = None
        self._lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def last(self) -> RunResult | None:
        return self.history[0] if self.history else None

    @property
    def running(self) -> bool:
        return self._lock.locked()

    # ---- loop ------------------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def wake(self) -> None:
        self._wake.set()

    async def _loop(self) -> None:
        while True:
            self._wake.clear()
            await self.run_once()
            interval = self.store.current().interval_minutes * 60
            self.next_run = time.time() + interval
            try:
                await asyncio.wait_for(self._wake.wait(), interval)
            except asyncio.TimeoutError:
                pass

    # ---- one pass --------------------------------------------------------------
    async def run_once(self, dry_run: bool | None = None) -> RunResult:
        async with self._lock:
            s = self.store.current()
            res = RunResult(started=time.time(), dry_run=s.dry_run if dry_run is None else dry_run)
            if not s.configured:
                res.error = "Configuration needed: set the approved tracker on the Settings page."
            else:
                try:
                    await asyncio.to_thread(self._cycle, s, res)
                    res.ok = True
                except Exception as e:
                    log.exception("Run failed")
                    res.error = f"{type(e).__name__}: {e}"
                    self._client = None  # reconnect next time
            res.finished = time.time()
            self.history.appendleft(res)
            return res

    def _client_for(self, s: Settings):
        key = (s.qbit_url, s.qbit_username, s.qbit_password)
        if self._client is None or key != self._client_key:
            self._client = self.client_factory(s)
            self._client.connect()
            self._client_key = key
        return self._client

    def _cycle(self, s: Settings, res: RunResult) -> None:
        qb = self._client_for(s)
        plan = decide(qb.torrents(), s.approved_tracker, s.target_seeding, s.max_age_hours, time.time())
        res.counts, res.actions, res.warnings = plan.counts, plan.actions, list(plan.warnings)
        warn = qb.queue_warning(s.target_seeding)
        if warn:
            res.warnings.append(warn)
        for w in res.warnings:
            log.warning(w)

        verb = "Would" if res.dry_run else "Will"
        for a in plan.actions:
            log.info("%s %s %s (%s)", verb, a.action, a.name, a.reason)
        if not res.dry_run:
            if plan.pause:
                qb.pause([a.hash for a in plan.pause])
            if plan.resume:
                qb.resume([a.hash for a in plan.resume])
        c = plan.counts
        log.info("%s%d approved torrents seeding (%d recent + %d largest); paused %d, resumed %d",
                 "[dry run] " if res.dry_run else "", c["seeding_after"], c["young"], c["large"],
                 c["to_pause"], c["to_resume"])

    async def test_connection(self, s: Settings) -> tuple[bool, str]:
        try:
            return True, await asyncio.to_thread(lambda: self.client_factory(s).connect())
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
