import pytest

from btmamager.core import mam as mam_mod
from btmamager.core.mam import Mam, MamError, Snatch, mam_id_from_comment, match
from btmamager.core.policy import Torrent

MAM = "t.myanonamouse.net"


class FakeResponse:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


def fake_mam(monkeypatch, lists, user=None):
    """A Mam whose HTTP calls are answered from `lists` (type -> rows)."""
    calls = []

    def get(url, params=None, timeout=None):
        calls.append((url, dict(params or {})))
        if url.endswith("/jsonLoad.php"):
            return FakeResponse(user or {"uid": 42, "username": "mouse", "unsat": {"count": 3, "limit": 50}})
        rows = lists.get(params["type"], [])
        page = params["iteration"]
        return FakeResponse({"rows": rows[page * mam_mod.PAGE:(page + 1) * mam_mod.PAGE]})

    m = Mam("secret")
    monkeypatch.setattr(m.http, "get", get)
    return m, calls


def test_connect_reports_user(monkeypatch):
    m, _ = fake_mam(monkeypatch, {})
    assert m.connect() == "MAM user mouse (uid 42, 3 unsatisfied of 50 allowed)"
    assert m.http.cookies.get("mam_id") == "secret"


def test_needs_seeding_pages_and_dedupes(monkeypatch):
    big = [{"id": i, "title": f"Book {i}"} for i in range(300)]
    m, calls = fake_mam(monkeypatch, {"seedUnsat": big, "inactUnsat": [{"id": 7, "title": "Book 7"}],
                                      "inactHnr": [{"id": "999", "title": "Late", "hash": "AB" * 20}]})
    got = m.needs_seeding()
    assert len(got) == 301
    assert Snatch(999, "Late", "ab" * 20, "inactHnr") in got
    unsat_pages = [p["iteration"] for u, p in calls if p.get("type") == "seedUnsat"]
    assert unsat_pages == [0, 1]
    assert all(p["uid"] == 42 for u, p in calls if "type" in p)


def test_errors_raise(monkeypatch):
    m, _ = fake_mam(monkeypatch, {}, user={"error": "Request not via correct session type"})
    with pytest.raises(MamError, match="session type"):
        m.connect()
    m, _ = fake_mam(monkeypatch, {}, user={"username": "x"})
    with pytest.raises(MamError, match="user id"):
        m.connect()


def test_comment_link():
    assert mam_id_from_comment("https://www.myanonamouse.net/t/123456") == 123456
    assert mam_id_from_comment("downloaded from elsewhere") is None
    assert mam_id_from_comment("") is None


def t(h, name, mam_id=None):
    return Torrent(h, name, 1, "stoppedUP", 1.0, 1, [MAM], mam_id)


def test_match_by_hash_id_then_name():
    ts = [t("a" * 40, "Whatever"), t("b", "Other name", mam_id=5), t("c", "The Book: Vol. 1"),
          t("d", "Same Title", mam_id=8)]
    found, missing = match([
        Snatch(1, "x", "a" * 40),
        Snatch(5, "Different title"),
        Snatch(None, "the book vol 1"),
        Snatch(9, "Same Title"),  # d is known to be #8, so not this one
        Snatch(10, "Gone"),
    ], ts)
    assert found == {"a" * 40, "b", "c"}
    assert [s.title for s in missing] == ["Same Title", "Gone"]
