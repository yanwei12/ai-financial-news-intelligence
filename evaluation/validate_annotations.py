"""
Check annotation files.

    python -m evaluation.validate_annotations evaluation/annotations
    python -m evaluation.validate_annotations some_file.json --cache-dir path/to/cache

Offline by design: real article text is read from a local cache
(<cache-dir>/<content_hash>.json, keys "title" and "body"), never fetched here.
Exit code is 1 when any file has a problem, so this can run in CI.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from evaluation.annotation_schema import Annotation, check_against_article
from src.processing.cleaner import PreparedArticle, prepare_article

DEFAULT_CACHE_DIR = Path(__file__).resolve().parent / "cache"


def load_article_text(annotation: Annotation, cache_dir: Path) -> str:
    """Body text for this annotation: inline for synthetic samples, cached otherwise."""
    if annotation.is_synthetic:
        return annotation.article.text or ""
    cached = cache_dir / f"{annotation.article.content_hash}.json"
    if not cached.is_file():
        raise FileNotFoundError(
            f"article text not cached: {cached.name} (fetch and cache it before validating)"
        )
    return json.loads(cached.read_text(encoding="utf-8"))["body"]


def validate_file(path: Path, cache_dir: Path) -> tuple[Annotation | None, list[str]]:
    try:
        annotation = Annotation.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError as error:  # pydantic's ValidationError is a ValueError: bad JSON or bad schema
        return None, [_short(error)]
    try:
        body = load_article_text(annotation, cache_dir)
    except (OSError, KeyError, json.JSONDecodeError) as error:
        return annotation, [str(error)]
    ref = annotation.article
    prepared: PreparedArticle = prepare_article(ref.title, body, ref.paragraph_mode)
    return annotation, check_against_article(annotation, prepared)


def _short(error: Exception) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in item['loc']) or 'file'}: {item['msg']}"
            for item in error.errors()
        )
    return f"invalid JSON: {error}"


def find_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        files.extend(sorted(path.rglob("*.json")) if path.is_dir() else [path])
    return files


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="+", type=Path)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR)
    args = parser.parse_args(argv)

    missing = [path for path in args.paths if not path.exists()]
    if missing:
        for path in missing:
            print(f"path not found: {path}", file=sys.stderr)
        return 1
    files = find_files(args.paths)
    if not files:
        print("no .json files found", file=sys.stderr)
        return 1

    failed = 0
    verdicts = {True: Counter(), False: Counter()}  # keyed by is_synthetic
    for path in files:
        annotation, problems = validate_file(path, args.cache_dir)
        if problems:
            failed += 1
            print(f"FAIL {path}")
            for problem in problems:
                print(f"     - {problem}")
        else:
            print(f"ok   {path}")
        if annotation is not None and not problems:
            verdicts[annotation.is_synthetic][annotation.overall_verdict] += 1

    print(f"\n{len(files) - failed} ok, {failed} failed")
    # Real and synthetic samples are always reported apart.
    for synthetic, label in ((False, "real"), (True, "synthetic")):
        if verdicts[synthetic]:
            counts = ", ".join(f"{v}={n}" for v, n in sorted(verdicts[synthetic].items()))
            print(f"{label}: {counts}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
