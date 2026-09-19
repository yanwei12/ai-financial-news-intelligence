"""
Score a predictor against human annotations.

    python -m evaluation.evaluate --gold evaluation/annotations/real --predictor rules

The predictor sees each headline claim and the evidence paragraphs the
annotator cited, and answers relation / gap_type. The verdict is derived from
those two, the same way it is for humans. This measures classification with
correct evidence (plan §8, first test). Retrieval quality is a separate test.

Real and synthetic samples are reported apart. Gold labels written by an AI
draft are refused unless you explicitly allow them, because scoring one AI
against another AI's opinion is not a benchmark.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable

from evaluation import baseline_rules
from evaluation.annotation_schema import derive_verdict
from evaluation.dataset import Pair, build_pairs, is_ai_draft, load_annotations
from evaluation.metrics import format_report
from evaluation.split import SPLITS, assign_split
from evaluation.validate_annotations import DEFAULT_CACHE_DIR

PREDICTORS: dict[str, Callable[[Pair], tuple[str, str]]] = {"rules": baseline_rules.predict}
RELATIONS = ["supports", "conflicts", "insufficient"]
GAP_TYPES = ["none", "certainty", "scope", "contradiction", "insufficient"]
VERDICTS = ["supported", "missing_conditions", "contradicted", "insufficient"]
SMALL_SAMPLE = 30


def evaluate(
    gold_paths: list[Path],
    predictor: str,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    allow_draft_gold: bool = False,
    out: Callable[[str], None] = print,
    split: str = "all",
) -> int:
    annotations = load_annotations(gold_paths)
    drafts = sorted({a.annotator for a in annotations if is_ai_draft(a)})
    if drafts and not allow_draft_gold:
        out(f"REFUSED: gold labels include AI-drafted annotations ({', '.join(drafts)}).\n"
            "Review them into your own annotator files first. To only test the pipeline, "
            "pass --allow-draft-gold; the numbers are then not a benchmark.")
        return 2

    predict = PREDICTORS[predictor]
    pairs = build_pairs(annotations, cache_dir)
    if split != "all":
        pairs = [p for p in pairs if assign_split(p.group_id) == split]
        out(f"split: {split} only (group-aware, see evaluation/split.py)")
        if split == "test":
            out("reminder: score the test split rarely and last. Every look at it and every change made "
                "because of it uses it up as an honest measure.")
    out(f"predictor: {predictor} ({baseline_rules.VERSION})   claims: {len(pairs)}   "
        f"articles: {len({p.sample_id for p in pairs})}")
    if not pairs:
        out("no claims in this split yet.")
        return 0
    if drafts:
        out(f"WARNING: gold includes AI drafts ({', '.join(drafts)}). This checks the pipeline only; it is NOT a benchmark.")

    for synthetic, label in ((False, "real"), (True, "synthetic")):
        subset = [p for p in pairs if p.is_synthetic == synthetic]
        if not subset:
            continue
        predictions = [predict(p) for p in subset]
        out(f"\n{'=' * 60}\n{label.upper()} SAMPLES")
        if len(subset) < SMALL_SAMPLE:
            out(f"note: n={len(subset)} < {SMALL_SAMPLE}. These numbers are too noisy to rank methods; "
                "they show the pipeline works and where errors are.")
        out("\n" + format_report("relation", [p.relation for p in subset], [r for r, _ in predictions], RELATIONS))
        out("\n" + format_report("gap_type", [p.gap_type for p in subset], [g for _, g in predictions], GAP_TYPES))
        predicted_verdicts = [derive_verdict(r, g) for r, g in predictions]
        out("\n" + format_report("verdict (derived)", [p.verdict for p in subset], predicted_verdicts, VERDICTS))
        wrong = [(p, r, g) for p, (r, g) in zip(subset, predictions) if (r, g) != (p.relation, p.gap_type)]
        out(f"\n  errors: {len(wrong)} of {len(subset)}")
        for pair, relation, gap in wrong:
            out(f"   {pair.sample_id} {pair.claim_id}  gold={pair.relation}/{pair.gap_type}  "
                f"pred={relation}/{gap}  | {pair.claim_text[:36]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--gold", nargs="+", type=Path, required=True)
    parser.add_argument("--predictor", choices=sorted(PREDICTORS), default="rules")
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    parser.add_argument("--allow-draft-gold", action="store_true")
    parser.add_argument("--split", choices=["all", *SPLITS], default="all",
                        help="score only one group-aware split (default: everything)")
    args = parser.parse_args(argv)
    return evaluate(args.gold, args.predictor, args.cache_dir, args.allow_draft_gold, split=args.split)


if __name__ == "__main__":
    raise SystemExit(main())
