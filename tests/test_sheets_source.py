import logging

import gspread
import pytest

from sources.sheets_source import SheetsSource, SheetsSourceError


class FakeWorksheet:
    def __init__(self, headers, rows):
        self._headers = headers
        self._rows = rows

    def row_values(self, row_number):
        return self._headers

    def get_all_records(self):
        return self._rows


class FakeWorkbook:
    def __init__(self, worksheets: dict):
        self._worksheets = worksheets

    def worksheet(self, name):
        if name not in self._worksheets:
            raise gspread.exceptions.WorksheetNotFound(name)
        return self._worksheets[name]


class FakeClient:
    def __init__(self, workbook):
        self._workbook = workbook

    def open_by_key(self, key):
        return self._workbook


WATCHLIST_HEADERS = [
    "Name",
    "Type",
    "Status",
    "Category",
    "All",
    "US",
    "UK",
    "BR",
    "AUS",
    "Aliases / keywords",
    "Source URL",
    "Why tracked",
    "Priority",
    "Added by",
    "Date added",
]

CRITERIA_HEADERS = ["Section", "Content"]
TOPICS_HEADERS = ["Topic", "Notes"]
PARTNERS_HEADERS = [
    "Partner (entity)",
    "Status",
    "sentence",
    "title",
    "All",
    "US",
    "UK",
    "BR",
    "AU",
]


def make_source(worksheets: dict) -> SheetsSource:
    source = SheetsSource.__new__(SheetsSource)
    source._client = FakeClient(FakeWorkbook(worksheets))
    source._spreadsheet_id = "fake-spreadsheet-id"
    return source


def watchlist_row(**overrides):
    row = {
        "Name": "Uber",
        "Type": "Competitor",
        "Status": "Active",
        "Category": "",
        "All": "FALSE",
        "US": "TRUE",
        "UK": "FALSE",
        "BR": "TRUE",
        "AUS": "FALSE",
        "Aliases / keywords": "Uber One, Uber Rewards",
        "Source URL": "https://uber.com/press",
        "Why tracked": "Direct competitor for youth loyalty spend.",
        "Priority": "High",
        "Added by": "Jane Doe",
        "Date added": "2026-01-01",
    }
    row.update(overrides)
    return row


def partners_row(**overrides):
    row = {
        "Partner (entity)": "Fever",
        "Status": "Active",
        "sentence": "live-events redemption",
        "title": "Fever",
        "All": "FALSE",
        "US": "TRUE",
        "UK": "FALSE",
        "BR": "FALSE",
        "AU": "FALSE",
    }
    row.update(overrides)
    return row


CRITERIA_ROWS = [
    {"Section": "NOMO context", "Content": "NOMO is a rewards app for teens."},
    {"Section": "Region weighting", "Content": "BR is primary; US, UK, AU are growing."},
    {"Section": "Competitor criteria", "Content": "Competes for youth loyalty attention."},
    {"Section": "Partner criteria", "Content": "Has tradeable reward inventory."},
    {"Section": "Do-not-suggest", "Content": "Meta\nTikTok"},
]

TOPICS_ROWS = [
    {"Topic": "youth social media policy", "Notes": "Age-verification laws."},
    {"Topic": "rewards-fintech funding", "Notes": ""},
]


def full_worksheets(watchlist_rows=None, partners_rows=None, criteria_rows=None, topics_rows=None):
    return {
        "Watchlist": FakeWorksheet(WATCHLIST_HEADERS, watchlist_rows or [watchlist_row()]),
        "Criteria": FakeWorksheet(CRITERIA_HEADERS, criteria_rows or CRITERIA_ROWS),
        "Industry Topics": FakeWorksheet(TOPICS_HEADERS, topics_rows or TOPICS_ROWS),
        "Partnerships": FakeWorksheet(PARTNERS_HEADERS, partners_rows or [partners_row()]),
    }


