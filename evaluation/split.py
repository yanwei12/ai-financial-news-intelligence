"""
Deterministic, group-aware train / validation / test split.

    python -m evaluation.split evaluation/annotations/real
    python -m evaluation.split evaluation/annotations/real --export evaluation/cache/exports

Articles about the same event (or reprints) share a group_id and always land in
the same split, so a model cannot be trained on one telling of an event and
tested on another.

A group's split depends only on its group_id and a fixed salt, never on what
else exists. Adding annotations later can therefore never move an existing
group into or out of the test set: once the test set has content, it stays
fixed (plan §9). The price is that with very few groups a split can be empty;
the report says so instead of hiding it.

Real and synthetic samples are reported apart. Gold labels written by an AI
draft are refused unless allowed, as in evaluate.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable

from evaluation.dataset import Pair, build_pairs, is_ai_draft, load_annotations
from evaluation.validate_annotations import DEFAULT_CACHE_DIR

SALT = "headline-split-v1"
RATIOS = (("train", 0.70), ("validation", 0.15), ("test", 0.15))
SPLITS = tuple(name for name, _ in RATIOS)
MIN_GROUPS_PER_SPLIT = 5


def assign_split(group_id: str, salt: str = SALT, ratios=RATIOS) -> str:
    digest = hashlib.sha256(f"{salt}:{group_id}".encode("utf-8")).digest()
    point = int.from_bytes(digest[:8], "big") / 2**64
    cumulative = 0.0
    for name, share in ratios:
        cumulative += share
        if point < cumulative:
            return name
    return ratios[-1][0]


def split_pairs(pairs: list[Pair]) -> dict[str, list[Pair]]:
    result: dict[str, list[Pair]] = {name: [] for name in SPLITS}
    for pair in pairs:
        result[assign_split(pair.group_id)].append(pair)
    return result


def find_leaks(split: dict[str, list[Pair]]) -> list[str]:
    """Group ids that appear in more than one split. Empty by construction; kept as an audit."""
    homes: dict[str, set[str]] = defaultdict(set)
    for name, pairs in split.items():
        for pair in pairs:
            homes[pair.group_id].add(name)
    return sorted(group for group, names in homes.items() if len(names) > 1)


def export_pairs(split: dict[str, list[Pair]], folder: Path) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for name, pairs in split.items():
        path = folder / f"{name}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for pair in pairs:
                handle.write(json.dumps({
                    "split": name, "sample_id": pair.sample_id, "claim_id": pair.claim_id,
                    "group_id": pair.group_id, "annotator": pair.annotator, "is_synthetic": pair.is_synthetic,
                    "title": pair.title, "claim": pair.claim_text,
                    "evidence": [{"id": i, "text": t} for i, t in zip(pair.evidence_ids, pair.evidence_texts)],
                    "relation": pair.relation, "gap_type": pair.gap_type, "verdict": pair.verdict,
                }, ensure_ascii=False) + "\n")
        written.append(path)
    return written


def _is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def report(pairs: list[Pair], out: Callable[[str], None]) -> None:
    for synthetic, label in ((False, "real"), (True, "synthetic")):
        subset = [p for p in pairs if p.is_synthetic == synthetic]
        if not subset:
            continue
        split = split_pairs(subset)
        out(f"\n{label.upper()}   {len(subset)} claims, {len({p.group_id for p in subset})} groups")
        out(f"  {'split':<12}{'groups':>7}{'articles':>9}{'claims':>7}   relations")
        for name in SPLITS:
            members = split[name]
            relations = Counter(p.relation for p in members)
            out(f"  {name:<12}{len({p.group_id for p in members}):>7}{len({p.sample_id for p in members}):>9}"
                f"{len(members):>7}   " + (", ".join(f"{k}={v}" for k, v in sorted(relations.items())) or "-"))
        for name in SPLITS:
            groups = len({p.group_id for p in split[name]})
            if groups < MIN_GROUPS_PER_SPLIT:
                out(f"  note: {label} {name} has {groups} group(s) (< {MIN_GROUPS_PER_SPLIT}); "
                    "too few to measure anything with yet")
        leaks = find_leaks(split)
        out("  leakage check: " + ("FAILED for " + ", ".join(leaks) if leaks else "no group crosses splits"))


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--export", type=Path, help="write <split>.jsonl files into this folder")
    parser.add_argument("--allow-draft", action="store_true", help="include AI-drafted annotations")
    parser.add_argument("--allow-real-text-export", action="store_true",
                        help="allow writing real article text outside evaluation/cache (it is not ours to share)")
    args = parser.parse_args(argv)

    annotations = load_annotations(args.paths)
    drafts = sorted({a.annotator for a in annotations if is_ai_draft(a)})
    if drafts and not args.allow_draft:
        print(f"REFUSED: annotations include AI drafts ({', '.join(drafts)}). Use --allow-draft to include them.")
        return 2
    pairs = build_pairs(annotations, args.cache_dir)
    if drafts:
        print(f"WARNING: includes AI drafts ({', '.join(drafts)}); not a valid basis for a benchmark.")
    print(f"salt {SALT}; ratios " + ", ".join(f"{n} {s:.0%}" for n, s in RATIOS))
    report(pairs, print)

    if args.export:
        has_real = any(not p.is_synthetic for p in pairs)
        if has_real and not _is_inside(args.export, args.cache_dir) and not args.allow_real_text_export:
            print(f"\nREFUSED export: real article text may only go inside {args.cache_dir} (git-ignored). "
                  "Pass --allow-real-text-export if you are sure.")
            return 2
        for path in export_pairs(split_pairs(pairs), args.export):
            print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
