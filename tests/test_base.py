import logging

import pytest

from sources.base import MissingRequiredFieldError, check_columns, parse_criteria


def test_parse_criteria_all_sections_present():
    sections = {
        "NOMO context": "NOMO is a rewards app for teens.",
        "Region weighting": "BR is primary; US, UK, AU are growing.",
        "Competitor criteria": "Anything competing for youth loyalty attention.",
        "Partner criteria": "Has tradeable reward inventory.",
        "Do-not-suggest": "Meta\nTikTok\n- Snap",
    }
    criteria = parse_criteria(sections)

    assert criteria.nomo_context == "NOMO is a rewards app for teens."
    assert criteria.region_weighting == "BR is primary; US, UK, AU are growing."
    assert criteria.competitor_criteria == "Anything competing for youth loyalty attention."
    assert criteria.partner_criteria == "Has tradeable reward inventory."
    assert criteria.do_not_suggest == ["Meta", "TikTok", "Snap"]


def test_parse_criteria_tolerates_typo_in_header():
    # "Competitior" (typo) should still fuzzy-match "Competitor criteria".
    sections = {"Competitior criteria": "Anything competing for youth loyalty attention."}
    criteria = parse_criteria(sections)

    assert criteria.competitor_criteria == "Anything competing for youth loyalty attention."


def test_parse_criteria_missing_section_defaults_empty_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        criteria = parse_criteria({"NOMO context": "NOMO is a rewards app."})

    assert criteria.nomo_context == "NOMO is a rewards app."
    assert criteria.region_weighting == ""
    assert criteria.do_not_suggest == []
    assert "Region weighting" in caplog.text
    assert "missing" in caplog.text.lower()


def test_parse_criteria_unrecognized_header_ignored_and_warns(caplog):
    with caplog.at_level(logging.WARNING):
        criteria = parse_criteria(
            {
                "NOMO context": "NOMO is a rewards app.",
                "Random unrelated section": "some text that matches nothing",
            }
        )

    assert criteria.nomo_context == "NOMO is a rewards app."
    assert "Random unrelated section" in caplog.text


def test_check_columns_missing_required_raises():
    with pytest.raises(MissingRequiredFieldError, match="entity"):
        check_columns(
            present_headers={"Status"},
            column_map={"entity": "Partner (entity)", "status": "Status"},
            required_keys={"entity"},
            source_name="Partners tab",
        )


def test_check_columns_missing_optional_warns_not_raises(caplog):
    with caplog.at_level(logging.WARNING):
        check_columns(
            present_headers={"Partner (entity)"},
            column_map={"entity": "Partner (entity)", "status": "Status"},
            required_keys={"entity"},
            source_name="Partners tab",
        )

    assert "status" in caplog.text
    assert "Partners tab" in caplog.text


def test_check_columns_all_present_no_warning(caplog):
    with caplog.at_level(logging.WARNING):
        check_columns(
            present_headers={"Partner (entity)", "Status"},
            column_map={"entity": "Partner (entity)", "status": "Status"},
            required_keys={"entity"},
            source_name="Partners tab",
        )

    assert caplog.text == ""


def test_check_columns_handles_list_valued_headers():
    # region_flags maps to a list of headers (§6.1) — all five must be present.
    with pytest.raises(MissingRequiredFieldError):
        check_columns(
            present_headers={"All", "US", "UK"},
            column_map={"region_flags": ["All", "US", "UK", "BR", "AU"]},
            required_keys={"region_flags"},
            source_name="Watchlist tab",
        )
