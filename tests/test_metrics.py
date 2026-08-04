from types import SimpleNamespace

import metrics


def usage(
    input_tokens=0,
    output_tokens=0,
    cache_creation_input_tokens=0,
    cache_read_input_tokens=0,
    web_search_requests=0,
):
    server_tool_use = SimpleNamespace(web_search_requests=web_search_requests)
    return SimpleNamespace(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_creation_input_tokens=cache_creation_input_tokens,
        cache_read_input_tokens=cache_read_input_tokens,
        server_tool_use=server_tool_use,
    )


def setup_function(_):
    metrics.drain()  # clear any leftover state from other tests


def test_estimate_cost_usd_known_model():
    # 1,000,000 input tokens + 1,000,000 output tokens on Sonnet 5 ($2/$10 per MTok)
    cost = metrics.estimate_cost_usd("claude-sonnet-5", usage(input_tokens=1_000_000, output_tokens=1_000_000))
    assert cost == 12.0


def test_estimate_cost_usd_includes_web_search():
    cost = metrics.estimate_cost_usd("claude-sonnet-5", usage(web_search_requests=1000))
    assert cost == 10.0  # $10 per 1,000 searches, no token cost


def test_estimate_cost_usd_cache_read_discounted():
    # cache reads bill at 0.1x the input rate
    full_price = metrics.estimate_cost_usd("claude-sonnet-5", usage(input_tokens=1_000_000))
    cache_price = metrics.estimate_cost_usd("claude-sonnet-5", usage(cache_read_input_tokens=1_000_000))
    assert cache_price == round(full_price * 0.1, 6)


def test_estimate_cost_usd_unknown_model_returns_none():
    assert metrics.estimate_cost_usd("some-future-model", usage(input_tokens=100)) is None


def test_record_and_drain_round_trips():
    response = SimpleNamespace(stop_reason="end_turn", usage=usage(input_tokens=500, output_tokens=100))

    metrics.record("monitor", "Uber", "claude-sonnet-5", 1234.5, response)
    calls = metrics.drain()

    assert len(calls) == 1
    entry = calls[0]
    assert entry["stage"] == "monitor"
    assert entry["label"] == "Uber"
    assert entry["model"] == "claude-sonnet-5"
    assert entry["latency_ms"] == 1234.5
    assert entry["stop_reason"] == "end_turn"
    assert entry["input_tokens"] == 500
    assert entry["output_tokens"] == 100
    assert entry["estimated_cost_usd"] is not None
    assert "timestamp" in entry


def test_drain_clears_recorded_calls():
    response = SimpleNamespace(stop_reason="end_turn", usage=usage())
    metrics.record("scout", "angle", "claude-sonnet-5", 100.0, response)

    first_drain = metrics.drain()
    second_drain = metrics.drain()

    assert len(first_drain) == 1
    assert second_drain == []


def test_record_handles_missing_usage_gracefully():
    response = SimpleNamespace(stop_reason="end_turn")  # no .usage at all

    metrics.record("industry", "topic", "claude-sonnet-5", 50.0, response)
    calls = metrics.drain()

    assert calls[0]["input_tokens"] == 0
    assert calls[0]["estimated_cost_usd"] is None


def test_peek_does_not_clear():
    response = SimpleNamespace(stop_reason="end_turn", usage=usage())
    metrics.record("scout", "angle", "claude-sonnet-5", 100.0, response)

    first_peek = metrics.peek()
    second_peek = metrics.peek()

    assert len(first_peek) == 1
    assert len(second_peek) == 1  # still there — peek doesn't drain
    assert len(metrics.drain()) == 1  # cleanup


def test_summarize_totals_across_calls():
    calls = [
        {
            "stage": "monitor",
            "input_tokens": 100,
            "output_tokens": 20,
            "web_search_requests": 1,
            "latency_ms": 500.0,
            "estimated_cost_usd": 0.001,
        },
        {
            "stage": "scout",
            "input_tokens": 300,
            "output_tokens": 80,
            "web_search_requests": 3,
            "latency_ms": 1500.0,
            "estimated_cost_usd": 0.004,
        },
    ]

    summary = metrics.summarize(calls)

    assert summary["call_count"] == 2
    assert summary["input_tokens"] == 400
    assert summary["output_tokens"] == 100
    assert summary["web_search_requests"] == 4
    assert summary["total_latency_ms"] == 2000.0
    assert summary["estimated_cost_usd"] == 0.005


def test_summarize_empty_list():
    summary = metrics.summarize([])
    assert summary["call_count"] == 0
    assert summary["estimated_cost_usd"] == 0  # sum([]) == 0, no None in an empty list


def test_summarize_cost_none_when_any_call_unpriced():
    calls = [
        {
            "stage": "monitor",
            "input_tokens": 100,
            "output_tokens": 20,
            "web_search_requests": 0,
            "latency_ms": 100.0,
            "estimated_cost_usd": 0.001,
        },
        {
            "stage": "scout",
            "input_tokens": 100,
            "output_tokens": 20,
            "web_search_requests": 0,
            "latency_ms": 100.0,
            "estimated_cost_usd": None,  # unknown model
        },
    ]

    summary = metrics.summarize(calls)

    assert summary["estimated_cost_usd"] is None


def test_summarize_by_stage_groups_correctly():
    calls = [
        {
            "stage": "monitor",
            "input_tokens": 100,
            "output_tokens": 20,
            "web_search_requests": 1,
            "latency_ms": 500.0,
            "estimated_cost_usd": 0.001,
        },
        {
            "stage": "monitor",
            "input_tokens": 50,
            "output_tokens": 10,
            "web_search_requests": 1,
            "latency_ms": 300.0,
            "estimated_cost_usd": 0.0005,
        },
        {
            "stage": "scout",
            "input_tokens": 300,
            "output_tokens": 80,
            "web_search_requests": 3,
            "latency_ms": 1500.0,
            "estimated_cost_usd": 0.004,
        },
    ]

    by_stage = metrics.summarize_by_stage(calls)

    assert set(by_stage) == {"monitor", "scout"}
    assert by_stage["monitor"]["call_count"] == 2
    assert by_stage["monitor"]["input_tokens"] == 150
    assert by_stage["scout"]["call_count"] == 1
