import json

from fastapi.testclient import TestClient

from btmamager.core.config import ConfigStore, Settings
from btmamager.core.manager import Manager
from btmamager.core.policy import Torrent
from btmamager.web.app import create_app

AUTH = ("admin", "pw")


class FakeQBit:
    def __init__(self, torrents):
        self._torrents = torrents
        self.paused, self.resumed = [], []

    def connect(self):
        return "qBittorrent v5.0.0 (Web API 2.11.2)"

    def torrents(self):
        return self._torrents

    def _set(self, hashes, state):
        for t in self._torrents:
            if t.hash in hashes:
                t.state = state

    # The background loop may run too, so these change state like qBittorrent would.
    def pause(self, hashes):
        self.paused += hashes
        self._set(hashes, "stoppedUP")

    def resume(self, hashes):
        self.resumed += hashes
        self._set(hashes, "stalledUP")

    def queue_warning(self, target):
        return None

    def fill_mam_ids(self, torrents):
        pass


class FakeMam:
    def __init__(self, mam_id, snatches=(), fail=None):
        self.mam_id, self.snatches, self.fail = mam_id, list(snatches), fail

    def connect(self):
        if self.fail:
            raise self.fail
        return "MAM user mouse (uid 42)"

    def needs_seeding(self):
        return self.snatches


def client(tmp_path, locked=(), mam=None, **settings):
    base = Settings(_env_file=None, data_dir=str(tmp_path), web_password="pw", **settings)
    store = ConfigStore(base, locked=set(locked), path=tmp_path / "settings.json")
    qb = FakeQBit([
        Torrent("a", "MAM book", 10, "stoppedUP", 1.0, 1, ["t.myanonamouse.net"]),
        Torrent("b", "Other", 10, "uploading", 1.0, 1, ["tracker.example.org"]),
    ])
    mgr = Manager(store, client_factory=lambda s: qb, mam_factory=mam or (lambda mam_id: FakeMam(mam_id)))
    return TestClient(create_app(store, mgr)), qb


def test_auth_required_and_healthz_open(tmp_path):
    c, _ = client(tmp_path)
    with c:
        assert c.get("/healthz").status_code == 200
        assert c.get("/").status_code == 401
        assert c.get("/", auth=AUTH).status_code == 200


def test_unconfigured_does_nothing(tmp_path):
    c, qb = client(tmp_path)
    with c:
        page = c.get("/", auth=AUTH).text
        assert "Configuration needed" in page
        c.post("/run", auth=AUTH)
    assert qb.paused == [] and qb.resumed == []


def test_settings_save_then_live_run(tmp_path):
    c, qb = client(tmp_path)
    with c:
        r = c.post("/settings", auth=AUTH, data={"approved_tracker": "t.myanonamouse.net", "target_seeding": "5"},
                   follow_redirects=False)
        assert r.status_code == 303
        saved = json.loads((tmp_path / "settings.json").read_text())
        assert saved["approved_tracker"] == "t.myanonamouse.net" and saved["dry_run"] is False
        c.post("/run", auth=AUTH)
        assert c.get("/", auth=AUTH).status_code == 200
        # Saving wakes the background loop, so either it or Run now applied the changes, exactly once.
        runs = c.app.state.manager.history
        assert sum(len(r.actions) for r in runs if not r.dry_run) == 2
    assert qb.paused == ["b"] and qb.resumed == ["a"]


def test_dry_run_changes_nothing(tmp_path):
    c, qb = client(tmp_path, approved_tracker="t.myanonamouse.net", dry_run=True)
    with c:
        c.post("/run", auth=AUTH)
        assert "would pause" in c.get("/", auth=AUTH).text
    assert qb.paused == [] and qb.resumed == []


def test_locked_fields_disabled_and_bad_input_400(tmp_path):
    c, _ = client(tmp_path, locked={"qbit_url"}, qbit_url="http://qb:8080")
    with c:
        page = c.get("/settings", auth=AUTH).text
        assert 'name="qbit_url"' in page and "disabled" in page
        r = c.post("/settings", auth=AUTH, data={"target_seeding": "-5"})
        assert r.status_code == 400


def test_connection_button(tmp_path):
    c, _ = client(tmp_path)
    with c:
        r = c.post("/settings/test", auth=AUTH, data={})
        assert "Connected" in r.text


def test_mam_unsatisfied_seeds_and_failure_changes_nothing(tmp_path):
    from btmamager.core.mam import MamError, Snatch
    snatches = [Snatch(1, "MAM book"), Snatch(2, "Deleted one")]
    c, qb = client(tmp_path, mam=lambda mam_id: FakeMam(mam_id, snatches), approved_tracker="t.myanonamouse.net",
                   mam_id="abc", target_seeding=0, max_age_hours=0, dry_run=True)
    with c:
        c.post("/run", auth=AUTH)
        last = c.app.state.manager.last
        assert last.ok and last.counts["unsatisfied"] == 1
        assert {(a.hash, a.action) for a in last.actions} == {("a", "resume"), ("b", "pause")}
        assert any("Deleted one" in w for w in last.warnings)
        page = c.get("/", auth=AUTH).text
        assert "unsatisfied on MAM" in page
        assert "MAM user mouse" in c.post("/settings/test-mam", auth=AUTH, data={}).text

    c, qb = client(tmp_path, mam=lambda mam_id: FakeMam(mam_id, fail=MamError("expired")),
                   approved_tracker="t.myanonamouse.net", mam_id="abc", dry_run=False)
    with c:
        c.post("/run", auth=AUTH)
        assert "expired" in c.app.state.manager.last.error
    assert qb.paused == [] and qb.resumed == []


def test_mam_id_can_be_cleared(tmp_path):
    c, _ = client(tmp_path)
    with c:
        c.post("/settings", auth=AUTH, data={"mam_id": "abc"})
        assert c.app.state.store.current().mam_id == "abc"
        assert 'name="mam_id_clear"' in c.get("/settings", auth=AUTH).text
        c.post("/settings", auth=AUTH, data={"mam_id": ""})
        assert c.app.state.store.current().mam_id == "abc"
        c.post("/settings", auth=AUTH, data={"mam_id": "", "mam_id_clear": "on"})
        assert c.app.state.store.current().mam_id == ""
