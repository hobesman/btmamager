# btmamager

Keeps qBittorrent seeding only MyAnonamouse (or any one tracker's) torrents, and only as many as you want.

Each run, for **completed** torrents:

1. **Other trackers**: anything seeding from a tracker other than `APPROVED_TRACKER` is paused.
2. **Must seed**: approved-tracker torrents always seed if
   - MAM lists them as **unsatisfied** or **hit & run** (only when `MAM_ID` is set; see below), or
   - they completed within `MAX_AGE_HOURS` (set it to `0` to rely on MAM alone).
3. **Largest**: the rest of `TARGET_SEEDING` is filled with the largest remaining approved-tracker torrents.
   Paused ones are resumed if needed; everything else from that tracker is paused.

Example: target 190, 72 h, `t.myanonamouse.net`. With 10 MAM torrents completed in the last 72 h, those 10
seed plus the 180 largest older MAM torrents, and every non-MAM torrent is paused. Downloads, checking and
errored torrents are never touched. A torrent counts as MAM if **any** of its trackers is that host (or a
subdomain of it).

## Unsatisfied torrents from MAM

Give btmamager a MAM session and it asks MAM which of your torrents still need seed time, instead of
(or as well as) guessing from their age:

1. On MAM, open **Preferences → Security** and create a session for the IP btmamager connects from
   (an ASN-locked session survives a changing IP). Copy its `mam_id`.
2. Paste it into **Settings → MAM session** (or `MAM_ID` in `.env`) and press **Test MAM connection**.

Each run then reads your **Seeding/Inactive, Unsatisfied** and **Hit & Run** lists from your MAM profile and
keeps every matching torrent seeding, even past the target. MAM's list has no infohash, so torrents are
matched by the `myanonamouse.net/t/<id>` link in the torrent's comment, or else by name. Anything MAM lists
that can't be found in qBittorrent is shown as a warning on the Status page.

If MAM can't be reached or rejects the session, that run changes nothing and shows the error, so a lapsed
session never pauses a torrent that still owes seed time. This uses the endpoints behind MAM's own profile
pages, which aren't a documented API and could change.

## Run it

```bash
mkdir btmamager && cd btmamager
curl -fsSLO https://raw.githubusercontent.com/hobesman/btmamager/main/docker-compose.example.yml
curl -fsSLO https://raw.githubusercontent.com/hobesman/btmamager/main/.env.example
mv docker-compose.example.yml docker-compose.yml && mv .env.example .env
# edit .env: set WEB_PASSWORD and QBIT_URL; edit docker-compose.yml: the network qBittorrent is on
docker compose pull && docker compose up -d
```

Open `http://<host>:8091`, log in with `WEB_USER` / `WEB_PASSWORD`, and fill in the rest on **Settings**.

It starts in **dry run**: the Status page lists what it *would* pause and resume. When that looks right,
turn dry run off (Settings, or `DRY_RUN=false`) and press **Run now**.

Update: `docker compose pull && docker compose up -d`.

## Settings

Any setting in the environment / `.env` wins and is shown locked in the web UI. Anything not set there is
editable in the UI and saved to `/data/settings.json`.

| Variable | Default | |
|---|---|---|
| `WEB_USER` / `WEB_PASSWORD` | `admin` / – | Web UI login (env only; required) |
| `WEB_PORT` | `8091` | Env only |
| `QBIT_URL` | `http://qbittorrent:8080` | qBittorrent Web UI as reachable from this container |
| `QBIT_USERNAME` / `QBIT_PASSWORD` | empty | Leave empty if qBittorrent bypasses auth for the Docker subnet |
| `APPROVED_TRACKER` | – | e.g. `t.myanonamouse.net`; a pasted announce URL is trimmed to its host |
| `TARGET_SEEDING` | `190` | Approved-tracker torrents to seed |
| `MAM_ID` | empty | MAM session cookie; when set, unsatisfied / hit & run torrents always seed |
| `MAX_AGE_HOURS` | `72` | Completed within this window → always seed, even past the target; `0` = off |
| `INTERVAL_MINUTES` | `15` | How often to run |
| `DRY_RUN` | `true` | Only report what would change |

A qBittorrent password or `mam_id` entered in the UI is stored in plain text in `data/settings.json`; put it
in `.env` if you'd rather not.

**qBittorrent queueing**: if *Options → BitTorrent → Torrent Queueing* is on with *Maximum active uploads* or
*Maximum active torrents* below your target, some resumed torrents will sit as "queued" instead of seeding.
The Status page warns about this.

## CLI

```bash
docker exec btmamager btmamager run-once --dry-run
```

## Development

```bash
pip install -e ".[dev]"
pytest -q
```

Pushes to `main` run the tests and publish `ghcr.io/hobesman/btmamager:latest` (multi-arch). Tag `vX.Y.Z` for
versioned images. The first time the package is published, make it public under GitHub → your profile →
Packages → btmamager → Package settings → Change visibility, so Docker hosts can pull without logging in.
