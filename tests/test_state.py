from datetime import date, datetime

import state
from models import Digest, RejectedCandidate


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


def _rejected(name="Bolt", **overrides) -> RejectedCandidate:
    fields = {
        "name": name,
        "suggested_type": "Competitor",
        "region": "US",
        "why_fits": "Fits the criteria.",
        "source_url": "https://example.com/news",
        "reason": "Too similar to an existing competitor.",
    }
    fields.update(overrides)
    return RejectedCandidate(**fields)


def test_save_and_load_rejected_candidates_round_trips(tmp_path):
    path = tmp_path / "rejected_candidates.json"
    original = {
        "bolt": {
            "name": "Bolt",
            "suggested_type": "Competitor",
            "region": "US",
            "why_fits": "Fits.",
            "source_url": "https://example.com/a",
            "reason": "Weak fit.",
            "category": None,
            "confidence": "medium",
            "first_rejected": "2026-07-20",
            "last_rejected": "2026-07-20",
            "last_shown": None,
        }
    }

    state.save_rejected_candidates(original, path=path)
    loaded = state.load_rejected_candidates(path=path)

    assert loaded == original


def test_load_rejected_candidates_missing_file_returns_empty_dict(tmp_path):
    path = tmp_path / "does_not_exist.json"
    assert state.load_rejected_candidates(path=path) == {}


def test_upsert_rejected_candidate_new_entry_sets_first_and_last_rejected():
    updated = state.upsert_rejected_candidate({}, _rejected(), today=date(2026, 7, 30))

    entry = updated["bolt"]
    assert entry["first_rejected"] == "2026-07-30"
    assert entry["last_rejected"] == "2026-07-30"
    assert entry["last_shown"] is None
    assert entry["reason"] == "Too similar to an existing competitor."
    assert entry["reject_count"] == 1
    assert entry["shown_count"] == 0


def test_upsert_rejected_candidate_repeat_preserves_first_rejected_and_last_shown():
    existing = {
        "bolt": {
            **{
                "name": "Bolt",
                "suggested_type": "Competitor",
                "region": "US",
                "why_fits": "Fits.",
                "source_url": "https://example.com/a",
                "category": None,
                "confidence": "medium",
            },
            "reason": "Weak fit.",
            "first_rejected": "2026-07-01",
            "last_rejected": "2026-07-01",
            "last_shown": "2026-07-10",
            "reject_count": 2,
            "shown_count": 3,
        }
    }

    updated = state.upsert_rejected_candidate(
        existing, _rejected(reason="Still no traction."), today=date(2026, 7, 30)
    )

    entry = updated["bolt"]
    assert entry["first_rejected"] == "2026-07-01"  # preserved
    assert entry["last_rejected"] == "2026-07-30"  # refreshed
    assert entry["last_shown"] == "2026-07-10"  # preserved
    assert entry["reason"] == "Still no traction."  # refreshed
    assert entry["reject_count"] == 3  # incremented
    assert entry["shown_count"] == 3  # preserved (only mark_shown touches this)


def test_is_suppressed_true_within_window():
    entry = {"last_rejected": "2026-07-25"}
    assert state.is_suppressed(entry, today=date(2026, 7, 30), window_days=14) is True


def test_is_suppressed_false_past_window():
    entry = {"last_rejected": "2026-07-10"}
    assert state.is_suppressed(entry, today=date(2026, 7, 30), window_days=14) is False


def test_prune_rejected_candidates_drops_entries_past_window():
    data = {
        "recent": {"last_rejected": "2026-07-25"},
        "stale": {"last_rejected": "2026-04-01"},
    }

    pruned = state.prune_rejected_candidates(data, today=date(2026, 7, 30), window_days=90)

    assert set(pruned) == {"recent"}


def test_mark_shown_sets_last_shown_for_matching_entry():
    data = {"bolt": {"name": "Bolt", "last_shown": None}}

    updated = state.mark_shown(data, "Bolt", today=date(2026, 7, 30))

    assert updated["bolt"]["last_shown"] == "2026-07-30"


def test_mark_shown_increments_shown_count():
    data = {"bolt": {"name": "Bolt", "last_shown": "2026-07-10", "shown_count": 2}}

    updated = state.mark_shown(data, "Bolt", today=date(2026, 7, 30))

    assert updated["bolt"]["shown_count"] == 3


def test_mark_shown_defaults_shown_count_to_zero_when_absent():
    data = {"bolt": {"name": "Bolt", "last_shown": None}}

    updated = state.mark_shown(data, "Bolt", today=date(2026, 7, 30))

    assert updated["bolt"]["shown_count"] == 1


def test_mark_shown_no_matching_entry_is_a_noop():
    data = {"bolt": {"name": "Bolt", "last_shown": None}}

    updated = state.mark_shown(data, "Lyft", today=date(2026, 7, 30))

    assert updated == data


def test_entry_to_candidate_round_trips_fields():
    entry = {
        "name": "Bolt",
        "suggested_type": "Competitor",
        "region": "US",
        "why_fits": "Fits.",
        "source_url": "https://example.com/a",
        "reason": "Weak fit.",
        "category": "mobility",
        "confidence": "medium",
        "reject_count": 4,
        "shown_count": 2,
    }

    candidate = state.entry_to_candidate(entry)

    assert candidate == RejectedCandidate(
        name="Bolt",
        suggested_type="Competitor",
        region="US",
        why_fits="Fits.",
        source_url="https://example.com/a",
        reason="Weak fit.",
        category="mobility",
        confidence="medium",
        reject_count=4,
        shown_count=2,
    )


def test_entry_to_candidate_defaults_counts_to_zero_when_absent():
    entry = {
        "name": "Bolt",
        "suggested_type": "Competitor",
        "region": "US",
        "why_fits": "Fits.",
        "source_url": "https://example.com/a",
        "reason": "Weak fit.",
    }

    candidate = state.entry_to_candidate(entry)

    assert candidate.reject_count == 0
    assert candidate.shown_count == 0
