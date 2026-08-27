"""Cross-cutting call telemetry: latency, token usage, and estimated cost for
every Anthropic API call the pipeline makes. Global collector (like the
stdlib `logging` module) so call sites don't need to thread a collector
object through every function signature — main.py drains it once at the end
of a run and persists to state/metrics/YYYY-MM-DD.jsonl."""

from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# $ per million tokens. Sonnet 5 figures are intro pricing, in effect through
# 2026-08-31 — revisit after that date (standard rate is $3/$15).
PRICING_PER_MTOK = {
    "claude-sonnet-5": {"input": 2.00, "output": 10.00},
    "claude-opus-5": {"input": 5.00, "output": 25.00},
    "claude-haiku-4-5": {"input": 1.00, "output": 5.00},
}
WEB_SEARCH_COST_PER_1000 = 10.00

_calls: list[dict] = []
_lock = threading.Lock()


def estimate_cost_usd(model: str, usage) -> float | None:
    """None (rather than 0) when the model isn't in PRICING_PER_MTOK, so a
    missing price shows up as a gap to fill rather than a silent zero."""
    rates = PRICING_PER_MTOK.get(model)
    if rates is None:
        logger.warning("metrics: no pricing entry for model %r — cost left unestimated", model)
        return None

    input_tokens = getattr(usage, "input_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
    server_tool_use = getattr(usage, "server_tool_use", None)
    web_searches = getattr(server_tool_use, "web_search_requests", 0) or 0

    token_cost = (
        (input_tokens + cache_creation) * rates["input"]
        + cache_read * rates["input"] * 0.1
        + output_tokens * rates["output"]
    ) / 1_000_000
    search_cost = web_searches * WEB_SEARCH_COST_PER_1000 / 1000
    return round(token_cost + search_cost, 6)


def record(stage: str, label: str, model: str, latency_ms: float, response) -> None:
    """Called right after every Anthropic API call completes. stage/label
    identify what the call was for (e.g. "monitor"/"Uber", "synthesize"/
    "digest") so a JSONL line is self-describing without cross-referencing
    logs."""
    usage = getattr(response, "usage", None)
    server_tool_use = getattr(usage, "server_tool_use", None) if usage else None
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "stage": stage,
        "label": label,
        "model": model,
        "latency_ms": round(latency_ms, 1),
        "stop_reason": getattr(response, "stop_reason", None),
        "input_tokens": getattr(usage, "input_tokens", 0) or 0 if usage else 0,
        "output_tokens": getattr(usage, "output_tokens", 0) or 0 if usage else 0,
        "cache_creation_input_tokens": (
            getattr(usage, "cache_creation_input_tokens", 0) or 0 if usage else 0
        ),
        "cache_read_input_tokens": (
            getattr(usage, "cache_read_input_tokens", 0) or 0 if usage else 0
        ),
        "web_search_requests": getattr(server_tool_use, "web_search_requests", 0) or 0,
        "estimated_cost_usd": estimate_cost_usd(model, usage) if usage else None,
    }
    with _lock:
        _calls.append(entry)


def drain() -> list[dict]:
    """Atomically returns and clears everything recorded so far — call once
    at the end of a run, in a finally block, so a mid-run failure still
    flushes whatever calls completed before it."""
    with _lock:
        calls, _calls[:] = list(_calls), []
    return calls


def peek() -> list[dict]:
    """Non-destructive snapshot of everything recorded so far — used to build
    the in-digest metrics footnote mid-run, without disturbing drain()'s
    end-of-run persistence."""
    with _lock:
        return list(_calls)


def summarize(calls: list[dict]) -> dict:
    """Aggregate totals across a set of recorded calls. estimated_cost_usd is
    None (not partial-summed) if any call's cost couldn't be estimated —
    a partial total would understate spend without saying so."""
    costs = [c["estimated_cost_usd"] for c in calls]
    total_cost = None if any(c is None for c in costs) else round(sum(costs), 4)
    return {
        "call_count": len(calls),
        "input_tokens": sum(c["input_tokens"] for c in calls),
        "output_tokens": sum(c["output_tokens"] for c in calls),
        "web_search_requests": sum(c["web_search_requests"] for c in calls),
        "total_latency_ms": round(sum(c["latency_ms"] for c in calls), 1),
        "estimated_cost_usd": total_cost,
    }


def summarize_by_stage(calls: list[dict]) -> dict[str, dict]:
    """Same aggregation as summarize(), grouped by stage — for the weekly
    rollup, where "which stage is actually expensive" is the useful view."""
    by_stage: dict[str, list[dict]] = {}
    for call in calls:
        by_stage.setdefault(call["stage"], []).append(call)
    return {stage: summarize(stage_calls) for stage, stage_calls in by_stage.items()}
