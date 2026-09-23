"""
Rule baseline (rules-v0): keyword cues for certainty, scope and negation.

This is the simple, explainable method the plan (§8) asks every other method
to beat. The cue lists come straight from docs/annotation/guide.md §3 and were
fixed BEFORE any labels existed. Do not add words after looking at results on
the annotated articles; that would tune the baseline on its own test data. A
new list is a new version (bump VERSION) that gets evaluated from scratch.

Input is one Pair (title, claim, gold evidence). Output is (relation, gap_type).
"""

from __future__ import annotations

import hashlib
import json
import re

from evaluation.dataset import Pair

VERSION = "rules-v0"

# guide §3, "確定性": headline words vs body words that mean "not settled".
# The single character 已 from the guide is left out: it is far too common to be a cue.
CERTAINTY_TITLE = ("確定", "正式", "拍板", "宣布")
HEDGES = ("評估", "考慮", "研議", "傳出", "預計", "可能", "規劃", "尚未", "視情況")
# guide §3, "範圍"
SCOPE_TITLE = ("全面", "所有", "全球", "各地", "全部", "一律")
LIMITS = ("部分", "特定", "僅", "只適用", "限於", "某些")
# guide §3, "矛盾": a body sentence that negates something the claim talks about.
NEGATIONS = ("不會", "沒有", "並未", "否認", "未曾", "無意", "不再")

# Untuned, chosen up front: below this share of the claim's character pairs found
# in the evidence, the evidence is treated as unrelated.
MIN_OVERLAP = 0.25


def cue_fingerprint() -> str:
    """Hash of every cue list and threshold. A test pins it to VERSION so the lists cannot drift silently."""
    payload = json.dumps([CERTAINTY_TITLE, HEDGES, SCOPE_TITLE, LIMITS, NEGATIONS, MIN_OVERLAP],
                         ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _bigrams(text: str) -> set[str]:
    text = re.sub(r"\s+", "", text)
    return {text[i:i + 2] for i in range(len(text) - 1)}


def overlap(claim: str, evidence: str) -> float:
    claim_pairs = _bigrams(claim)
    return len(claim_pairs & _bigrams(evidence)) / len(claim_pairs) if claim_pairs else 0.0


def _negated(claim: str, evidence_texts: tuple[str, ...]) -> bool:
    for text in evidence_texts:
        for sentence in re.split(r"[。！？!?]", text):
            if any(word in sentence for word in NEGATIONS) and overlap(claim, sentence) >= MIN_OVERLAP:
                return True
    return False


def predict(pair: Pair) -> tuple[str, str]:
    evidence = "\n".join(pair.evidence_texts)
    if not pair.evidence_texts or overlap(pair.claim_text, evidence) < MIN_OVERLAP:
        return "insufficient", "insufficient"
    if _negated(pair.claim_text, pair.evidence_texts):
        return "conflicts", "contradiction"
    if any(w in pair.title for w in CERTAINTY_TITLE) and any(w in evidence for w in HEDGES):
        return "supports", "certainty"
    if any(w in pair.title for w in SCOPE_TITLE) and any(w in evidence for w in LIMITS):
        return "supports", "scope"
    return "supports", "none"
