"""Group-aware split. Offline; uses the shipped synthetic examples."""

import json
from pathlib import Path

import pytest

from evaluation import evaluate, split
from evaluation.dataset import Pair, build_pairs, load_annotations

EXAMPLES = Path(__file__).resolve().parent.parent / "evaluation" / "annotations" / "examples"


def fake_pair(group, sample="s", synthetic=True):
    return Pair(sample, "C1", group, "tester", synthetic, "標題", "主張", ("P001",), ("內文",),
                "supports", "none", "supported")


def example_pairs():
    return build_pairs(load_annotations([EXAMPLES]))


# ------------------------------------------------------------- assignment


def test_assignment_is_pinned_so_it_never_drifts():
    """Changing the salt or algorithm would silently reshuffle a test set that already has content."""
    assert split.SALT == "headline-split-v1"
    # Values taken from the algorithm as first released, covering all three splits.
    pinned = {"g-0": "train", "g-1": "train", "g-2": "train", "g-9": "test", "g-12": "test", "g-17": "test",
              "g-18": "validation", "g-26": "validation", "g-30": "validation",
              "syn-01": "train", "syn-02": "test", "syn-06": "test"}
    assert {g: split.assign_split(g) for g in pinned} == pinned


def test_shares_are_roughly_70_15_15_over_many_groups():
    names = [split.assign_split(f"group-{i}") for i in range(20000)]
    for name, share in split.RATIOS:
        assert names.count(name) / len(names) == pytest.approx(share, abs=0.02)


def test_a_group_never_moves_when_other_groups_are_added():
    groups = [f"event-{i}" for i in range(50)]
    alone = {g: split.assign_split(g) for g in groups}
    pairs = [fake_pair(g, sample=g) for g in groups] + [fake_pair(f"later-{i}") for i in range(200)]
    together = {p.group_id: name for name, members in split.split_pairs(pairs).items() for p in members}
    assert all(together[g] == alone[g] for g in groups)


def test_articles_of_one_event_share_a_split_and_no_group_leaks():
    pairs = [fake_pair("same-event", sample="a"), fake_pair("same-event", sample="b"),
             fake_pair("same-event", sample="c")] + [fake_pair(f"other-{i}") for i in range(30)]
    result = split.split_pairs(pairs)
    homes = {name for name, members in result.items() for p in members if p.group_id == "same-event"}
    assert len(homes) == 1
    assert split.find_leaks(result) == []


def test_find_leaks_reports_a_group_in_two_splits():
    leaky = {"train": [fake_pair("g1")], "validation": [], "test": [fake_pair("g1")]}
    assert split.find_leaks(leaky) == ["g1"]


# ------------------------------------------------------------- export


def test_export_writes_one_jsonl_per_split_with_evidence(tmp_path):
    pairs = example_pairs()
    files = split.export_pairs(split.split_pairs(pairs), tmp_path)
    assert sorted(p.name for p in files) == ["test.jsonl", "train.jsonl", "validation.jsonl"]
    rows = [json.loads(line) for f in files for line in f.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(pairs)
    sample = next(r for r in rows if r["sample_id"] == "syn-01-certainty")
    assert sample["split"] == split.assign_split("syn-01")
    assert sample["relation"] == "supports" and sample["gap_type"] == "certainty"
    assert sample["evidence"][0]["id"] == "P001" and "評估" in sample["evidence"][0]["text"]


def test_cli_reports_synthetic_separately_and_can_export(tmp_path, capsys):
    assert split.main([str(EXAMPLES), "--export", str(tmp_path / "out")]) == 0
    out = capsys.readouterr().out
    assert "SYNTHETIC" in out and "REAL" not in out.replace("real article", "")
    assert "no group crosses splits" in out and "wrote" in out
    assert (tmp_path / "out" / "train.jsonl").exists()


def test_cli_refuses_ai_drafts_unless_allowed(tmp_path, capsys):
    drafts = tmp_path / "drafts"
    drafts.mkdir()
    record = json.loads((EXAMPLES / "syn-01-certainty.json").read_text(encoding="utf-8"))
    record["annotator"] = "claude-draft"
    (drafts / "a.json").write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    assert split.main([str(drafts)]) == 2
    assert "REFUSED" in capsys.readouterr().out
    assert split.main([str(drafts), "--allow-draft"]) == 0
    assert "WARNING" in capsys.readouterr().out


def test_real_article_text_is_only_exported_inside_the_private_cache(tmp_path, capsys):
    cache = tmp_path / "cache"
    cache.mkdir()
    body = "第一段內容，寫得夠長一點。" * 10
    from src.processing.article_text import content_hash
    from src.processing.cleaner import prepare_article
    prepared = prepare_article("真實標題", body)
    (cache / f"{content_hash(body)}.json").write_text(json.dumps({"title": "真實標題", "body": body}), encoding="utf-8")
    folder = tmp_path / "ann"
    folder.mkdir()
    (folder / "r1.json").write_text(json.dumps({
        "sample_id": "r1", "group_id": "g", "annotator": "max", "is_synthetic": False,
        "article": {"title": "真實標題", "document_id": prepared.document_id, "url": "https://tw.stock.yahoo.com/x",
                    "content_hash": content_hash(body)},
        "claims": [{"claim_id": "C1", "text": "x", "relation": "supports", "gap_type": "none", "evidence_ids": ["P001"]}],
    }, ensure_ascii=False), encoding="utf-8")

    outside = tmp_path / "public"
    assert split.main([str(folder), "--cache-dir", str(cache), "--export", str(outside)]) == 2
    assert "REFUSED export" in capsys.readouterr().out and not outside.exists()
    assert split.main([str(folder), "--cache-dir", str(cache), "--export", str(cache / "exports")]) == 0
    assert (cache / "exports" / "train.jsonl").exists() or (cache / "exports" / "test.jsonl").exists()
    assert split.main([str(folder), "--cache-dir", str(cache), "--export", str(outside),
                       "--allow-real-text-export"]) == 0


def test_few_groups_are_called_out_not_hidden(capsys):
    split.report(example_pairs(), print)
    assert "too few to measure anything with yet" in capsys.readouterr().out


# ------------------------------------------------------------- evaluate --split


def test_evaluate_can_score_one_split_only(capsys):
    pairs = example_pairs()
    in_train = [p for p in pairs if split.assign_split(p.group_id) == "train"]
    assert evaluate.evaluate([EXAMPLES], "rules", split="train") == 0
    out = capsys.readouterr().out
    assert "split: train only" in out and f"claims: {len(in_train)}" in out


def test_evaluate_test_split_carries_the_reminder_and_an_empty_split_is_handled(capsys):
    assert evaluate.evaluate([EXAMPLES], "rules", split="test") == 0
    out = capsys.readouterr().out
    assert "reminder: score the test split rarely and last" in out
    empty = [n for n in split.SPLITS if not [p for p in example_pairs() if split.assign_split(p.group_id) == n]]
    if empty:
        assert evaluate.evaluate([EXAMPLES], "rules", split=empty[0]) == 0
        assert "no claims in this split yet." in capsys.readouterr().out
