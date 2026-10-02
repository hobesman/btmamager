"""Settings: environment first, then values saved from the web UI.

A setting given in the environment (or `.env`) wins and is shown read-only in the UI. Anything else
can be changed in the UI and is stored in DATA_DIR/settings.json.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import Field, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # qBittorrent
    qbit_url: str = "http://qbittorrent:8080"
    qbit_username: str = ""
    qbit_password: str = ""

    # Policy
    approved_tracker: str = ""
    target_seeding: int = Field(default=190, ge=0)
    max_age_hours: float = Field(default=72.0, ge=0)
    interval_minutes: float = Field(default=15.0, ge=1)
    dry_run: bool = True

    # Web (environment only)
    web_user: str = "admin"
    web_password: str = ""
    web_port: int = 8091

    # State (environment only)
    data_dir: str = "/data"

    @field_validator("approved_tracker")
    @classmethod
    def _host_only(cls, v: str) -> str:
        """Accept a pasted announce URL as well as a bare hostname."""
        v = v.strip().lower()
        if "://" in v:
            v = urlsplit(v).hostname or ""
        return v.split("/")[0].split(":")[0].strip(".")

    @field_validator("qbit_url")
    @classmethod
    def _url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if v and "://" not in v:
            v = "http://" + v
        return v

    @property
    def configured(self) -> bool:
        return bool(self.approved_tracker)

    def redacted(self) -> dict:
        out = self.model_dump()
        for k in SECRETS:
            if out.get(k):
                out[k] = "********"
        return out


SECRETS = {"qbit_password", "web_password"}


@dataclass(frozen=True)
class FieldInfo:
    name: str
    label: str
    help: str
    kind: str  # text | password | number | checkbox


# Settings the web UI can change (unless set in the environment), in display order.
EDITABLE: list[FieldInfo] = [
    FieldInfo("qbit_url", "qBittorrent URL", "Web UI address as seen from this container, e.g. http://qbittorrent:8080", "text"),
    FieldInfo("qbit_username", "qBittorrent username", "Leave empty if qBittorrent skips auth for this network", "text"),
    FieldInfo("qbit_password", "qBittorrent password", "Leave blank to keep the saved password", "password"),
    FieldInfo("approved_tracker", "Approved tracker", "Hostname, e.g. t.myanonamouse.net (subdomains match too)", "text"),
    FieldInfo("target_seeding", "Target seeding torrents", "Approved-tracker torrents to keep seeding", "number"),
    FieldInfo("max_age_hours", "Always seed if completed within (hours)", "These are kept even beyond the target", "number"),
    FieldInfo("interval_minutes", "Run every (minutes)", "Minimum 1", "number"),
    FieldInfo("dry_run", "Dry run", "Only show what would change; don't pause or resume anything", "checkbox"),
]
EDITABLE_NAMES = {f.name for f in EDITABLE}


def env_keys(env_file: str | Path | None = ".env") -> set[str]:
    """Setting names present in the process environment or the .env file."""
    keys = {k.lower() for k in os.environ}
    if env_file and Path(env_file).is_file():
        keys |= {k.lower() for k, v in dotenv_values(env_file).items() if v is not None}
    return keys & set(Settings.model_fields)


class ConfigError(ValueError):
    def __init__(self, errors: dict[str, str]):
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))
        self.errors = errors


class ConfigStore:
    """Merges defaults < settings.json < environment, and saves UI edits to settings.json."""

    def __init__(self, base: Settings | None = None, locked: set[str] | None = None,
                 path: str | Path | None = None):
        self.base = base or Settings()
        self.locked = env_keys() if locked is None else set(locked)
        self.path = Path(path) if path else Path(self.base.data_dir) / "settings.json"
        self._lock = threading.Lock()
        self._current = self._merge(self._read())

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except FileNotFoundError:
            return {}
        except (OSError, ValueError):
            return {}

    def _merge(self, saved: dict) -> Settings:
        values = self.base.model_dump()
        for k, v in saved.items():
            if k in EDITABLE_NAMES and k not in self.locked:
                values[k] = v
        try:
            return Settings(_env_file=None, **values)
        except ValidationError:
            # A bad file (edited by hand?) shouldn't stop the app; fall back to env/defaults.
            return self.base

    def current(self) -> Settings:
        with self._lock:
            return self._current

    def is_locked(self, name: str) -> bool:
        return name in self.locked

    def preview(self, form: dict[str, str]) -> Settings:
        """Settings as they would be with this form applied, without saving. Raises ConfigError."""
        return self._apply(form)[0]

    def save(self, form: dict[str, str]) -> Settings:
        """Validate and persist a submitted settings form. Raises ConfigError."""
        with self._lock:
            settings, saved = self._apply(form)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(saved, indent=2), encoding="utf-8")
            os.replace(tmp, self.path)
            self._current = settings
            return settings

    def _apply(self, form: dict[str, str]) -> tuple[Settings, dict]:
        errors = {k: "set by environment" for k in form if k in self.locked and k in EDITABLE_NAMES}
        if errors:
            raise ConfigError(errors)
        saved = {k: v for k, v in self._read().items() if k in EDITABLE_NAMES}
        for f in EDITABLE:
            if f.name in self.locked:
                continue
            if f.kind == "checkbox":
                saved[f.name] = str(form.get(f.name, "")).lower() in ("1", "true", "on", "yes")
            elif f.name in form:
                value = str(form[f.name]).strip()
                if f.kind == "password" and not value:
                    continue  # blank = keep existing
                saved[f.name] = value
        values = self.base.model_dump()
        values.update({k: v for k, v in saved.items() if k not in self.locked})
        try:
            settings = Settings(_env_file=None, **values)
        except ValidationError as e:
            raise ConfigError({str(err["loc"][0]): err["msg"] for err in e.errors()}) from None
        # Store normalised values (e.g. a pasted announce URL becomes just the hostname).
        dumped = settings.model_dump()
        return settings, {k: dumped[k] for k in saved}


@lru_cache
def get_store() -> ConfigStore:
    return ConfigStore()
