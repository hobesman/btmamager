"""MyAnonamouse: which of your torrents still need seeding (unsatisfied or hit & run).

MAM has no documented API for this. We use what its own pages use:

* `/jsonLoad.php` returns your user id (and works with an API session's `mam_id` cookie).
* `/json/loadUserDetailsTorrents.php?uid=..&type=..` lists the torrents behind the tabs on your profile,
  250 per page. Rows carry the MAM torrent id and title but no infohash, so torrents are matched to
  qBittorrent by the `/t/<id>` link in the torrent's comment, falling back to the name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import requests

BASE = "https://www.myanonamouse.net"
# Profile lists of torrents that still need seed time: unsatisfied or hit & run, seeding or not.
NEED_SEEDING = ("seedUnsat", "inactUnsat", "seedHnr", "inactHnr")
PAGE = 250
MAX_PAGES = 40  # 10,000 torrents per list
TORRENT_LINK = re.compile(r"myanonamouse\.net/t/(\d+)", re.I)
HASH = re.compile(r"^[0-9a-f]{40}$", re.I)


class MamError(RuntimeError):
    pass


@dataclass(frozen=True)
class Snatch:
    id: int | None
    title: str
    hash: str = ""  # only if MAM ever includes it
    kind: str = ""  # which list it came from, e.g. seedUnsat


def mam_id_from_comment(comment: str) -> int | None:
    m = TORRENT_LINK.search(comment or "")
    return int(m.group(1)) if m else None


def norm(name: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", (name or "").lower())


class Mam:
    def __init__(self, mam_id: str, base: str = BASE):
        self.base = base
        self.http = requests.Session()
        self.http.headers["User-Agent"] = "btmamager"
        # MAM refreshes the cookie in its responses; the session keeps the latest one.
        self.http.cookies.set("mam_id", mam_id.strip(), domain=".myanonamouse.net")
        self._uid: int | None = None
        self._user = ""

    def _get(self, path: str, **params) -> dict:
        try:
            r = self.http.get(self.base + path, params=params, timeout=(10, 60))
        except requests.RequestException as e:
            raise MamError(f"could not reach MAM: {e}") from None
        if r.status_code in (401, 403):
            raise MamError(f"MAM refused the session (HTTP {r.status_code}); create a new mam_id")
        try:
            r.raise_for_status()
            data = r.json()
        except (requests.HTTPError, ValueError):
            raise MamError(f"unexpected response from {path} (HTTP {r.status_code})") from None
        if not isinstance(data, dict):
            raise MamError(f"unexpected response from {path}")
        if data.get("error") or data.get("success") is False:
            raise MamError(f"{path}: {data.get('error') or 'request failed'}")
        return data

    def connect(self) -> str:
        """Check the session and return who it belongs to."""
        data = self._get("/jsonLoad.php", snatch_summary="")
        try:
            self._uid = int(data["uid"])
        except (KeyError, TypeError, ValueError):
            raise MamError("MAM did not return a user id; is mam_id a valid session?") from None
        self._user = str(data.get("username") or self._uid)
        unsat = data.get("unsat") if isinstance(data.get("unsat"), dict) else {}
        extra = f", {unsat['count']} unsatisfied of {unsat['limit']} allowed" \
            if {"count", "limit"} <= set(unsat) else ""
        return f"MAM user {self._user} (uid {self._uid}{extra})"

    def needs_seeding(self) -> list[Snatch]:
        if self._uid is None:
            self.connect()
        out: dict[tuple, Snatch] = {}
        for kind in NEED_SEEDING:
            for row in self._rows(kind):
                s = self._snatch(row, kind)
                out.setdefault((s.id, s.title), s)
        return list(out.values())

    def _rows(self, kind: str) -> list[dict]:
        rows, seen = [], set()
        for page in range(MAX_PAGES):
            data = self._get("/json/loadUserDetailsTorrents.php", uid=self._uid, type=kind, iteration=page)
            batch = data.get("rows")
            if not isinstance(batch, list):
                raise MamError(f"no torrent list in the {kind} response")
            new = [r for r in batch if isinstance(r, dict) and (r.get("id"), r.get("title")) not in seen]
            seen.update((r.get("id"), r.get("title")) for r in new)
            rows += new
            if not new or len(batch) < PAGE:
                return rows
        raise MamError(f"more than {MAX_PAGES * PAGE} torrents in the {kind} list")

    @staticmethod
    def _snatch(row: dict, kind: str) -> Snatch:
        try:
            tid = int(row.get("id"))
        except (TypeError, ValueError):
            tid = None
        h = next((str(row[k]).lower() for k in ("hash", "infohash", "info_hash")
                  if HASH.match(str(row.get(k) or ""))), "")
        return Snatch(tid, str(row.get("title") or ""), h, kind)


def match(snatches: list[Snatch], torrents) -> tuple[set[str], list[Snatch]]:
    """qBittorrent hashes of the torrents MAM says need seeding, and the snatches not found."""
    by_hash = {t.hash.lower(): t for t in torrents}
    by_id: dict[int, list] = {}
    by_name: dict[str, list] = {}
    for t in torrents:
        if t.mam_id is not None:
            by_id.setdefault(t.mam_id, []).append(t)
        else:  # a torrent whose MAM id we know can't be a different snatch with the same name
            by_name.setdefault(norm(t.name), []).append(t)

    found: set[str] = set()
    missing: list[Snatch] = []
    for s in snatches:
        hits = ([by_hash[s.hash]] if s.hash in by_hash else
                by_id.get(s.id) if s.id is not None and s.id in by_id else
                by_name.get(norm(s.title), []) if norm(s.title) else [])
        if hits:
            found.update(t.hash for t in hits)
        else:
            missing.append(s)
    return found, missing
