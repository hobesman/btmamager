"""Thin wrapper over qbittorrent-api: read torrents, pause/resume them."""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

import qbittorrentapi

from .config import Settings
from .mam import mam_id_from_comment
from .policy import Torrent

log = logging.getLogger(__name__)
CHUNK = 200  # hashes per pause/resume request


def hostname(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


class QBit:
    def __init__(self, url: str, username: str = "", password: str = ""):
        self.client = qbittorrentapi.Client(
            host=url, username=username or None, password=password or None,
            REQUESTS_ARGS={"timeout": (10, 60)},
        )
        self._login = bool(username)
        # hash -> tracker hostnames. Trackers rarely change, and paused torrents report no
        # current tracker, so looking them up every cycle would cost one request per torrent.
        self._hosts: dict[str, list[str]] = {}
        # hash -> MAM torrent id from the comment (None if it has no MAM link). Comments never change.
        self._mam_ids: dict[str, int | None] = {}

    @classmethod
    def from_settings(cls, s: Settings) -> "QBit":
        return cls(s.qbit_url, s.qbit_username, s.qbit_password)

    def connect(self) -> str:
        """Log in (if credentials are set) and return the qBittorrent version."""
        if self._login:
            self.client.auth_log_in()
        return f"qBittorrent {self.client.app_version()} (Web API {self.client.app_web_api_version()})"

    def torrents(self) -> list[Torrent]:
        out = []
        seen = set()
        for t in self.client.torrents_info():
            h = t["hash"]
            seen.add(h)
            if "comment" in t:  # qBittorrent 5 lists comments; older versions need a request each
                self._mam_ids[h] = mam_id_from_comment(t["comment"])
            out.append(Torrent(
                hash=h, name=t.get("name", h), size=int(t.get("size") or 0), state=t.get("state", ""),
                progress=float(t.get("progress") or 0), completion_on=int(t.get("completion_on") or 0),
                trackers=self._trackers(h, t.get("tracker") or ""), mam_id=self._mam_ids.get(h),
            ))
        for cache in (self._hosts, self._mam_ids):
            for h in set(cache) - seen:
                del cache[h]
        return out

    def fill_mam_ids(self, torrents: list[Torrent]) -> None:
        """Set `mam_id` from each torrent's comment, fetching comments not seen before."""
        for t in torrents:
            if t.hash not in self._mam_ids:
                comment = self.client.torrents_properties(torrent_hash=t.hash).get("comment") or ""
                self._mam_ids[t.hash] = mam_id_from_comment(comment)
            t.mam_id = self._mam_ids[t.hash]

    def _trackers(self, h: str, current: str) -> list[str]:
        if current:
            host = hostname(current)
            cached = self._hosts.get(h)
            if cached and host in cached:
                return cached
            if host:
                # The working tracker is enough to classify the torrent; keep any others we knew.
                self._hosts[h] = sorted({host, *(cached or [])})
                return self._hosts[h]
        if h not in self._hosts:
            urls = [tr["url"] for tr in self.client.torrents_trackers(torrent_hash=h)]
            # Skip the "** [DHT] **" / PeX / LSD pseudo-entries.
            self._hosts[h] = sorted({hostname(u) for u in urls if "://" in u} - {""})
        return self._hosts[h]

    def pause(self, hashes: list[str]) -> None:
        stop = getattr(self.client, "torrents_stop", None) or self.client.torrents_pause
        for i in range(0, len(hashes), CHUNK):
            stop(torrent_hashes=hashes[i:i + CHUNK])

    def resume(self, hashes: list[str]) -> None:
        start = getattr(self.client, "torrents_start", None) or self.client.torrents_resume
        for i in range(0, len(hashes), CHUNK):
            start(torrent_hashes=hashes[i:i + CHUNK])

    def queue_warning(self, target: int) -> str | None:
        """Warn when qBittorrent's queue limits would hold back some of the torrents we resume."""
        try:
            p = self.client.app_preferences()
        except Exception as e:  # informational only
            log.debug("Could not read preferences: %s", e)
            return None
        if not p.get("queueing_enabled"):
            return None
        limits = [(n, p.get(k, -1)) for n, k in (("Maximum active uploads", "max_active_uploads"),
                                                 ("Maximum active torrents", "max_active_torrents"))]
        low = [f"{n} = {v}" for n, v in limits if v is not None and 0 <= v < target]
        if not low:
            return None
        return (f"qBittorrent's torrent queueing is on ({', '.join(low)}), below the target of {target}. "
                "Some resumed torrents will sit in 'queued' instead of seeding. Raise the limits or "
                "turn off queueing under Options → BitTorrent.")
