"""Cross-run persistent state: which stories have already been seen, when the
pipeline last completed successfully, and an archive of each day's digest.
Committed back to the repo by the GitHub Actions workflow after a successful
run. See the v2 plan, Phases 14/16/17/18."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from text_match import normalize_headline, normalize_name

DEFAULT_STATE_PATH = Path("state/seen_stories.json")
DEFAULT_DIGEST_ARCHIVE_DIR = Path("state/digests")

_EMPTY_STATE = {"seen_stories": {}, "last_success": None}


def hash_story(entity_or_topic: str, headline: str) -> str:
    key = f"{normalize_name(entity_or_topic)}|{normalize_headline(headline)}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def load_state(path: Path = DEFAULT_STATE_PATH) -> dict:
    if not path.exists():
        return {"seen_stories": {}, "last_success": None}
    with path.open() as f:
        data = json.load(f)
    return {"seen_stories": data.get("seen_stories", {}), "last_success": data.get("last_success")}


def save_state(state: dict, path: Path = DEFAULT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def prune_state(state: dict, now: date, window_days: int = 14) -> dict:
    cutoff = now.toordinal() - window_days
    pruned_stories = {
        story_hash: entry
        for story_hash, entry in state["seen_stories"].items()
        if date.fromisoformat(entry["first_seen"]).toordinal() >= cutoff
    }
    return {"seen_stories": pruned_stories, "last_success": state["last_success"]}


def save_digest_archive(digest, run_date: date, dir: Path = DEFAULT_DIGEST_ARCHIVE_DIR) -> None:
    dir.mkdir(parents=True, exist_ok=True)
    path = dir / f"{run_date.isoformat()}.json"
    with path.open("w") as f:
        json.dump(asdict(digest), f, indent=2, sort_keys=True)


def hours_since(iso_timestamp: str, now: datetime) -> float:
    then = datetime.fromisoformat(iso_timestamp)
    return (now - then).total_seconds() / 3600
