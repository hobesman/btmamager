"""The background loop: read torrents, decide, apply, remember what happened."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .config import ConfigStore, Settings
from .mam import Mam, match
from .policy import Action, decide, is_approved
from .qbit import QBit

log = logging.getLogger(__name__)
HISTORY = 20
MISSING_SHOWN = 10


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
    def __init__(self, store: ConfigStore, client_factory: Callable[[Settings], QBit] = QBit.from_settings,
                 mam_factory: Callable[[str], Mam] = Mam):
        self.store = store
        self.client_factory = client_factory
        self.mam_factory = mam_factory
        self._mam = None
        self._mam_key = None
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
                    self._client = self._mam = None  # reconnect next time
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

    def _mam_for(self, s: Settings) -> Mam:
        if self._mam is None or s.mam_id != self._mam_key:
            self._mam = self.mam_factory(s.mam_id)
            self._mam.connect()
            self._mam_key = s.mam_id
        return self._mam

    def _unsatisfied(self, s: Settings, qb, torrents, warnings: list[str]) -> set[str]:
        """Hashes MAM says still need seeding. Raises (so nothing changes) if MAM can't be asked."""
        snatches = self._mam_for(s).needs_seeding()
        ours = [t for t in torrents if is_approved(t, s.approved_tracker)]
        qb.fill_mam_ids(ours)
        found, missing = match(snatches, ours)
        log.info("MAM lists %d torrents needing seeding; %d found in qBittorrent", len(snatches), len(found))
        if missing:
            names = ", ".join(m.title or f"#{m.id}" for m in missing[:MISSING_SHOWN])
            more = f" and {len(missing) - MISSING_SHOWN} more" if len(missing) > MISSING_SHOWN else ""
            warnings.append(f"{len(missing)} torrents MAM lists as needing seeding aren't in qBittorrent "
                            f"(or couldn't be matched): {names}{more}.")
        return found

    def _cycle(self, s: Settings, res: RunResult) -> None:
        qb = self._client_for(s)
        torrents = qb.torrents()
        warnings: list[str] = []
        unsat = self._unsatisfied(s, qb, torrents, warnings) if s.use_mam else set()
        plan = decide(torrents, s.approved_tracker, s.target_seeding, s.max_age_hours, time.time(), unsat)
        res.counts, res.actions, res.warnings = plan.counts, plan.actions, warnings + plan.warnings
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
        log.info("%s%d approved torrents seeding (%d unsatisfied + %d recent + %d largest); paused %d, resumed %d",
                 "[dry run] " if res.dry_run else "", c["seeding_after"], c["unsatisfied"], c["young"],
                 c["large"], c["to_pause"], c["to_resume"])

    async def test_connection(self, s: Settings) -> tuple[bool, str]:
        try:
            return True, await asyncio.to_thread(lambda: self.client_factory(s).connect())
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"

    async def test_mam(self, s: Settings) -> tuple[bool, str]:
        if not s.use_mam:
            return False, "No MAM session set."

        def check() -> str:
            mam = self.mam_factory(s.mam_id)
            who = mam.connect()
            return f"{who}; {len(mam.needs_seeding())} torrents listed as needing seeding"
        try:
            return True, await asyncio.to_thread(check)
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
