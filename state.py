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

from models import RejectedCandidate
from text_match import normalize_headline, normalize_name

DEFAULT_STATE_PATH = Path("state/seen_stories.json")
DEFAULT_DIGEST_ARCHIVE_DIR = Path("state/digests")
DEFAULT_REJECTED_CANDIDATES_PATH = Path("state/rejected_candidates.json")
DEFAULT_METRICS_DIR = Path("state/metrics")

# How long a rejected candidate is suppressed from re-entering synthesis
# (excluded in main.py's exclude_previously_rejected). After this, it's
# eligible for re-scouting/re-evaluation again, so a genuine change in the
# story isn't stuck behind an old rejection.
REJECTION_SUPPRESSION_DAYS = 14
# How long a rejected candidate stays in the persisted record at all (used
# for the "worth a second look" reconsider list). Not the same as the
# suppression window: a candidate can keep getting
# re-rejected and stay "fresh" here well past 14 days, as long as it keeps
# resurfacing; it only drops out after 90 days with no fresh rejection.
REJECTION_RETENTION_DAYS = 90

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


def load_rejected_candidates(path: Path = DEFAULT_REJECTED_CANDIDATES_PATH) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f)


def save_rejected_candidates(state: dict, path: Path = DEFAULT_REJECTED_CANDIDATES_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def upsert_rejected_candidate(state: dict, candidate: RejectedCandidate, today: date) -> dict:
    """Adds or refreshes a rejected candidate, keyed by normalized name.
    Preserves first_rejected and last_shown across repeat rejections of the
    same candidate; everything else (reason, confidence, etc.) reflects the
    latest rejection."""
    key = normalize_name(candidate.name)
    existing = state.get(key)
    updated = dict(state)
    updated[key] = {
        **asdict(candidate),
        "first_rejected": existing["first_rejected"] if existing else today.isoformat(),
        "last_rejected": today.isoformat(),
        "last_shown": existing.get("last_shown") if existing else None,
        "reject_count": (existing.get("reject_count", 0) if existing else 0) + 1,
        "shown_count": existing.get("shown_count", 0) if existing else 0,
    }
    return updated


def is_suppressed(entry: dict, today: date, window_days: int = REJECTION_SUPPRESSION_DAYS) -> bool:
    """Whether a rejected candidate should still be excluded from re-entering
    synthesis — true while its most recent rejection is within the
    suppression window."""
    last_rejected = date.fromisoformat(entry["last_rejected"])
    return (today - last_rejected).days < window_days


def prune_rejected_candidates(
    state: dict, today: date, window_days: int = REJECTION_RETENTION_DAYS
) -> dict:
    return {
        key: entry
        for key, entry in state.items()
        if (today - date.fromisoformat(entry["last_rejected"])).days < window_days
    }


def mark_shown(state: dict, name: str, today: date) -> dict:
    key = normalize_name(name)
    if key not in state:
        return state
    updated = dict(state)
    entry = updated[key]
    updated[key] = {
        **entry,
        "last_shown": today.isoformat(),
        "shown_count": entry.get("shown_count", 0) + 1,
    }
    return updated


def entry_to_candidate(entry: dict) -> RejectedCandidate:
    return RejectedCandidate(
        name=entry["name"],
        suggested_type=entry["suggested_type"],
        region=entry["region"],
        why_fits=entry["why_fits"],
        source_url=entry["source_url"],
        reason=entry["reason"],
        category=entry.get("category"),
        confidence=entry.get("confidence"),
        reject_count=entry.get("reject_count", 0),
        shown_count=entry.get("shown_count", 0),
    )


def append_metrics(
    calls: list[dict], run_date: date, dir: Path = DEFAULT_METRICS_DIR
) -> None:
    """Appends one JSON line per recorded API call to state/metrics/<date>.jsonl
    — a day can have multiple runs (manual re-triggers), so this appends
    rather than overwrites like save_digest_archive does."""
    if not calls:
        return
    dir.mkdir(parents=True, exist_ok=True)
    path = dir / f"{run_date.isoformat()}.jsonl"
    with path.open("a") as f:
        for call in calls:
            f.write(json.dumps(call, sort_keys=True) + "\n")
