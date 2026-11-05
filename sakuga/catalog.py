"""Catalogue rules, pure so they can be tested: what to keep, and which cel a listing belongs to."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .filters import FRANCHISES
from .images import distance

# pHash Hamming distances, measured on real listing photos (2026-11-05): a relist's re-upload, half
# size or JPEG q50 lands at 0-4; a 5% crop, padding or brightening at 4-30; two different cels of the
# same franchise were never closer than 14. At or under NEAR it is the same cel; up to MAYBE the LLM
# compares the photos. A re-photographed cel is not close in pHash at all, so a near-identical title
# also sends the pair to the LLM.
NEAR = 8
MAYBE = 20
TITLE_SIM = 0.6
MAX_ASKS = 4


@dataclass
class Keep:
    ok: bool
    kind: str
    gainax: bool
    reason: str = ""


def keep(franchise: str, regex_kind: str, verdict: dict | None) -> Keep:
    """Combine the regex first pass (which already passed) with the LLM's reading of the photo.

    The regex keeps the franchise (it reads the Japanese title, which is reliable); the LLM may
    only narrow: a print, a non-GAINAX reproduction, or drawings where the franchise wants cels only.
    """
    gainax = regex_kind == "gainax-limited"
    if not verdict:
        return Keep(True, "cel" if gainax else regex_kind, gainax)
    kind = verdict.get("kind", "")
    gainax = gainax or bool(verdict.get("gainax_limited")) and franchise == "eva"
    if kind in ("print", "not_art"):
        return Keep(False, kind, False, f"LLM: {kind.replace('_', ' ')}")
    if kind == "reproduction" and not gainax:
        return Keep(False, kind, False, "LLM: reproduction")
    if kind in ("genga", "douga") and not FRANCHISES[franchise]["genga"]:
        return Keep(False, kind, False, f"LLM: {kind}, cels only for this franchise")
    if gainax:
        return Keep(True, "cel", True)
    return Keep(True, "cel" if kind == "reproduction" else kind, False)


@dataclass
class Candidate:
    cel_id: int
    phash: str
    franchise: str
    title: str = ""


def _bigrams(t: str) -> set[str]:
    t = re.sub(r"[\s\u3000【】「」()（）・,，、。!！?？★☆♪/／|]+", "", t.lower())
    return {t[i : i + 2] for i in range(len(t) - 1)}


def title_sim(a: str, b: str) -> float:
    """Jaccard similarity of character bigrams: 1.0 for the same title, about 0.2 for two cel listings."""
    x, y = _bigrams(a), _bigrams(b)
    return len(x & y) / len(x | y) if x and y else 0.0


def group(
    phash: str,
    franchise: str,
    candidates: list[Candidate],
    same: Callable[[int], bool | None] | None = None,
    title: str = "",
) -> int | None:
    """The cel this listing shows, or None for a new cel.

    same(cel_id) asks the LLM about a borderline pair and may return None (no LLM, or no answer),
    which counts as different: a duplicate on the Catalogue page is cheaper than a lost new cel.
    """
    pool = [c for c in candidates if c.franchise == franchise]
    scored = sorted((distance(phash, c.phash) if phash else 64, c.cel_id, c.title) for c in pool)
    if scored and scored[0][0] <= NEAR:
        return scored[0][1]
    asks = [cel_id for d, cel_id, _ in scored if NEAR < d <= MAYBE]
    if title:
        asks += [
            cel_id for d, cel_id, t in scored if d > MAYBE and title_sim(title, t) >= TITLE_SIM
        ]
    for cel_id in asks[:MAX_ASKS]:
        if same and same(cel_id):
            return cel_id
    return None
