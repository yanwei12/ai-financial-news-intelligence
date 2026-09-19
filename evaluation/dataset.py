"""Turn annotation files into (headline claim, evidence) pairs for evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evaluation.annotation_schema import Annotation, check_against_article
from evaluation.validate_annotations import DEFAULT_CACHE_DIR, find_files, load_article_text
from src.processing.cleaner import prepare_article


@dataclass(frozen=True)
class Pair:
    """One claim with the exact evidence paragraphs the annotator cited."""

    sample_id: str
    claim_id: str
    group_id: str
    annotator: str
    is_synthetic: bool
    title: str
    claim_text: str
    evidence_ids: tuple[str, ...]
    evidence_texts: tuple[str, ...]
    relation: str
    gap_type: str
    verdict: str


def load_annotations(paths: list[Path]) -> list[Annotation]:
    annotations = []
    for path in find_files(paths):
        try:
            annotations.append(Annotation.model_validate_json(path.read_text(encoding="utf-8")))
        except ValueError as error:
            raise ValueError(f"{path}: {error}") from error
    return annotations


def is_ai_draft(annotation: Annotation) -> bool:
    name = annotation.annotator.lower()
    return "claude" in name or "draft" in name


def build_pairs(annotations: list[Annotation], cache_dir: Path = DEFAULT_CACHE_DIR) -> list[Pair]:
    pairs: list[Pair] = []
    for annotation in annotations:
        ref = annotation.article
        prepared = prepare_article(ref.title, load_article_text(annotation, cache_dir), ref.paragraph_mode)
        problems = check_against_article(annotation, prepared)
        if problems:
            raise ValueError(f"{annotation.sample_id}: " + "; ".join(problems))
        text_of = {paragraph.id: paragraph.text for paragraph in prepared.paragraphs}
        for claim in annotation.claims:
            pairs.append(Pair(
                sample_id=annotation.sample_id, claim_id=claim.claim_id, group_id=annotation.group_id,
                annotator=annotation.annotator, is_synthetic=annotation.is_synthetic,
                title=ref.title, claim_text=claim.text,
                evidence_ids=tuple(claim.evidence_ids),
                evidence_texts=tuple(text_of[i] for i in claim.evidence_ids),
                relation=claim.relation, gap_type=claim.gap_type, verdict=claim.verdict,
            ))
    return pairs
