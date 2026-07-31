"""Seeds synthetic state/digests/*.json archives for the most recently
completed Mon-Fri, so weekly_main.py can be exercised without waiting for
real days to accumulate. Content is hand-authored, not real pipeline output —
deliberately built with a story that runs across three days (Mon/Tue/Fri), a
shorter two-day regulatory thread (Mon/Wed), and a candidate that resurfaces
(Mon/Fri), so the weekly rollup's cross-day dedup and theme-extraction have
something real to chew on. Use --days to seed fewer than 5 weekdays and
exercise the partial-week ("N of 5 weekdays") path.

Usage: python seed_synthetic_week.py [--days 5] [--dir state/digests]
Then: python weekly_main.py (optionally with SLACK_WEBHOOK_URL pointed at a
test channel first — see diagnose_synthesis.py's SLACK_PREVIEW_WEBHOOK_URL
pattern for the same idea applied to the daily digest)."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

from models import Candidate, Digest, DigestItem
from state import DEFAULT_DIGEST_ARCHIVE_DIR
from weekly_main import _prior_week_weekdays


def _item(headline: str, url: str, source: str, summary: str) -> DigestItem:
    return DigestItem(headline=headline, url=url, source=source, summary=summary)


def _candidate(name: str, why_fits: str, url: str) -> Candidate:
    return Candidate(
        name=name,
        suggested_type="Partner prospect",
        region="US",
        why_fits=why_fits,
        source_url=url,
    )


def _day_digests() -> list[Digest]:
    """One entry per weekday, Monday first."""
    return [
        # Monday — opens the Uber One thread and the age-verification thread,
        # plus a fresh candidate.
        Digest(
            quiet_day=False,
            competition=[
                _item(
                    "Uber One pilots teen loyalty rewards in Austin",
                    "https://example.com/uber-1",
                    "TechCrunch",
                    "Uber is testing a teen-focused loyalty tier with screen-time-adjacent rewards.",
                )
            ],
            industry=[
                _item(
                    "Texas advances age-verification bill for social apps",
                    "https://example.com/policy-1",
                    "Reuters",
                    "State lawmakers advance a bill requiring age verification for social platforms.",
                )
            ],
            new_candidates=[
                _candidate(
                    "Vivaly",
                    "Direct reward-marketplace overlap with NOMO's core mechanic.",
                    "https://example.com/vivaly",
                )
            ],
            tracking_counts={"competitors": 2, "partner_prospects": 1},
        ),
        # Tuesday — the Uber One story continues; a one-off partner update.
        Digest(
            quiet_day=False,
            competition=[
                _item(
                    "Uber One's teen rewards pilot expands to 3 more cities",
                    "https://example.com/uber-2",
                    "TechCrunch",
                    "The pilot NOMO first flagged Monday has expanded beyond Austin.",
                )
            ],
            partner_prospects=[
                _item(
                    "Fever adds two new city partnerships",
                    "https://example.com/fever-1",
                    "PR Newswire",
                    "Existing partner Fever expands its live-events footprint.",
                )
            ],
            tracking_counts={"competitors": 2, "partner_prospects": 1},
        ),
        # Wednesday — the regulatory thread continues; a second new candidate.
        Digest(
            quiet_day=False,
            industry=[
                _item(
                    "Texas age-verification bill passes committee vote",
                    "https://example.com/policy-2",
                    "Reuters",
                    "Follow-up on Monday's bill — advances to a full floor vote.",
                )
            ],
            new_candidates=[
                _candidate(
                    "Timily",
                    "Screen-time gamification with a tradeable rewards layer.",
                    "https://example.com/timily",
                )
            ],
            tracking_counts={"competitors": 2, "partner_prospects": 1},
        ),
        # Thursday — an unrelated one-off, to check it doesn't get folded
        # into either running thread.
        Digest(
            quiet_day=False,
            competition=[
                _item(
                    "Long Game announces new financial-literacy reward tier",
                    "https://example.com/longgame-1",
                    "Business Wire",
                    "A new reward tier tied to financial-literacy modules.",
                )
            ],
            tracking_counts={"competitors": 2, "partner_prospects": 1},
        ),
        # Friday — closes out the Uber One thread with a regulatory-scrutiny
        # angle, and Vivaly resurfaces (tests notable_candidates dedup).
        Digest(
            quiet_day=False,
            competition=[
                _item(
                    "Uber One's teen loyalty pilot draws regulatory scrutiny",
                    "https://example.com/uber-3",
                    "The Information",
                    "The week-long pilot now faces questions from regulators.",
                )
            ],
            new_candidates=[
                _candidate(
                    "Vivaly",
                    "Direct reward-marketplace overlap with NOMO's core mechanic.",
                    "https://example.com/vivaly",
                )
            ],
            tracking_counts={"competitors": 2, "partner_prospects": 1},
        ),
    ]


def seed(dir: Path, num_days: int) -> list[date]:
    weekdays = _prior_week_weekdays(date.today())
    digests = _day_digests()
    dir.mkdir(parents=True, exist_ok=True)

    written = []
    for day, digest in list(zip(weekdays, digests))[:num_days]:
        path = dir / f"{day.isoformat()}.json"
        with path.open("w") as f:
            json.dump(asdict(digest), f, indent=2, sort_keys=True)
        written.append(day)
    return written


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days", type=int, default=5, choices=range(1, 6), help="how many weekdays to seed (1-5)"
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=DEFAULT_DIGEST_ARCHIVE_DIR,
        help="archive directory to write into (default: state/digests)",
    )
    args = parser.parse_args()

    written = seed(args.dir, args.days)

    print(f"Seeded {len(written)} synthetic daily digest(s) in {args.dir}:")
    for day in written:
        print(f"  - {day.isoformat()}.json")
    print("\nNow run: python weekly_main.py")


if __name__ == "__main__":
    main()