def test_load_all_produces_source_data():
    source = make_source(full_worksheets())
    data = source.load_all()

    assert len(data.entities) == 2  # Uber (watchlist) + Fever (partners)
    uber = next(e for e in data.entities if e.name == "Uber")
    assert uber.source == "watchlist"
    assert uber.region == ["US", "BR"]
    assert uber.aliases == ["Uber One", "Uber Rewards"]

    fever = next(e for e in data.entities if e.name == "Fever")
    assert fever.source == "partners_db"
    assert fever.type == "Existing partner"

    assert "Fever: live-events redemption" in data.reward_landscape
    assert "Fever" in data.excluded_names
    assert data.criteria.nomo_context == "NOMO is a rewards app for teens."
    assert data.criteria.do_not_suggest == ["Meta", "TikTok"]
    assert [t.topic for t in data.industry_topics] == [
        "youth social media policy",
        "rewards-fintech funding",
    ]


def test_region_all_flag_wins_regardless_of_individual_flags():
    row = watchlist_row(All="TRUE", US="TRUE", BR="TRUE")
    source = make_source(full_worksheets(watchlist_rows=[row]))
    data = source.load_all()

    uber = next(e for e in data.entities if e.name == "Uber")
    assert uber.region == ["All"]


def test_excluded_type_row_is_excluded_not_tracked():
    rows = [watchlist_row(Name="Meta", Type="Excluded", Status="Active", **{"Aliases / keywords": "Facebook, Instagram"})]
    source = make_source(full_worksheets(watchlist_rows=rows))
    data = source.load_all()

    assert [e for e in data.entities if e.source == "watchlist"] == []
    assert "Meta" in data.excluded_names
    assert "Facebook" in data.excluded_names
    assert "Instagram" in data.excluded_names


def test_paused_and_converted_rows_are_excluded_not_tracked():
    rows = [
        watchlist_row(Name="PausedCo", Status="Paused"),
        watchlist_row(Name="ConvertedCo", Status="Converted"),
        watchlist_row(Name="ActiveCo", Status="Active"),
    ]
    source = make_source(full_worksheets(watchlist_rows=rows))
    data = source.load_all()

    names = {e.name for e in data.entities}
    assert names == {"ActiveCo", "Fever"}
    assert "PausedCo" in data.excluded_names
    assert "ConvertedCo" in data.excluded_names


def test_partners_paired_row_filter_skips_blank_entity_rows():
    rows = [
        partners_row(**{"Partner (entity)": "Fever", "sentence": "live-events redemption"}),
        partners_row(**{"Partner (entity)": "", "sentence": "(traducao em portugues)"}),
    ]
    source = make_source(full_worksheets(partners_rows=rows))
    data = source.load_all()

    partner_entities = [e for e in data.entities if e.source == "partners_db"]
    assert len(partner_entities) == 1
    assert partner_entities[0].name == "Fever"


def test_partners_missing_status_column_defaults_all_to_active(caplog):
    headers_without_status = [h for h in PARTNERS_HEADERS if h != "Status"]
    row = partners_row()
    del row["Status"]
    worksheets = full_worksheets(partners_rows=[row])
    worksheets["Partnerships"] = FakeWorksheet(headers_without_status, [row])

    with caplog.at_level(logging.WARNING):
        source = make_source(worksheets)
        data = source.load_all()

    partner_entities = [e for e in data.entities if e.source == "partners_db"]
    assert len(partner_entities) == 1
    assert partner_entities[0].status == "Active"
    assert "Status" in caplog.text
    assert "Active" in caplog.text


def test_partners_inactive_status_excluded_when_column_present():
    rows = [partners_row(Status="Inactive")]
    source = make_source(full_worksheets(partners_rows=rows))
    data = source.load_all()

    assert [e for e in data.entities if e.source == "partners_db"] == []
    assert "Fever" not in data.excluded_names


def test_missing_required_watchlist_column_fails_loudly():
    headers_without_name = [h for h in WATCHLIST_HEADERS if h != "Name"]
    worksheets = full_worksheets()
    worksheets["Watchlist"] = FakeWorksheet(headers_without_name, [watchlist_row()])
    source = make_source(worksheets)

    with pytest.raises(Exception, match="(?i)name"):
        source.load_all()


def test_missing_tab_fails_loudly():
    worksheets = full_worksheets()
    del worksheets["Criteria"]
    source = make_source(worksheets)

    with pytest.raises(SheetsSourceError, match="Criteria"):
        source.load_all()
