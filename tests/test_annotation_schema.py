"""Annotation format and validator. Offline; no network, no model calls."""

import itertools
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from evaluation.annotation_schema import (
    Annotation,
    AnnotatedClaim,
    check_against_article,
    derive_verdict,
)
from evaluation.validate_annotations import main as validate_main
from src.ai import headline
from src.processing.article_text import content_hash
from src.processing.cleaner import prepare_article

EXAMPLES = Path(__file__).resolve().parent.parent / "evaluation" / "annotations" / "examples"
BODY = "第一段內容。\n\n第二段內容。"
TITLE = "標題"


def synthetic(**overrides) -> dict:
    prepared = prepare_article(TITLE, BODY)
    record = {
        "sample_id": "s1", "group_id": "g1", "annotator": "tester", "is_synthetic": True,
        "article": {"title": TITLE, "document_id": prepared.document_id, "text": BODY},
        "claims": [{"claim_id": "C1", "text": "主張", "relation": "supports",
                    "gap_type": "none", "evidence_ids": ["P001"]}],
    }
    record.update(overrides)
    return record


def real(**overrides) -> dict:
    prepared = prepare_article(TITLE, BODY)
    record = synthetic()
    record["is_synthetic"] = False
    record["article"] = {"title": TITLE, "document_id": prepared.document_id,
                         "url": "https://tw.stock.yahoo.com/news/x.html", "content_hash": content_hash(BODY)}
    record.update(overrides)
    return record


# ------------------------------------------------------------- verdict rules


@pytest.mark.parametrize(
    "relation, gap, verdict",
    [
        ("supports", "none", "supported"),
        ("supports", "certainty", "missing_conditions"),
        ("supports", "scope", "missing_conditions"),
        ("conflicts", "contradiction", "contradicted"),
        ("insufficient", "insufficient", "insufficient"),
    ],
)
def test_verdict_is_derived_from_relation_and_gap(relation, gap, verdict):
    assert derive_verdict(relation, gap) == verdict


@pytest.mark.parametrize(
    "relation, gap",
    [("supports", "contradiction"), ("supports", "insufficient"), ("conflicts", "none"),
     ("conflicts", "certainty"), ("insufficient", "scope"), ("insufficient", "none")],
)
def test_contradictory_pairs_are_rejected(relation, gap):
    with pytest.raises(ValueError, match="cannot be combined"):
        derive_verdict(relation, gap)
    with pytest.raises(ValidationError):
        AnnotatedClaim(claim_id="C1", text="x", relation=relation, gap_type=gap, evidence_ids=["P001"])


def test_verdict_rules_match_the_gemini_prototype():
    """Human labels and the LLM output must accept exactly the same (verdict, gap_type) pairs."""
    article = prepare_article(TITLE, BODY)
    ours = {(derive_verdict(r, g), g) for r, g in [
        ("supports", "none"), ("supports", "certainty"), ("supports", "scope"),
        ("conflicts", "contradiction"), ("insufficient", "insufficient")]}
    for verdict, gap in itertools.product(
        ["supported", "missing_conditions", "contradicted", "insufficient"],
        ["none", "certainty", "scope", "contradiction", "insufficient"],
    ):
        raw = json.dumps({"summary": "s", "claims": [{
            "claim": "c", "verdict": verdict, "explanation": "e",
            "gap_type": gap, "evidence_ids": ["P001"]}]})
        try:
            headline.validate_result(raw, article)
            accepted = True
        except headline.AnalysisError:
            accepted = False
        assert accepted == ((verdict, gap) in ours), (verdict, gap)


def test_overall_verdict_uses_the_most_serious_claim():
    claims = [
        {"claim_id": "C1", "text": "a", "relation": "supports", "gap_type": "none", "evidence_ids": ["P001"]},
        {"claim_id": "C2", "text": "b", "relation": "insufficient", "gap_type": "insufficient"},
        {"claim_id": "C3", "text": "c", "relation": "supports", "gap_type": "scope", "evidence_ids": ["P002"]},
    ]
    assert Annotation.model_validate(synthetic(claims=claims)).overall_verdict == "missing_conditions"
    claims.append({"claim_id": "C4", "text": "d", "relation": "conflicts",
                   "gap_type": "contradiction", "evidence_ids": ["P001"]})
    assert Annotation.model_validate(synthetic(claims=claims)).overall_verdict == "contradicted"


# ------------------------------------------------------------- schema rules


