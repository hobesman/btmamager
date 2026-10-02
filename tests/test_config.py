import json

import pytest

from btmamager.core.config import ConfigError, ConfigStore, Settings


def make(tmp_path, locked=(), **env):
    base = Settings(_env_file=None, data_dir=str(tmp_path), **env)
    return ConfigStore(base, locked=set(locked), path=tmp_path / "settings.json")


def test_file_overrides_defaults(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"target_seeding": 50, "approved_tracker": "t.myanonamouse.net"}))
    s = make(tmp_path).current()
    assert s.target_seeding == 50
    assert s.configured


def test_env_overrides_file_and_locks(tmp_path):
    (tmp_path / "settings.json").write_text(json.dumps({"target_seeding": 50}))
    store = make(tmp_path, locked={"target_seeding"}, target_seeding=190)
    assert store.current().target_seeding == 190
    assert store.is_locked("target_seeding")
    with pytest.raises(ConfigError):
        store.save({"target_seeding": "10"})


def test_save_normalises_and_persists(tmp_path):
    store = make(tmp_path)
    store.save({"approved_tracker": "https://T.MyAnonamouse.net/announce.php?passkey=x",
                "target_seeding": "150", "dry_run": "on"})
    data = json.loads((tmp_path / "settings.json").read_text())
    assert data["approved_tracker"] == "t.myanonamouse.net"
    assert data["target_seeding"] == 150
    assert data["dry_run"] is True
    assert make(tmp_path).current().target_seeding == 150


def test_unchecked_checkbox_saves_false(tmp_path):
    store = make(tmp_path)
    store.save({"approved_tracker": "t.myanonamouse.net"})
    assert store.current().dry_run is False


def test_blank_password_keeps_saved_one(tmp_path):
    store = make(tmp_path)
    store.save({"qbit_password": "secret", "dry_run": "on"})
    store.save({"qbit_password": "", "dry_run": "on"})
    assert store.current().qbit_password == "secret"


def test_invalid_values_rejected(tmp_path):
    store = make(tmp_path)
    with pytest.raises(ConfigError) as e:
        store.save({"target_seeding": "-1", "interval_minutes": "abc"})
    assert set(e.value.errors) == {"target_seeding", "interval_minutes"}
    assert not (tmp_path / "settings.json").exists()


def test_corrupt_file_falls_back(tmp_path):
    (tmp_path / "settings.json").write_text("{not json")
    assert make(tmp_path).current().target_seeding == 190
