"""The LLM's two jobs: read a listing (title plus photo) and say whether two photos are the same cel.

Calls the cluster's LiteLLM proxy (OpenAI-compatible chat completions) with the `claude` model. Each
answer is cached by the caller (the listing's verdict column, the `same` table), so a listing costs
one call in its life. With no endpoint configured both functions return None and the regex filter
alone decides.
"""

from __future__ import annotations

import base64
import json
import logging
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

CLASSIFY = """You catalogue Japanese anime production art for a collector. Read this marketplace listing
(Japanese title, an optional machine translation, and the main photo) and answer in JSON only:

{"kind": "cel" | "genga" | "douga" | "reproduction" | "print" | "not_art",
 "gainax_limited": true|false,
 "franchise": "broly" | "fma" | "eva" | "pokemon" | "other",
 "characters": ["English names"],
 "episode_cut": "e.g. ep 24 C-298, or empty",
 "background": true|false,
 "title_en": "a short English title for a catalogue card, under 70 characters",
 "notes": "one sentence on anything a collector would check (douga included, sketch, damage)"}

Definitions:
- cel: a hand-painted production cel used in the anime (セル画), with or without background/douga.
- genga / douga: pencil key drawings / in-between drawings on paper.
- reproduction: a 複製セル or replica, including official limited reproductions.
- gainax_limited: an official GAINAX numbered limited reproduction cel (e.g. 限定100部, No.04/100).
- print: printed cels, Cel Club (セル画倶楽部), prize cels, cards, posters, art books.
- not_art: figures, books, goods, anything else.
- broly is Dragon Ball's Broly (ブロリー) only; other Dragon Ball characters are "other".
Judge from the photo as well as the words: a glossy printed card or a book page is not a cel.

Title: {title}
Translation: {extra}
Price: {price}"""

SAME = """These are the main photos of two Japanese marketplace listings of anime production cels.
Are they the same physical cel (the same artwork, perhaps photographed again, cropped or with a
different background sheet)? Different cuts of the same scene, or the same pose on a different cel,
are NOT the same. Answer in JSON only: {"same": true|false}"""


@dataclass
class Usage:
    calls: int = 0
    cost: float = 0.0


def _image_part(path: Path) -> dict:
    data = base64.b64encode(path.read_bytes()).decode()
    return {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{data}"}}


def _parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        v = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return v if isinstance(v, dict) else None


class LLM:
    def __init__(self, url: str, key: str, model: str):
        self.url, self.key, self.model = url, key, model
        self.usage = Usage()

    @property
    def on(self) -> bool:
        return bool(self.url)

    def _ask(self, content: list[dict], max_tokens: int = 400) -> dict | None:
        body = json.dumps(
            {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": content}],
            }
        ).encode()
        req = urllib.request.Request(
            f"{self.url}/chat/completions",
            data=body,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.key}"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:  # nosec B310: configured endpoint
                cost = r.headers.get("x-litellm-response-cost", "")
                out = json.loads(r.read())
        except Exception as e:  # pylint: disable=broad-exception-caught
            log.warning("llm call failed: %s", type(e).__name__)
            return None
        self.usage.calls += 1
        try:
            self.usage.cost += float(cost or 0)
        except ValueError:
            pass
        try:
            text = out["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            return None
        return _parse(text)

    def classify(self, title: str, extra: str, price: str, image: Path | None) -> dict | None:
        if not self.on:
            return None
        prompt = CLASSIFY.replace("{title}", title[:400]).replace("{extra}", extra[:400])
        prompt = prompt.replace("{price}", price)
        content: list[dict] = [{"type": "text", "text": prompt}]
        if image and image.exists():
            content.append(_image_part(image))
        v = self._ask(content)
        if not v or v.get("kind") not in {
            "cel",
            "genga",
            "douga",
            "reproduction",
            "print",
            "not_art",
        }:
            return None
        return v

    def same(self, a: Path, b: Path) -> bool | None:
        if not self.on or not a.exists() or not b.exists():
            return None
        v = self._ask([{"type": "text", "text": SAME}, _image_part(a), _image_part(b)], 50)
        return bool(v["same"]) if v and "same" in v else None
