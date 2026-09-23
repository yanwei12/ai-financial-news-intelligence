"""
Annotation format for "does the article body support the headline?".

One Annotation file is one annotator's judgement of one article. The human
records two things per headline claim:

  relation   supports | conflicts | insufficient   (the 3-class model task)
  gap_type   none | certainty | scope | contradiction | insufficient

The product-facing verdict is never typed by hand. It is derived from those
two fields (derive_verdict), so an annotator cannot produce a contradictory
pair. The verdict names match src/ai/headline.py, so human labels and the
Gemini prototype can be compared directly. See docs/annotation/guide.md.

Real articles are stored by URL and hashes only; their text lives in a local,
git-ignored cache. Synthetic articles carry their (invented) text inline.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from src.processing.article_text import content_hash
from src.processing.cleaner import PREPARATION_VERSION, PreparedArticle

Relation = Literal["supports", "conflicts", "insufficient"]
GapType = Literal["none", "certainty", "scope", "contradiction", "insufficient"]
Verdict = Literal["supported", "missing_conditions", "contradicted", "insufficient"]

# relation -> the gap types that may accompany it, and the verdict each gives.
_VERDICTS: dict[tuple[str, str], Verdict] = {
    ("supports", "none"): "supported",
    ("supports", "certainty"): "missing_conditions",
    ("supports", "scope"): "missing_conditions",
    ("conflicts", "contradiction"): "contradicted",
    ("insufficient", "insufficient"): "insufficient",
}
# When one article has several claims, the most serious verdict wins
# (same order as src/ai/headline.py).
_SEVERITY: tuple[Verdict, ...] = ("contradicted", "missing_conditions", "insufficient", "supported")

_SHA256 = r"^[a-f0-9]{64}$"
_PARAGRAPH_ID = re.compile(r"^P\d{3,}$")


def derive_verdict(relation: str, gap_type: str) -> Verdict:
    """Product verdict for a (relation, gap_type) pair; ValueError if the pair is invalid."""
    try:
        return _VERDICTS[(relation, gap_type)]
    except KeyError:
        allowed = sorted(g for r, g in _VERDICTS if r == relation)
        raise ValueError(
            f"relation={relation!r} cannot be combined with gap_type={gap_type!r}"
            + (f" (allowed: {', '.join(allowed)})" if allowed else "")
        ) from None


class ArticleRef(BaseModel):
    """Which exact text was annotated."""

    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=2000)
    # cleaner.prepare_article(...).document_id; changes if title, body or mode change.
    document_id: str = Field(pattern=_SHA256)
    paragraph_mode: Literal["blank_lines", "line_breaks"] = "blank_lines"
    preparation_version: str = PREPARATION_VERSION
    url: str | None = Field(default=None, max_length=2048)
    # article_text.content_hash of the body; required for real articles.
    content_hash: str | None = Field(default=None, pattern=_SHA256)
    # Only for synthetic articles. Real article text is never stored in git.
    text: str | None = None


class AnnotatedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_id: str = Field(pattern=r"^C\d+$")
    # The checkable claim, in the annotator's words. Not a copy of the whole headline.
    text: str = Field(min_length=1, max_length=500)
    relation: Relation
    gap_type: GapType
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    note: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _check(self) -> "AnnotatedClaim":
        derive_verdict(self.relation, self.gap_type)
        for evidence_id in self.evidence_ids:
            if not _PARAGRAPH_ID.match(evidence_id):
                raise ValueError(f"evidence id {evidence_id!r} must look like P001")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("evidence_ids contains duplicates")
        if self.relation != "insufficient" and not self.evidence_ids:
            raise ValueError(f"relation={self.relation!r} needs at least one evidence id")
        return self

    @property
    def verdict(self) -> Verdict:
        return derive_verdict(self.relation, self.gap_type)


class Annotation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,80}$")
    # Articles about the same event or reprints share a group; a group must
    # stay entirely in train, validation or test.
    group_id: str = Field(pattern=r"^[A-Za-z0-9._-]{1,80}$")
    annotator: str = Field(min_length=1, max_length=40)
    is_synthetic: bool
    article: ArticleRef
    claims: list[AnnotatedClaim] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def _check(self) -> "Annotation":
        ref = self.article
        if self.is_synthetic:
            if not ref.text:
                raise ValueError("a synthetic sample must include article.text")
        else:
            if ref.text is not None:
                raise ValueError("a real article must not embed its text (keep url + content_hash only)")
            if not ref.url or not ref.content_hash:
                raise ValueError("a real article needs article.url and article.content_hash")
        ids = [claim.claim_id for claim in self.claims]
        if len(set(ids)) != len(ids):
            raise ValueError("claim_id values must be unique within a sample")
        return self

    @property
    def overall_verdict(self) -> Verdict:
        verdicts = {claim.verdict for claim in self.claims}
        return next(v for v in _SEVERITY if v in verdicts)


def check_against_article(annotation: Annotation, prepared: PreparedArticle) -> list[str]:
    """
    Problems found when the annotation is compared with the text it refers to.
    An empty list means every reference resolves. That proves the citations
    exist, not that the human judgement is right.
    """
    problems: list[str] = []
    ref = annotation.article
    if prepared.document_id != ref.document_id:
        problems.append(
            "document_id does not match the article text (title, body, paragraph mode "
            "or preparation version changed since it was annotated)"
        )
    if ref.content_hash and content_hash(prepared.original_text) != ref.content_hash:
        problems.append("content_hash does not match the article body")
    known = {paragraph.id for paragraph in prepared.paragraphs}
    for claim in annotation.claims:
        missing = [i for i in claim.evidence_ids if i not in known]
        if missing:
            problems.append(f"claim {claim.claim_id}: unknown paragraph id(s) {', '.join(missing)}")
    return problems
