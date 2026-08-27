"""Full production-pipeline dry run against a preview Slack channel.

Unlike diagnose_synthesis.py (which reimplements Stages 1-5 individually),
this calls main.py's real run() — same gap-recovery window, same candidate
filtering/dedup, same reconsider-list logic, same everything the real cron
does — so what lands in the preview channel is exactly what the real channel
would show. Two things are redirected so this is safe to run as often as you
like:

  - Slack: config.config.slack_webhook_url is swapped for
    SLACK_PREVIEW_WEBHOOK_URL before main.run() is called. Required.
  - State: state/ reads and writes are redirected to a scratch copy under
    .preview_run/state/, reseeded fresh from the real state/ directory on
    every run. 

Usage: SLACK_PREVIEW_WEBHOOK_URL=<test-channel-webhook> python preview_run.py
"""

from __future__ import annotations

import dataclasses
import logging
import os
import shutil
import sys
from pathlib import Path

import config
import main as main_module

PREVIEW_DIR = Path(".preview_run")
REAL_STATE_DIR = Path("state")


def _seed_preview_state() -> None:
    """Fresh copy of the real state/ dir on every run, so gap-recovery and
    suppression reflect true production state without ever being able to
    drift from — or write back to — it."""
    if PREVIEW_DIR.exists():
        shutil.rmtree(PREVIEW_DIR)
    preview_state_dir = PREVIEW_DIR / "state"
    if REAL_STATE_DIR.exists():
        shutil.copytree(REAL_STATE_DIR, preview_state_dir)
    else:
        preview_state_dir.mkdir(parents=True)


def main() -> int:
    preview_webhook_url = os.environ.get("SLACK_PREVIEW_WEBHOOK_URL")
    if not preview_webhook_url:
        print(
            "SLACK_PREVIEW_WEBHOOK_URL must be set to a test channel's webhook — "
            "refusing to run without it (this script is only useful if it "
            "actually posts somewhere you can look at).",
            file=sys.stderr,
        )
        return 1

    config.config = dataclasses.replace(config.config, slack_webhook_url=preview_webhook_url)

    _seed_preview_state()
    real_cwd = Path.cwd()
    os.chdir(PREVIEW_DIR)
    try:
        print(
            f"Preview run — posting to SLACK_PREVIEW_WEBHOOK_URL, reading/writing "
            f"scratch state under {PREVIEW_DIR}/state (real state/ untouched).\n"
        )
        exit_code = main_module.run()
    finally:
        os.chdir(real_cwd)

    if exit_code == 0:
        print(
            f"\nPreview run succeeded — check the test channel. Scratch state "
            f"(digest archive, updated seen_stories, etc.) is under "
            f"{PREVIEW_DIR}/state for inspection; real state/ was not touched."
        )
    else:
        print(f"\nPreview run exited {exit_code} — see log output above.")
    return exit_code


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(main())
