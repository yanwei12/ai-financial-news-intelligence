"""
Compare two sets of annotations of the same articles.

    python -m evaluation.compare evaluation/annotations/drafts evaluation/annotations/real

Use it for two annotators, or for an AI draft against the human review of it.
Claims are matched by the evidence paragraphs they cite and by how similar
their wording is, because two people phrase a claim differently. Read every
match: an automatic alignment can be wrong. Nothing is called "right" here;
the list of disagreements is what the two annotators discuss (plan §9).
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

from evaluation.annotation_schema import AnnotatedClaim, Annotation
from evaluation.dataset import load_annotations
from evaluation.metrics import cohen_kappa

MIN_MATCH_SCORE = 0.3
SMALL_SAMPLE = 30


@dataclass
class SampleComparison:
    sample_id: str
    verdict_a: str
    verdict_b: str
    same_text: bool
    matched: list[tuple[AnnotatedClaim, AnnotatedClaim]] = field(default_factory=list)
    only_a: list[AnnotatedClaim] = field(default_factory=list)
    only_b: list[AnnotatedClaim] = field(default_factory=list)


def _score(a: AnnotatedClaim, b: AnnotatedClaim) -> float:
    evidence_a, evidence_b = set(a.evidence_ids), set(b.evidence_ids)
    jaccard = len(evidence_a & evidence_b) / len(evidence_a | evidence_b) if evidence_a | evidence_b else 1.0
    return 0.6 * jaccard + 0.4 * SequenceMatcher(None, a.text, b.text).ratio()


def align(claims_a: list[AnnotatedClaim], claims_b: list[AnnotatedClaim]):
    """Greedy best-first matching. Returns (matched pairs, unmatched in a, unmatched in b)."""
    scored = sorted(
        ((_score(a, b), i, j) for i, a in enumerate(claims_a) for j, b in enumerate(claims_b)),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    used_a: set[int] = set()
    used_b: set[int] = set()
    matched = []
    for score, i, j in scored:
        if score < MIN_MATCH_SCORE:
            break
        if i in used_a or j in used_b:
            continue
        used_a.add(i)
        used_b.add(j)
        matched.append((claims_a[i], claims_b[j]))
    matched.sort(key=lambda pair: pair[0].claim_id)
    return (matched,
            [c for i, c in enumerate(claims_a) if i not in used_a],
            [c for j, c in enumerate(claims_b) if j not in used_b])


def _by_sample(annotations: list[Annotation]) -> dict[str, Annotation]:
    result: dict[str, Annotation] = {}
    for annotation in annotations:
        if annotation.sample_id in result:
            raise ValueError(f"sample_id {annotation.sample_id!r} appears twice in one folder "
                             "(two annotators must be in separate folders)")
        result[annotation.sample_id] = annotation
    return result


def compare(dir_a: Path, dir_b: Path):
    a_side, b_side = _by_sample(load_annotations([dir_a])), _by_sample(load_annotations([dir_b]))
    comparisons = []
    for sample_id in sorted(set(a_side) & set(b_side)):
        a, b = a_side[sample_id], b_side[sample_id]
        matched, only_a, only_b = align(a.claims, b.claims)
        comparisons.append(SampleComparison(
            sample_id, a.overall_verdict, b.overall_verdict,
            a.article.document_id == b.article.document_id, matched, only_a, only_b))
    return comparisons, sorted(set(a_side) - set(b_side)), sorted(set(b_side) - set(a_side))


def format_comparison(comparisons, only_in_a, only_in_b, name_a: str, name_b: str) -> str:
    lines = [f"A = {name_a}    B = {name_b}", f"articles in both: {len(comparisons)}"]
    if only_in_a:
        lines.append(f"only in A (no counterpart): {', '.join(only_in_a)}")
    if only_in_b:
        lines.append(f"only in B (no counterpart): {', '.join(only_in_b)}")

    relations_a, relations_b, gaps_a, gaps_b = [], [], [], []
    for item in comparisons:
        same = "same" if item.verdict_a == item.verdict_b else "DIFFERENT"
        lines.append(f"\n{item.sample_id}   overall: A={item.verdict_a}  B={item.verdict_b}  [{same}]")
        if not item.same_text:
            lines.append("  WARNING: the two files annotate different text versions (document_id differs); "
                         "paragraph ids may not line up")
        for a, b in item.matched:
            relations_a.append(a.relation); relations_b.append(b.relation)
            gaps_a.append(a.gap_type); gaps_b.append(b.gap_type)
            agree = (a.relation, a.gap_type) == (b.relation, b.gap_type)
            lines.append(f"  {'ok  ' if agree else 'DIFF'} A {a.claim_id} {a.relation}/{a.gap_type}  "
                         f"B {b.claim_id} {b.relation}/{b.gap_type}")
            lines.append(f"       A: {a.text[:44]}   {list(a.evidence_ids)}")
            lines.append(f"       B: {b.text[:44]}   {list(b.evidence_ids)}")
            if not agree:
                for who, claim in (("A", a), ("B", b)):
                    if claim.note:
                        lines.append(f"       note {who}: {claim.note[:110]}")
        for a in item.only_a:
            lines.append(f"  A only  {a.claim_id} {a.relation}/{a.gap_type}  {a.text[:44]}")
        for b in item.only_b:
            lines.append(f"  B only  {b.claim_id} {b.relation}/{b.gap_type}  {b.text[:44]}")

    n = len(relations_a)
    lines.append(f"\nmatched claims: {n}   unmatched: "
                 f"{sum(len(c.only_a) + len(c.only_b) for c in comparisons)}")
    if n:
        agree_pair = sum(1 for x, y in zip(zip(relations_a, gaps_a), zip(relations_b, gaps_b)) if x == y)
        lines.append(f"claims with identical relation+gap_type: {agree_pair}/{n}")
        for name, x, y in (("relation", relations_a, relations_b), ("gap_type", gaps_a, gaps_b)):
            kappa = cohen_kappa(x, y)
            lines.append(f"  {name}: agreement {sum(p == q for p, q in zip(x, y))}/{n}, "
                         f"Cohen's kappa {'n/a' if kappa is None else f'{kappa:.2f}'}")
        verdicts = sum(c.verdict_a == c.verdict_b for c in comparisons)
        lines.append(f"articles with the same overall verdict: {verdicts}/{len(comparisons)}")
        if n < SMALL_SAMPLE:
            advice = ("Discuss the DIFF lines rather than trusting the number." if agree_pair < n
                      else "No disagreements, but with this few claims that says little about how clear the guide is.")
            lines.append(f"note: n={n} < {SMALL_SAMPLE}; kappa this small is only a rough signal. {advice}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dir_a", type=Path)
    parser.add_argument("dir_b", type=Path)
    args = parser.parse_args(argv)
    for path in (args.dir_a, args.dir_b):
        if not path.is_dir():
            print(f"not a folder: {path}", file=sys.stderr)
            return 1
    comparisons, only_a, only_b = compare(args.dir_a, args.dir_b)
    print(format_comparison(comparisons, only_a, only_b, str(args.dir_a), str(args.dir_b)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
