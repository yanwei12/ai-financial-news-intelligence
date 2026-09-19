"""Metrics and the rule baseline. Hand-computed expectations, no network."""

import pytest

from evaluation import baseline_rules
from evaluation.dataset import Pair
from evaluation.metrics import accuracy, cohen_kappa, confusion, format_report, macro_f1, per_class

LABELS = ["a", "b", "c"]
GOLD = ["a", "a", "a", "b", "b", "c"]
PRED = ["a", "a", "b", "b", "b", "a"]


# ------------------------------------------------------------- metrics


def test_confusion_and_accuracy():
    counts = confusion(GOLD, PRED)
    assert counts[("a", "a")] == 2 and counts[("a", "b")] == 1 and counts[("c", "a")] == 1
    assert accuracy(GOLD, PRED) == pytest.approx(4 / 6)


def test_per_class_precision_recall_f1_by_hand():
    stats = per_class(GOLD, PRED, LABELS)
    assert stats["a"]["precision"] == pytest.approx(2 / 3) and stats["a"]["recall"] == pytest.approx(2 / 3)
    assert stats["b"]["precision"] == pytest.approx(2 / 3) and stats["b"]["recall"] == pytest.approx(1.0)
    assert stats["b"]["f1"] == pytest.approx(0.8)
    # class c: never predicted, one gold example -> precision undefined (None, not 0), F1 = 0
    assert stats["c"]["precision"] is None and stats["c"]["recall"] == 0.0 and stats["c"]["f1"] == 0.0
    assert stats["c"]["support"] == 1 and stats["c"]["predicted"] == 0


def test_macro_f1_skips_classes_absent_from_gold_and_predictions():
    assert macro_f1(["a", "b"], ["a", "b"], ["a", "b", "never_used"]) == 1.0
    assert per_class(["a"], ["a"], ["a", "z"])["z"]["f1"] is None
    assert macro_f1(GOLD, PRED, LABELS) == pytest.approx((2 / 3 + 0.8 + 0.0) / 3)
    assert macro_f1([], [], LABELS) is None


def test_kappa():
    assert cohen_kappa(["x", "y", "x", "y"], ["x", "y", "x", "y"]) == 1.0
    assert cohen_kappa(["x", "y", "x", "y"], ["y", "x", "y", "x"]) == -1.0
    # 3 of 4 agree, expected chance agreement 0.5 -> (0.75 - 0.5) / 0.5
    assert cohen_kappa(["x", "x", "y", "y"], ["x", "x", "y", "x"]) == pytest.approx(0.5)
    assert cohen_kappa(["x", "x"], ["x", "x"]) is None        # undefined, must not pretend to be 1
    assert cohen_kappa([], []) is None


def test_length_mismatch_is_an_error():
    with pytest.raises(ValueError):
        accuracy(["a"], ["a", "b"])


def test_report_shows_undefined_as_n_a():
    report = format_report("demo", GOLD, PRED, LABELS)
    assert "n=6" in report and "n/a" in report and "macro-F1 = 0.49" in report


# ------------------------------------------------------------- rule baseline


def pair(claim, evidence, title="標題", relation="supports", gap="none"):
    return Pair("s", "C1", "g", "tester", True, title, claim, tuple(f"P{i:03d}" for i, _ in enumerate(evidence, 1)),
                tuple(evidence), relation, gap, "supported")


def test_no_evidence_or_unrelated_evidence_is_insufficient():
    assert baseline_rules.predict(pair("星河公司即將裁員", [])) == ("insufficient", "insufficient")
    assert baseline_rules.predict(pair("星河公司即將裁員", ["天氣預報顯示明日多雲時晴。"])) == ("insufficient", "insufficient")


def test_negation_in_a_matching_sentence_is_a_conflict():
    result = baseline_rules.predict(pair("星河公司今年將關閉工廠", ["執行長表示，星河公司今年不會關閉任何工廠。"]))
    assert result == ("conflicts", "contradiction")


def test_certainty_needs_a_headline_word_and_a_hedge_in_the_evidence():
    evidence = ["星河公司正在評估調整售價，尚未作出決定。"]
    assert baseline_rules.predict(pair("星河公司調整售價", evidence, title="星河公司確定調漲價格")) == ("supports", "certainty")
    assert baseline_rules.predict(pair("星河公司調整售價", evidence, title="星河公司調漲價格")) == ("supports", "none")


def test_scope_needs_a_headline_word_and_a_limit_in_the_evidence():
    evidence = ["星河公司調漲售價，只適用北美部分產品。"]
    assert baseline_rules.predict(pair("星河公司全面調漲售價", evidence, title="星河公司全面漲價")) == ("supports", "scope")


def test_every_prediction_is_a_legal_pair():
    from evaluation.annotation_schema import derive_verdict
    cases = [pair("星河公司即將裁員", []), pair("星河公司調整售價", ["星河公司調整售價。"]),
             pair("星河公司今年將關閉工廠", ["星河公司今年不會關閉工廠。"])]
    for case in cases:
        derive_verdict(*baseline_rules.predict(case))       # raises on an illegal combination


def test_cue_lists_are_frozen_to_the_version():
    """Changing any cue word or threshold must come with a new VERSION (and a fresh evaluation)."""
    frozen = {"rules-v0": "5d9e4dc3356de53aa0223aab27c476367fee1e163ecb7eeb92fcdf455b4dbda5"}
    assert baseline_rules.VERSION in frozen, "new VERSION: add its fingerprint here on purpose"
    assert baseline_rules.cue_fingerprint() == frozen[baseline_rules.VERSION], (
        "cue lists changed without bumping baseline_rules.VERSION; that would tune the baseline on its test data")
