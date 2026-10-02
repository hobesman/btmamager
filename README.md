# btmamager

Keeps qBittorrent seeding only MyAnonamouse (or any one tracker's) torrents, and only as many as you want.

Each run, for **completed** torrents:

1. **Other trackers**: anything seeding from a tracker other than `APPROVED_TRACKER` is paused.
2. **Recent**: approved-tracker torrents that completed within `MAX_AGE_HOURS` always seed.
3. **Largest**: the rest of `TARGET_SEEDING` is filled with the largest remaining approved-tracker torrents.
   Paused ones are resumed if needed; everything else from that tracker is paused.

Example: target 190, 72 h, `t.myanonamouse.net`. With 10 MAM torrents completed in the last 72 h, those 10
seed plus the 180 largest older MAM torrents, and every non-MAM torrent is paused. Downloads, checking and
errored torrents are never touched. A torrent counts as MAM if **any** of its trackers is that host (or a
subdomain of it).

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
| `MAX_AGE_HOURS` | `72` | Completed within this window → always seed, even past the target |
| `INTERVAL_MINUTES` | `15` | How often to run |
| `DRY_RUN` | `true` | Only report what would change |

A qBittorrent password entered in the UI is stored in plain text in `data/settings.json`; put it in `.env`
if you'd rather not.

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
