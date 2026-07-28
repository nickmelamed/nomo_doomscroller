#!/usr/bin/env python3
"""One-time helper: provisions the NOMO Doomscroller Watchlist DB (SPEC.md §6.1).

Standalone by design — doesn't import config.py, since that requires env vars
(like NOTION_WATCHLIST_DB_ID) that don't exist until this script has run.
Only needs NOTION_API_KEY and a parent page ID to create the database under.

Usage:
    python scripts/create_notion_db.py --parent-page-id <notion-page-id>
"""

from __future__ import annotations

import argparse
import os
import sys

from dotenv import load_dotenv
from notion_client import Client

CATEGORY_OPTIONS = ["sports", "concerts", "app credits", "travel", "dining", "retail"]
REGION_OPTIONS = ["US", "UK", "BR", "Other"]


def build_properties() -> dict:
    def select(options: list[str]) -> dict:
        return {"select": {"options": [{"name": o} for o in options]}}

    def multi_select(options: list[str]) -> dict:
        return {"multi_select": {"options": [{"name": o} for o in options]}}

    return {
        "Name": {"title": {}},
        "Type": select(["Competitor", "Partner prospect", "Excluded"]),
        "Status": select(["Active", "Paused", "Converted"]),
        "Category": multi_select(CATEGORY_OPTIONS),
        "Region": multi_select(REGION_OPTIONS),
        "Aliases / keywords": {"rich_text": {}},
        "Source URL": {"url": {}},
        "Why tracked": {"rich_text": {}},
        "Priority": select(["High", "Medium", "Low"]),
        "Added by": {"rich_text": {}},
        "Date added": {"created_time": {}},
    }


def main() -> int:
    load_dotenv()

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--parent-page-id",
        required=True,
        help="Notion page ID to create the Watchlist database under.",
    )
    parser.add_argument(
        "--title",
        default="NOMO Doomscroller Watchlist",
        help="Title for the new database.",
    )
    args = parser.parse_args()

    api_key = os.environ.get("NOTION_API_KEY")
    if not api_key:
        print("ERROR: NOTION_API_KEY is not set.", file=sys.stderr)
        return 1

    client = Client(auth=api_key)

    db = client.databases.create(
        parent={"type": "page_id", "page_id": args.parent_page_id},
        title=[{"type": "text", "text": {"content": args.title}}],
        properties=build_properties(),
    )

    print(f"Created database: {db['id']}")
    print(f"URL: {db['url']}")
    print("Set NOTION_WATCHLIST_DB_ID to the ID above, and share this database")
    print("with your Notion integration if it isn't shared automatically.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
