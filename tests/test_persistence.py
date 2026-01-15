import json
import os
import time
from pathlib import Path

import pytest

from src.brain import MarketBrain


def _create_dummy_backup(backups_dir: Path, name: str, mtime: float, content: str = "old"):
    p = backups_dir / name
    p.write_text(content)
    # set modification time
    os.utime(p, (mtime, mtime))
    return p


def test_rotate_backups_keeps_newest_five(tmp_path: Path):
    data_dir = tmp_path / "data"
    backups_dir = data_dir / "backups"
    backups_dir.mkdir(parents=True)

    # create a dummy primary state file so rotate will copy it
    state_file = data_dir / "brain_state.json"
    state_file.write_text(json.dumps({"initial": True}))

    # create 7 backup files with increasing mtimes (oldest first)
    now = time.time()
    files = []
    for i in range(7):
        ts = now - (7 - i) * 60  # spaced by 60s
        name = f"brain_state_20200101T00000{i}Z.json"
        p = _create_dummy_backup(backups_dir, name, ts, content=f"backup-{i}")
        files.append(p)

    # construct a MarketBrain object without running __init__ (avoid repo writes)
    brain = MarketBrain.__new__(MarketBrain)
    brain.state_path = str(state_file)

    # call rotate, keep=5
    brain._rotate_backups(keep=5)

    remaining = sorted(backups_dir.glob("brain_state_*.json"))
    # assert exactly 5 remain
    assert len(remaining) == 5

    # newest 5 should remain: files[2:] (indices 2..6)
    expected_names = {p.name for p in files[2:]}
    remaining_names = {p.name for p in remaining}
    assert remaining_names == expected_names


def test_save_state_creates_backup_before_replace(tmp_path: Path):
    data_dir = tmp_path / "data"
    backups_dir = data_dir / "backups"
    backups_dir.mkdir(parents=True)

    state_file = data_dir / "brain_state.json"
    # write an existing primary state
    old_content = {"last": "old"}
    state_file.write_text(json.dumps(old_content))

    # prepare brain object without __init__ side-effects
    brain = MarketBrain.__new__(MarketBrain)
    brain.state_path = str(state_file)
    # new state to be saved
    brain.last_signatures = {"whale1": "sig_new"}

    # ensure no backups exist initially
    assert len(list(backups_dir.glob("*"))) == 0

    # call save_state which should create a backup of the old file, then replace
    brain._save_state()

    # primary state file should exist and contain the new content
    new_text = json.loads(state_file.read_text())
    assert new_text == brain.last_signatures

    # backups dir should contain at least one file
    backups = sorted(backups_dir.glob("brain_state_*.json"))
    assert len(backups) >= 1

    # newest backup should contain the previous content
    newest = backups[-1]
    backed = json.loads(newest.read_text())
    assert backed == old_content
