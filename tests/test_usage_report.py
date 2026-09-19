"""Cost and latency record. In-memory database; no model or network."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from scripts import usage_report
from src.ai import usage
from src.database.models import Base, HeadlineAnalysis

NOW = datetime(2026, 9, 19, 15, 0, tzinfo=timezone.utc)


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def add(session, key, when=NOW, **result):
    session.add(HeadlineAnalysis(cache_key=key.ljust(64, "0"), document_id="d" * 64,
                                 result_json=result.pop("raw", None) or json.dumps(result), created_at=when))


def fill(engine):
    with Session(engine) as session:
        add(session, "a", model="m1", prompt_version="v2", latency_ms=100,
            usage={"promptTokenCount": 1000, "candidatesTokenCount": 200, "thoughtsTokenCount": 300})
        add(session, "b", model="m1", prompt_version="v2", latency_ms=300,
            usage={"promptTokenCount": 2000, "candidatesTokenCount": 400})
        add(session, "c", model="m1", prompt_version="v1", latency_ms=1000, usage={})       # provider sent no usage
        add(session, "d", model="m2", prompt_version="v2")                                   # older row: no usage, no latency
        add(session, "e", raw="{not json")
        add(session, "f", when=NOW - timedelta(days=1), model="m1", prompt_version="v2", latency_ms=200,
            usage={"promptTokenCount": 500, "candidatesTokenCount": 100})
        session.commit()


def test_summary_counts_tokens_latency_and_today(engine):
    fill(engine)
    with Session(engine) as session:
        summary = usage.summarize(session, now=NOW)
    assert summary["analyses"] == 6 and summary["unreadable"] == 1
    assert summary["today"] == 5                                    # "f" was yesterday
    assert summary["input_tokens"] == 3500                          # 1000 + 2000 + 500
    assert summary["output_tokens"] == 1000                         # 200+300 thinking + 400 + 100
    assert summary["with_usage"] == 3
    assert {(i["model"], i["prompt_version"]): i["count"] for i in summary["by_version"]} == {
        ("m1", "v2"): 3, ("m1", "v1"): 1, ("m2", "v2"): 1}
    assert summary["latency_ms"] == {"n": 4, "mean": 400, "median": 250, "p95": 1000, "max": 1000}


def test_percentile_is_nearest_rank_of_real_measurements():
    values = list(range(100, 2100, 100))                             # 20 values, 100..2000
    assert usage._percentile(values, 0.95) == 1900                    # 19th of 20
    assert usage._percentile([5], 0.95) == 5


def test_empty_database_is_reported_as_no_measurements(engine):
    with Session(engine) as session:
        summary = usage.summarize(session, now=NOW)
    assert summary["analyses"] == 0 and summary["latency_ms"] is None and summary["input_tokens"] == 0
    text = usage.format_summary(summary, daily_limit=50)
    assert "saved analyses: 0" in text and "no measurements yet" in text and "small sample" not in text


def test_cost_estimate_uses_only_prices_you_give():
    summary = {"input_tokens": 1_000_000, "output_tokens": 500_000}
    assert usage.estimate_cost(summary, 0.30, 2.50) == pytest.approx(0.30 + 1.25)


def test_report_text_states_remaining_budget_and_its_own_limits(engine):
    fill(engine)
    with Session(engine) as session:
        summary = usage.summarize(session, now=NOW)
    text = usage.format_summary(summary, daily_limit=8, input_price=0.30, output_price=2.50)
    assert "5 of 8 allowed new model calls (3 left)" in text
    assert "3,500 in, 1,000 out (from 3 analyses that recorded usage)" in text
    assert "lower bound" in text and "small sample" in text
    assert f"estimated cost at your prices (0.3/M in, 2.5/M out): {usage.estimate_cost(summary, 0.30, 2.50):.4f}" in text


def test_over_the_limit_never_shows_a_negative_remainder(engine):
    fill(engine)
    with Session(engine) as session:
        text = usage.format_summary(usage.summarize(session, now=NOW), daily_limit=2)
    assert "(0 left)" in text


def test_cli_prints_the_report_and_needs_both_prices(engine, monkeypatch, capsys):
    fill(engine)
    monkeypatch.setattr(usage_report, "engine", engine)
    assert usage_report.main([]) == 0
    assert "saved analyses: 6" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        usage_report.main(["--input-price", "0.3"])


def test_cli_on_a_database_without_the_table_explains_instead_of_crashing(monkeypatch, capsys):
    bare = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    monkeypatch.setattr(usage_report, "engine", bare)
    assert usage_report.main([]) == 0
    assert "No analyses saved yet" in capsys.readouterr().out
