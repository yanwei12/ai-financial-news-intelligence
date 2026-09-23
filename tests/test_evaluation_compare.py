"""evaluate.py and compare.py on the shipped synthetic examples (offline)."""

import json
import shutil
from pathlib import Path

import pytest

from evaluation import compare, evaluate
from evaluation.annotation_schema import Annotation

EXAMPLES = Path(__file__).resolve().parent.parent / "evaluation" / "annotations" / "examples"


def copy_examples(target: Path, annotator: str) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    for source in EXAMPLES.glob("*.json"):
        record = json.loads(source.read_text(encoding="utf-8"))
        record["annotator"] = annotator
        (target / source.name).write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return target


def edit(folder: Path, name: str, change) -> None:
    path = folder / name
    record = json.loads(path.read_text(encoding="utf-8"))
    change(record)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")


def claim_count(folder: Path) -> int:
    return sum(len(Annotation.model_validate_json(p.read_text(encoding="utf-8")).claims) for p in folder.glob("*.json"))


# ------------------------------------------------------------- evaluate


def test_evaluate_reports_synthetic_samples_separately(capsys):
    assert evaluate.evaluate([EXAMPLES], "rules") == 0
    out = capsys.readouterr().out
    assert "SYNTHETIC SAMPLES" in out and "REAL SAMPLES" not in out
    assert "confusion matrix" in out and "macro-F1" in out and "n=7" in out
    assert "too noisy to rank methods" in out


def test_evaluate_refuses_ai_drafted_gold_unless_allowed(tmp_path, capsys):
    drafts = copy_examples(tmp_path / "drafts", "claude-draft")
    assert evaluate.evaluate([drafts], "rules") == 2
    assert "REFUSED" in capsys.readouterr().out

    assert evaluate.evaluate([drafts], "rules", allow_draft_gold=True) == 0
    out = capsys.readouterr().out
    assert "WARNING: gold includes AI drafts" in out and "NOT a benchmark" in out


def test_evaluate_fails_loudly_when_the_article_changed(tmp_path):
    folder = copy_examples(tmp_path / "gold", "tester")
    edit(folder, "syn-04-supported.json", lambda r: r["article"].update(text=r["article"]["text"] + "\n\n多出來的一段。"))
    with pytest.raises(ValueError, match="document_id"):
        evaluate.evaluate([folder], "rules")


# ------------------------------------------------------------- compare


def test_identical_annotations_fully_agree(tmp_path):
    a, b = copy_examples(tmp_path / "a", "one"), copy_examples(tmp_path / "b", "two")
    comparisons, only_a, only_b = compare.compare(a, b)
    total = claim_count(a)
    text = compare.format_comparison(comparisons, only_a, only_b, "a", "b")
    assert not only_a and not only_b
    assert sum(len(c.matched) for c in comparisons) == total
    assert f"identical relation+gap_type: {total}/{total}" in text
    assert "DIFF " not in text and "DIFFERENT" not in text and "Discuss the DIFF" not in text
    assert "No disagreements" in text


def test_a_changed_label_is_reported_with_both_notes(tmp_path):
    a, b = copy_examples(tmp_path / "a", "one"), copy_examples(tmp_path / "b", "two")
    edit(b, "syn-01-certainty.json", lambda r: r["claims"][0].update(gap_type="none", note="reviewer disagrees"))
    text = compare.format_comparison(*compare.compare(a, b), "a", "b")
    assert "DIFF A C1 supports/certainty  B C1 supports/none" in text
    assert "reviewer disagrees" in text
    assert "syn-01-certainty   overall: A=missing_conditions  B=supported  [DIFFERENT]" in text
    assert "articles with the same overall verdict" in text


def test_claims_are_matched_by_evidence_even_when_worded_and_numbered_differently(tmp_path):
    a, b = copy_examples(tmp_path / "a", "one"), copy_examples(tmp_path / "b", "two")

    def reword(record):
        record["claims"][0].update(claim_id="C7", text="公司已經確定要漲價")

    edit(b, "syn-01-certainty.json", reword)
    comparisons, _, _ = compare.compare(a, b)
    item = next(c for c in comparisons if c.sample_id == "syn-01-certainty")
    assert len(item.matched) == 1 and item.matched[0][1].claim_id == "C7" and not item.only_a and not item.only_b


def test_a_claim_only_one_annotator_made_is_listed_not_forced_into_a_match(tmp_path):
    a, b = copy_examples(tmp_path / "a", "one"), copy_examples(tmp_path / "b", "two")
    edit(b, "syn-06-implied-cause.json", lambda r: r.update(claims=r["claims"][:1]))
    comparisons, _, _ = compare.compare(a, b)
    item = next(c for c in comparisons if c.sample_id == "syn-06-implied-cause")
    assert [c.claim_id for c in item.only_a] == ["C2"] and not item.only_b
    assert "A only  C2 insufficient/insufficient" in compare.format_comparison(comparisons, [], [], "a", "b")


def test_samples_present_on_one_side_only_and_text_version_warning(tmp_path):
    a, b = copy_examples(tmp_path / "a", "one"), copy_examples(tmp_path / "b", "two")
    (b / "syn-05-insufficient.json").unlink()
    edit(b, "syn-02-scope.json", lambda r: r["article"].update(document_id="0" * 64))
    comparisons, only_a, only_b = compare.compare(a, b)
    text = compare.format_comparison(comparisons, only_a, only_b, "a", "b")
    assert only_a == ["syn-05-insufficient"] and only_b == []
    assert "only in A" in text and "different text versions" in text


def test_two_annotators_in_one_folder_is_an_error(tmp_path):
    a, b = copy_examples(tmp_path / "a", "one"), copy_examples(tmp_path / "b", "two")
    shutil.copy(EXAMPLES / "syn-01-certainty.json", a / "syn-01-certainty.second.json")
    with pytest.raises(ValueError, match="appears twice"):
        compare.compare(a, b)


def test_compare_cli_rejects_a_missing_folder(tmp_path, capsys):
    assert compare.main([str(tmp_path / "nope"), str(tmp_path)]) == 1
    assert "not a folder" in capsys.readouterr().err
