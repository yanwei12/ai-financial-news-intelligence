"""
Classification metrics without third-party dependencies.

Everything here works on parallel lists of labels. Undefined values (for
example precision of a class that was never predicted) are None, never 0, so a
report cannot hide "we have no idea" behind a number.
"""

from __future__ import annotations

from collections import Counter
from typing import Sequence


def confusion(gold: Sequence[str], pred: Sequence[str]) -> Counter:
    _check_lengths(gold, pred)
    return Counter(zip(gold, pred))


def accuracy(gold: Sequence[str], pred: Sequence[str]) -> float | None:
    _check_lengths(gold, pred)
    return sum(g == p for g, p in zip(gold, pred)) / len(gold) if gold else None


def per_class(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> dict[str, dict]:
    _check_lengths(gold, pred)
    counts = confusion(gold, pred)
    result = {}
    for label in labels:
        true_positive = counts[(label, label)]
        support = sum(n for (g, _), n in counts.items() if g == label)
        predicted = sum(n for (_, p), n in counts.items() if p == label)
        precision = true_positive / predicted if predicted else None
        recall = true_positive / support if support else None
        if support == 0 and predicted == 0:
            f1 = None                      # class absent from both: nothing to measure
        elif true_positive == 0:
            f1 = 0.0
        else:
            f1 = 2 * precision * recall / (precision + recall)
        result[label] = {"precision": precision, "recall": recall, "f1": f1,
                         "support": support, "predicted": predicted}
    return result


def macro_f1(gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> float | None:
    """Mean F1 over classes that appear in the gold labels or the predictions."""
    scores = [c["f1"] for c in per_class(gold, pred, labels).values() if c["f1"] is not None]
    return sum(scores) / len(scores) if scores else None


def cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float | None:
    """Agreement between two annotators beyond chance; None when it is undefined."""
    _check_lengths(a, b)
    if not a:
        return None
    n = len(a)
    observed = sum(x == y for x, y in zip(a, b)) / n
    count_a, count_b = Counter(a), Counter(b)
    expected = sum(count_a[label] * count_b[label] for label in set(count_a) | set(count_b)) / (n * n)
    if expected == 1:
        return None                        # both used a single identical label: kappa is undefined
    return (observed - expected) / (1 - expected)


def _check_lengths(a: Sequence, b: Sequence) -> None:
    if len(a) != len(b):
        raise ValueError(f"length mismatch: {len(a)} vs {len(b)}")


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def format_report(title: str, gold: Sequence[str], pred: Sequence[str], labels: Sequence[str]) -> str:
    counts = confusion(gold, pred)
    width = max(len(label) for label in labels) + 2
    lines = [f"{title}  (n={len(gold)})", "  confusion matrix (rows = gold, columns = predicted)"]
    lines.append("  " + " " * width + "".join(f"{label:>{width}}" for label in labels))
    for g in labels:
        lines.append("  " + f"{g:<{width}}" + "".join(f"{counts[(g, p)]:>{width}}" for p in labels))
    lines.append("")
    lines.append(f"  {'class':<{width}}{'precision':>10}{'recall':>8}{'f1':>6}{'support':>9}")
    for label, c in per_class(gold, pred, labels).items():
        lines.append(f"  {label:<{width}}{_fmt(c['precision']):>10}{_fmt(c['recall']):>8}"
                     f"{_fmt(c['f1']):>6}{c['support']:>9}")
    lines.append(f"\n  accuracy = {_fmt(accuracy(gold, pred))}    macro-F1 = {_fmt(macro_f1(gold, pred, labels))}")
    return "\n".join(lines)