def test_evidence_rules():
    def claim(**kw):
        base = {"claim_id": "C1", "text": "x", "relation": "supports", "gap_type": "none", "evidence_ids": ["P001"]}
        return AnnotatedClaim(**{**base, **kw})

    assert claim(relation="insufficient", gap_type="insufficient", evidence_ids=[]).evidence_ids == []
    with pytest.raises(ValidationError):
        claim(evidence_ids=[])                      # supports needs evidence
    with pytest.raises(ValidationError):
        claim(evidence_ids=["1"])                   # bad id shape
    with pytest.raises(ValidationError):
        claim(evidence_ids=["P001", "P001"])        # duplicates


def test_real_articles_must_not_embed_text_and_need_url_and_hash():
    assert Annotation.model_validate(real()).is_synthetic is False
    bad_text = real()
    bad_text["article"]["text"] = BODY
    with pytest.raises(ValidationError, match="must not embed"):
        Annotation.model_validate(bad_text)
    for missing in ("url", "content_hash"):
        record = real()
        del record["article"][missing]
        with pytest.raises(ValidationError, match="needs article.url"):
            Annotation.model_validate(record)


def test_synthetic_needs_text():
    record = synthetic()
    del record["article"]["text"]
    with pytest.raises(ValidationError, match="must include article.text"):
        Annotation.model_validate(record)


def test_unknown_fields_and_duplicate_claim_ids_are_rejected():
    with pytest.raises(ValidationError):
        Annotation.model_validate(synthetic(verdict="supported"))   # verdict is derived, never typed
    claim = synthetic()["claims"][0]
    with pytest.raises(ValidationError, match="unique"):
        Annotation.model_validate(synthetic(claims=[claim, dict(claim)]))


# ------------------------------------------------------------- against the article


def test_check_against_article():
    annotation = Annotation.model_validate(synthetic())
    prepared = prepare_article(TITLE, BODY)
    assert check_against_article(annotation, prepared) == []

    unknown = synthetic()
    unknown["claims"][0]["evidence_ids"] = ["P001", "P009"]
    problems = check_against_article(Annotation.model_validate(unknown), prepared)
    assert any("P009" in p for p in problems)

    changed = prepare_article(TITLE, BODY + "\n\n新增的一段。")
    assert any("document_id" in p for p in check_against_article(annotation, changed))
    assert any("document_id" in p for p in check_against_article(annotation, prepare_article("別的標題", BODY)))


def test_content_hash_mismatch_is_reported():
    annotation = Annotation.model_validate(real())
    other_body = "第一段內容。\n\n第二段被改過。"
    problems = check_against_article(annotation, prepare_article(TITLE, other_body))
    assert any("content_hash" in p for p in problems)


# ------------------------------------------------------------- shipped examples + CLI


def test_shipped_examples_are_valid_and_cover_all_four_verdicts(capsys):
    assert validate_main([str(EXAMPLES)]) == 0
    out = capsys.readouterr().out
    assert "0 failed" in out
    files = sorted(EXAMPLES.glob("*.json"))
    verdicts = {Annotation.model_validate_json(f.read_text(encoding="utf-8")).overall_verdict for f in files}
    assert verdicts == {"supported", "missing_conditions", "contradicted", "insufficient"}
    assert "synthetic:" in out and "real:" not in out


def test_cli_reports_bad_files_and_missing_cache(tmp_path, capsys):
    bad_ref = synthetic()
    bad_ref["claims"][0]["evidence_ids"] = ["P009"]
    (tmp_path / "bad_ref.json").write_text(json.dumps(bad_ref, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "bad_json.json").write_text("{not json", encoding="utf-8")
    (tmp_path / "uncached.json").write_text(json.dumps(real(), ensure_ascii=False), encoding="utf-8")

    assert validate_main([str(tmp_path), "--cache-dir", str(tmp_path / "cache")]) == 1
    out = capsys.readouterr().out
    assert "unknown paragraph id(s) P009" in out
    assert "invalid json" in out.lower()
    assert "not cached" in out
    assert "0 ok, 3 failed" in out


def test_missing_path_and_empty_folder_are_reported_cleanly(tmp_path, capsys):
    assert validate_main([str(tmp_path / "nope")]) == 1
    assert "path not found" in capsys.readouterr().err
    assert validate_main([str(tmp_path)]) == 1          # exists but holds no .json files
    assert "no .json files" in capsys.readouterr().err


def test_real_article_validates_against_the_local_cache(tmp_path, capsys):
    (tmp_path / "article.json").write_text(json.dumps(real(), ensure_ascii=False), encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / f"{content_hash(BODY)}.json").write_text(
        json.dumps({"title": TITLE, "body": BODY}, ensure_ascii=False), encoding="utf-8")

    assert validate_main([str(tmp_path / "article.json"), "--cache-dir", str(cache)]) == 0
    out = capsys.readouterr().out
    assert "real: supported=1" in out
