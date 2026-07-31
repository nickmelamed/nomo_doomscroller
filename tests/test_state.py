from datetime import date, datetime

import state
from models import Digest


def test_hash_story_stable_for_identical_input():
    a = state.hash_story("Uber", "Uber One expands rewards")
    b = state.hash_story("Uber", "Uber One expands rewards")
    assert a == b


def test_hash_story_differs_when_entity_differs():
    a = state.hash_story("Uber", "Expands rewards")
    b = state.hash_story("Lyft", "Expands rewards")
    assert a != b


def test_hash_story_differs_when_headline_differs():
    a = state.hash_story("Uber", "Uber One expands rewards")
    b = state.hash_story("Uber", "Uber cuts driver pay")
    assert a != b


def test_hash_story_normalizes_headline_variants():
    a = state.hash_story("Uber", "Uber One expands ticketing rewards")
    b = state.hash_story("Uber", "Uber One expands ticketing rewards!")
    assert a == b


def test_save_and_load_state_round_trips(tmp_path):
    path = tmp_path / "seen_stories.json"
    original = {
        "seen_stories": {
            "abc123": {
                "first_seen": "2026-07-20",
                "headline": "Some story",
                "entity_or_topic": "Uber",
            }
        },
        "last_success": "2026-07-30T13:00:00+00:00",
    }

    state.save_state(original, path=path)
    loaded = state.load_state(path=path)

    assert loaded == original


def test_load_state_missing_file_returns_empty_state(tmp_path):
    path = tmp_path / "does_not_exist.json"
    loaded = state.load_state(path=path)
    assert loaded == {"seen_stories": {}, "last_success": None}


def test_prune_state_drops_entries_past_window():
    data = {
        "seen_stories": {
            "recent": {"first_seen": "2026-07-25", "headline": "h1", "entity_or_topic": "Uber"},
            "stale": {"first_seen": "2026-07-01", "headline": "h2", "entity_or_topic": "Uber"},
        },
        "last_success": None,
    }

    pruned = state.prune_state(data, now=date(2026, 7, 30), window_days=14)

    assert set(pruned["seen_stories"]) == {"recent"}


def test_prune_state_keeps_boundary_entry():
    data = {
        "seen_stories": {
            "boundary": {
                "first_seen": "2026-07-16",  # exactly 14 days before now
                "headline": "h",
                "entity_or_topic": "Uber",
            },
        },
        "last_success": None,
    }

    pruned = state.prune_state(data, now=date(2026, 7, 30), window_days=14)

    assert set(pruned["seen_stories"]) == {"boundary"}


def test_save_and_load_digest_archive_round_trips(tmp_path):
    digest = Digest(
        quiet_day=False,
        competition=[],
        industry=[],
        partner_prospects=[],
        new_candidates=[],
        tracking_counts={"competitors": 1, "partner_prospects": 2},
    )

    state.save_digest_archive(digest, run_date=date(2026, 7, 30), dir=tmp_path)

    archived_path = tmp_path / "2026-07-30.json"
    assert archived_path.exists()
    import json

    with archived_path.open() as f:
        data = json.load(f)
    assert data["tracking_counts"] == {"competitors": 1, "partner_prospects": 2}


def test_hours_since_computes_elapsed_hours():
    then = "2026-07-28T13:00:00+00:00"
    now = datetime.fromisoformat("2026-07-30T15:00:00+00:00")
    assert state.hours_since(then, now) == 50.0
