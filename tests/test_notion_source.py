"""Verifies notion_source.py against mocked fixtures matching the Notion API's
response shape (§16 Phase 2) — live workspace access isn't available yet.
"""

from sources.notion_source import NotionSource
from sources.sheets_source import SheetsSource


class FakeDatabases:
    def __init__(self, data: dict):
        self._data = data

    def query(self, database_id, start_cursor=None):
        return {"results": self._data.get(database_id, []), "has_more": False, "next_cursor": None}


class FakeBlockChildren:
    def __init__(self, blocks_by_page: dict):
        self._blocks_by_page = blocks_by_page

    def list(self, block_id, start_cursor=None):
        return {
            "results": self._blocks_by_page.get(block_id, []),
            "has_more": False,
            "next_cursor": None,
        }


class FakeBlocks:
    def __init__(self, blocks_by_page):
        self.children = FakeBlockChildren(blocks_by_page)


class FakeNotionClient:
    def __init__(self, databases_data, blocks_data):
        self.databases = FakeDatabases(databases_data)
        self.blocks = FakeBlocks(blocks_data)


def rich_text(text: str) -> list[dict]:
    return [{"plain_text": text}]


def heading(level: str, text: str) -> dict:
    return {"type": f"heading_{level}", f"heading_{level}": {"rich_text": rich_text(text)}}


def paragraph(text: str) -> dict:
    return {"type": "paragraph", "paragraph": {"rich_text": rich_text(text)}}


def bullet(text: str) -> dict:
    return {"type": "bulleted_list_item", "bulleted_list_item": {"rich_text": rich_text(text)}}


def watchlist_page(**overrides) -> dict:
    properties = {
        "Name": {"title": rich_text("Uber")},
        "Type": {"select": {"name": "Competitor"}},
        "Status": {"select": {"name": "Active"}},
        "Category": {"multi_select": []},
        "Region": {"multi_select": [{"name": "US"}, {"name": "BR"}]},
        "Aliases / keywords": {"rich_text": rich_text("Uber One, Uber Rewards")},
        "Source URL": {"url": "https://uber.com/press"},
        "Why tracked": {"rich_text": rich_text("Direct competitor.")},
        "Priority": {"select": {"name": "High"}},
        "Added by": {"rich_text": rich_text("Jane Doe")},
        "Date added": {"created_time": "2026-01-01T00:00:00.000Z"},
    }
    properties.update(overrides)
    return {"properties": properties}


def partners_page(**overrides) -> dict:
    properties = {
        "Entity": {"title": rich_text("Fever")},
        "Status": {"select": {"name": "Active"}},
        "Region": {"multi_select": [{"name": "US"}]},
        "en | sentence": {"rich_text": rich_text("live-events redemption")},
        "en | title": {"rich_text": rich_text("Fever")},
    }
    properties.update(overrides)
    return {"properties": properties}


def gtm_partners_page(**overrides) -> dict:
    properties = {
        "Entity": {"title": rich_text("Lincoln High")},
        "Status": {"select": {"name": "Active"}},
        "Region": {"multi_select": [{"name": "US"}]},
        "Notes": {"rich_text": rich_text("500-student pilot")},
    }
    properties.update(overrides)
    return {"properties": properties}


def topic_page(topic: str, notes: str = "") -> dict:
    return {
        "properties": {
            "Topic": {"title": rich_text(topic)},
            "Notes": {"rich_text": rich_text(notes)},
        }
    }


CRITERIA_BLOCKS = [
    heading("1", "NOMO context"),
    paragraph("NOMO is a rewards app for teens."),
    heading("1", "Region weighting"),
    paragraph("BR is primary; US, UK, AU are growing."),
    heading("1", "Competitor criteria"),
    paragraph("Competes for youth loyalty attention."),
    heading("1", "Rewards partners criteria"),
    paragraph("Has tradeable reward inventory."),
    heading("1", "GTM partners criteria"),
    paragraph("School districts\nTelecom carriers"),
    heading("2", "Do-not-suggest"),
    bullet("Meta"),
    bullet("TikTok"),
]


