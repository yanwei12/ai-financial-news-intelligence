"""
Cost and latency record for saved headline analyses (plan §12: measure 20
articles, then estimate). Reads only what analysis already stored; no model or
network calls.

Token counts come from the provider's usage metadata. Prices change, so this
module never assumes any: pass your own per-million-token prices to get an
estimate, and treat it as an estimate.
"""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.database.models import HeadlineAnalysis

# Provider usage keys. Gemini bills "thinking" tokens as output, so they count with the candidates.
_INPUT_KEYS = ("promptTokenCount",)
_OUTPUT_KEYS = ("candidatesTokenCount", "thoughtsTokenCount")


def _tokens(usage: object, keys: tuple[str, ...]) -> int:
    if not isinstance(usage, dict):
        return 0
    return sum(v for k in keys if isinstance((v := usage.get(k)), int) and not isinstance(v, bool))


def _percentile(sorted_values: list[int], fraction: float) -> int:
    """Nearest-rank percentile; honest for small samples (no interpolation between real measurements)."""
    return sorted_values[max(1, math.ceil(len(sorted_values) * fraction)) - 1]


def summarize(session: Session, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    rows = session.execute(select(HeadlineAnalysis)).scalars().all()

    by_version: Counter = Counter()
    latencies: list[int] = []
    input_tokens = output_tokens = unreadable = today = with_usage = 0
    for row in rows:
        today += row.created_at >= start_of_day
        try:
            result = json.loads(row.result_json)
        except ValueError:
            unreadable += 1
            continue
        by_version[(result.get("model"), result.get("prompt_version"))] += 1
        usage = result.get("usage")
        if _tokens(usage, _INPUT_KEYS + _OUTPUT_KEYS):
            with_usage += 1
        input_tokens += _tokens(usage, _INPUT_KEYS)
        output_tokens += _tokens(usage, _OUTPUT_KEYS)
        if isinstance(result.get("latency_ms"), int):
            latencies.append(result["latency_ms"])

    ordered = sorted(latencies)
    return {
        "analyses": len(rows), "unreadable": unreadable, "today": today,
        "with_usage": with_usage, "input_tokens": input_tokens, "output_tokens": output_tokens,
        "by_version": [{"model": m, "prompt_version": v, "count": n} for (m, v), n in sorted(by_version.items(), key=str)],
        "latency_ms": None if not ordered else {
            "n": len(ordered), "mean": round(statistics.fmean(ordered)), "median": round(statistics.median(ordered)),
            "p95": _percentile(ordered, 0.95), "max": ordered[-1]},
    }


def estimate_cost(summary: dict, input_price_per_million: float, output_price_per_million: float) -> float:
    return (summary["input_tokens"] * input_price_per_million
            + summary["output_tokens"] * output_price_per_million) / 1_000_000


def format_summary(summary: dict, daily_limit: int, input_price: float | None = None,
                   output_price: float | None = None) -> str:
    lines = [f"saved analyses: {summary['analyses']}"
             + (f"  ({summary['unreadable']} unreadable)" if summary["unreadable"] else ""),
             f"today (UTC): {summary['today']} of {daily_limit} allowed new model calls "
             f"({max(0, daily_limit - summary['today'])} left)"]
    for item in summary["by_version"]:
        lines.append(f"  {item['count']:>4} x model={item['model']}  prompt={item['prompt_version']}")
    lines.append(f"tokens: {summary['input_tokens']:,} in, {summary['output_tokens']:,} out "
                 f"(from {summary['with_usage']} analyses that recorded usage)")
    latency = summary["latency_ms"]
    lines.append("latency: no measurements yet (older analyses did not record it)" if latency is None else
                 f"latency: n={latency['n']}  mean {latency['mean']} ms  median {latency['median']} ms  "
                 f"p95 {latency['p95']} ms  max {latency['max']} ms")
    if input_price is not None and output_price is not None:
        cost = estimate_cost(summary, input_price, output_price)
        lines.append(f"estimated cost at your prices ({input_price}/M in, {output_price}/M out): {cost:.4f}")
        if summary["with_usage"] < summary["analyses"]:
            lines.append("  note: some analyses recorded no usage, so this is a lower bound")
    if summary["analyses"] and summary["analyses"] < 20:
        lines.append(f"note: {summary['analyses']} analyses is a small sample; the plan suggests measuring about 20 "
                     "before projecting a daily cost.")
    return "\n".join(lines)
