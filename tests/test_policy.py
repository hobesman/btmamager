from btmamager.core.policy import Torrent, decide, host_matches

MAM = "t.myanonamouse.net"
NOW = 1_800_000_000
HOUR = 3600


def t(h, size=1, state="stalledUP", progress=1.0, age_h=1000, trackers=(MAM,)):
    return Torrent(hash=h, name=h, size=size, state=state, progress=progress,
                   completion_on=NOW - int(age_h * HOUR), trackers=list(trackers))


def ids(actions):
    return {a.hash for a in actions}


def apply(torrents, plan):
    """Return the torrents as they'd look after the plan runs."""
    pause, resume = ids(plan.pause), ids(plan.resume)
    out = []
    for x in torrents:
        state = "stoppedUP" if x.hash in pause else "stalledUP" if x.hash in resume else x.state
        out.append(Torrent(x.hash, x.name, x.size, state, x.progress, x.completion_on, x.trackers))
    return out


def test_other_trackers_paused_and_downloads_ignored():
    ts = [
        t("other-seeding", trackers=("tracker.example.org",)),
        t("other-paused", state="stoppedUP", trackers=("tracker.example.org",)),
        t("other-downloading", state="downloading", progress=0.5, trackers=("tracker.example.org",)),
        t("mam-downloading", state="downloading", progress=0.3),
        t("errored", state="error", trackers=("tracker.example.org",)),
    ]
    plan = decide(ts, MAM, 190, 72, NOW)
    assert ids(plan.pause) == {"other-seeding"}
    assert plan.resume == []


def test_young_always_kept_then_largest_fill_target():
    young = [t(f"y{i}", size=1, age_h=10, state="stoppedUP" if i % 2 else "uploading") for i in range(10)]
    old = [t(f"o{i:03}", size=1000 + i, state="stalledUP" if i % 3 else "pausedUP") for i in range(300)]
    plan = decide(young + old, MAM, 190, 72, NOW)

    after = apply(young + old, plan)
    seeding = {x.hash for x in after if x.active}
    assert len(seeding) == 190
    assert {y.hash for y in young} <= seeding
    # the 180 biggest old ones are o120..o299
    assert {f"o{i:03}" for i in range(120, 300)} <= seeding
    assert plan.counts["young"] == 10 and plan.counts["large"] == 180
    assert plan.counts["size_cutoff"] == 1120


def test_young_exceeding_target_all_kept_with_warning():
    ts = [t(f"y{i}", age_h=1, state="stoppedUP") for i in range(5)] + [t("old", size=99)]
    plan = decide(ts, MAM, 3, 72, NOW)
    assert ids(plan.resume) == {f"y{i}" for i in range(5)}
    assert ids(plan.pause) == {"old"}
    assert plan.warnings


def test_fewer_than_target_resumes_all():
    ts = [t(f"o{i}", state="stoppedUP") for i in range(5)]
    plan = decide(ts, MAM, 190, 72, NOW)
    assert ids(plan.resume) == {f"o{i}" for i in range(5)}
    assert plan.pause == []


def test_age_uses_completion_time_and_unknown_is_old():
    ts = [t("fresh", age_h=71, size=1), t("stale", age_h=73, size=2),
          Torrent("unknown", "unknown", 3, "stalledUP", 1.0, -1, [MAM])]
    plan = decide(ts, MAM, 1, 72, NOW)
    assert plan.counts["young"] == 1
    assert ids(plan.pause) == {"stale", "unknown"}


def test_any_tracker_matching_counts_and_subdomains():
    assert host_matches("t.myanonamouse.net", MAM)
    assert host_matches("www.t.myanonamouse.net", MAM)
    assert not host_matches("myanonamouse.net.evil.com", MAM)
    assert not host_matches("nott.myanonamouse.net", MAM)
    ts = [t("multi", trackers=("tracker.example.org", MAM))]
    assert decide(ts, MAM, 1, 72, NOW).pause == []


def test_second_pass_is_a_no_op():
    ts = [t(f"o{i}", size=i, state="stoppedUP" if i % 2 else "uploading") for i in range(50)]
    ts += [t("x", trackers=("other.org",))]
    first = decide(ts, MAM, 20, 72, NOW)
    second = decide(apply(ts, first), MAM, 20, 72, NOW)
    assert second.actions == []