def make_source(
    watchlist_pages=None,
    partners_pages=None,
    topics_pages=None,
    criteria_blocks=None,
    gtm_partners_pages=None,
    gtm_partners_db_id=None,
) -> NotionSource:
    source = NotionSource.__new__(NotionSource)
    source._watchlist_db_id = "watchlist-db"
    source._criteria_page_id = "criteria-page"
    source._topics_db_id = "topics-db"
    source._partners_db_id = "partners-db"
    source._gtm_partners_db_id = gtm_partners_db_id
    source._client = FakeNotionClient(
        databases_data={
            "watchlist-db": watchlist_pages if watchlist_pages is not None else [watchlist_page()],
            "partners-db": partners_pages if partners_pages is not None else [partners_page()],
            "topics-db": topics_pages
            if topics_pages is not None
            else [topic_page("youth social media policy", "Age-verification laws.")],
            "gtm-partners-db": gtm_partners_pages
            if gtm_partners_pages is not None
            else [gtm_partners_page()],
        },
        blocks_data={"criteria-page": criteria_blocks or CRITERIA_BLOCKS},
    )
    return source


def test_load_all_produces_source_data():
    source = make_source()
    data = source.load_all()

    assert len(data.entities) == 2
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
    assert [t.topic for t in data.industry_topics] == ["youth social media policy"]


def test_excluded_and_paused_rows_excluded_not_tracked():
    pages = [
        watchlist_page(
            Name={"title": rich_text("Meta")},
            Type={"select": {"name": "Excluded"}},
            **{"Aliases / keywords": {"rich_text": rich_text("Facebook, Instagram")}},
        ),
        watchlist_page(
            Name={"title": rich_text("PausedCo")}, Status={"select": {"name": "Paused"}}
        ),
        watchlist_page(Name={"title": rich_text("ActiveCo")}),
    ]
    source = make_source(watchlist_pages=pages)
    data = source.load_all()

    names = {e.name for e in data.entities if e.source == "watchlist"}
    assert names == {"ActiveCo"}
    assert "Meta" in data.excluded_names
    assert "Facebook" in data.excluded_names
    assert "PausedCo" in data.excluded_names


def test_inactive_partner_excluded_from_entities_and_exclusion_list():
    pages = [partners_page(Status={"select": {"name": "Inactive"}})]
    source = make_source(partners_pages=pages)
    data = source.load_all()

    assert [e for e in data.entities if e.source == "partners_db"] == []
    assert "Fever" not in data.excluded_names


def test_notion_source_data_matches_shape_of_equivalent_sheets_fixture():
    """§16 Phase 2: notion_source.py must produce a SourceData identical in
    shape to what sheets_source.py produces from equivalent fixture data."""
    from tests.test_sheets_source import full_worksheets

    notion_source = make_source()
    notion_data = notion_source.load_all()

    sheets_source = SheetsSource.__new__(SheetsSource)
    from tests.test_sheets_source import FakeClient, FakeWorkbook

    sheets_source._client = FakeClient(FakeWorkbook(full_worksheets()))
    sheets_source._spreadsheet_id = "fake-id"
    sheets_data = sheets_source.load_all()

    assert {e.name for e in notion_data.entities} == {e.name for e in sheets_data.entities}
    assert {e.source for e in notion_data.entities} == {e.source for e in sheets_data.entities}
    assert notion_data.excluded_names == sheets_data.excluded_names
    assert notion_data.reward_landscape == sheets_data.reward_landscape
    assert notion_data.gtm_landscape == sheets_data.gtm_landscape
    assert notion_data.criteria == sheets_data.criteria
    assert [t.topic for t in notion_data.industry_topics] == ["youth social media policy"]


def test_gtm_partners_db_id_unset_falls_back_to_empty_with_warning(caplog):
    source = make_source(gtm_partners_db_id=None)

    with caplog.at_level("WARNING"):
        data = source.load_all()

    assert data.gtm_landscape == []
    assert [e for e in data.entities if e.source == "gtm_partners_db"] == []
    assert "GTM Partners" in caplog.text


def test_gtm_partners_db_id_set_loads_entities_and_landscape():
    source = make_source(gtm_partners_db_id="gtm-partners-db")
    data = source.load_all()

    gtm_entities = [e for e in data.entities if e.source == "gtm_partners_db"]
    assert len(gtm_entities) == 1
    assert gtm_entities[0].name == "Lincoln High"
    assert gtm_entities[0].type == "Existing GTM partner"
    assert "Lincoln High: 500-student pilot" in data.gtm_landscape
    assert "Lincoln High" in data.excluded_names


def test_gtm_partner_prospect_type_is_tracked():
    pages = [
        watchlist_page(
            Name={"title": rich_text("Lincoln High")},
            Type={"select": {"name": "GTM partner prospect"}},
        )
    ]
    source = make_source(watchlist_pages=pages)
    data = source.load_all()

    tracked = [e for e in data.entities if e.source == "watchlist"]
    assert [e.name for e in tracked] == ["Lincoln High"]
