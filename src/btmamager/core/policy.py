"""Which torrents to pause and which to resume. Pure logic, no network."""

from __future__ import annotations

from dataclasses import dataclass, field

# Completed torrents qBittorrent is seeding (or will seed once a queue slot frees up).
ACTIVE_UP = {"uploading", "stalledUP", "queuedUP", "forcedUP", "checkingUP"}
# Completed torrents that are paused (qBittorrent 5 calls this "stopped").
PAUSED_UP = {"pausedUP", "stoppedUP"}


@dataclass
class Torrent:
    hash: str
    name: str
    size: int
    state: str
    progress: float
    completion_on: int  # unix time; 0 or -1 when unknown
    trackers: list[str] = field(default_factory=list)  # hostnames
    mam_id: int | None = None  # from the torrent's comment link, when known

    @property
    def completed(self) -> bool:
        # Downloading, checking, moving, errored or missing-files torrents are left alone.
        return self.progress >= 1 and self.state in ACTIVE_UP | PAUSED_UP

    @property
    def active(self) -> bool:
        return self.state in ACTIVE_UP


@dataclass
class Action:
    hash: str
    name: str
    size: int
    action: str  # pause | resume
    reason: str


@dataclass
class Plan:
    pause: list[Action] = field(default_factory=list)
    resume: list[Action] = field(default_factory=list)
    counts: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def actions(self) -> list[Action]:
        return self.pause + self.resume


def host_matches(host: str, approved: str) -> bool:
    host = host.lower().rstrip(".")
    return bool(approved) and (host == approved or host.endswith("." + approved))


def is_approved(t: Torrent, approved: str) -> bool:
    return any(host_matches(h, approved) for h in t.trackers)


def decide(torrents: list[Torrent], approved: str, target: int, max_age_hours: float, now: float,
           unsatisfied: set[str] = frozenset()) -> Plan:
    """`unsatisfied`: hashes of torrents the tracker says still need seeding; they always seed."""
    plan = Plan()
    cutoff = now - max_age_hours * 3600

    done = [t for t in torrents if t.completed]
    ours = [t for t in done if is_approved(t, approved)]
    others = [t for t in done if not is_approved(t, approved)]

    # 1. Other trackers never seed.
    for t in others:
        if t.active:
            plan.pause.append(Action(t.hash, t.name, t.size, "pause", "not on the approved tracker"))

    # 2. Unsatisfied and recently completed approved torrents always seed.
    unsat = [t for t in ours if t.hash in unsatisfied]
    young = [t for t in ours if t.hash not in unsatisfied and max_age_hours > 0
             and t.completion_on > 0 and t.completion_on >= cutoff]
    keep = unsat + young
    keep_ids = {t.hash for t in keep}
    if len(keep) > target:
        why = " and ".join(w for w, n in ((f"{len(unsat)} unsatisfied", unsat),
                                          (f"{len(young)} completed in the last {max_age_hours:g} h", young)) if n)
        plan.warnings.append(f"{why}: {len(keep)} torrents, more than the target of {target}; "
                             "seeding all of them.")

    # 3. Fill the rest of the target with the largest of the others.
    older = sorted((t for t in ours if t.hash not in keep_ids), key=lambda t: (-t.size, t.hash))
    slots = max(0, target - len(keep))
    large, rest = older[:slots], older[slots:]

    for t in unsat:
        if not t.active:
            plan.resume.append(Action(t.hash, t.name, t.size, "resume", "unsatisfied on the tracker"))
    for t in young:
        if not t.active:
            plan.resume.append(Action(t.hash, t.name, t.size, "resume", f"completed within {max_age_hours:g} h"))
    for t in large:
        if not t.active:
            plan.resume.append(Action(t.hash, t.name, t.size, "resume", "among the largest"))
    for t in rest:
        if t.active:
            plan.pause.append(Action(t.hash, t.name, t.size, "pause", "outside the target count"))

    plan.counts = {
        "torrents": len(torrents),
        "completed": len(done),
        "other_tracker": len(others),
        "approved": len(ours),
        "unsatisfied": len(unsat),
        "young": len(young),
        "large": len(large),
        "seeding_after": len(keep) + len(large),
        "size_cutoff": large[-1].size if large else None,
        "to_pause": len(plan.pause),
        "to_resume": len(plan.resume),
    }
    return plan
