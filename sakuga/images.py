"""Listing photos: download once, keep a 640px copy on the volume, and a perceptual hash for grouping.

Marketplace image URLs expire with the listing and some refuse hotlinking, so the pages serve the
local copy. The pHash (DCT of a 32x32 greyscale, top-left 8x8 against its median) survives the
resizing, recompression and small crops a relist goes through; a different cel of the same character
lands far away (measured on real relists in tests/test_catalog.py).
"""

from __future__ import annotations

import hashlib
import io
import logging
import urllib.request
from pathlib import Path

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0 Safari/537.36"
)
MAX_SIDE = 640


def _dct_matrix(n: int) -> np.ndarray:
    k = np.arange(n)
    m = np.cos(np.pi * (2 * k[None, :] + 1) * k[:, None] / (2 * n))
    m[0] *= 1 / np.sqrt(2)
    return m * np.sqrt(2 / n)


_D32 = _dct_matrix(32)


def phash(img: Image.Image) -> str:
    """64-bit perceptual hash as 16 hex characters."""
    g = np.asarray(img.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float64)
    low = (_D32 @ g @ _D32.T)[:8, :8].flatten()
    bits = low[1:] > np.median(low[1:])  # the DC term carries brightness, not structure
    bits = np.concatenate([[low[0] > 0], bits])
    return f"{int(''.join('1' if b else '0' for b in bits), 2):016x}"


def distance(a: str, b: str) -> int:
    """Hamming distance between two hashes; 64 when either is missing."""
    if not a or not b:
        return 64
    return bin(int(a, 16) ^ int(b, 16)).count("1")


def fetch(url: str, referer: str = "") -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": referer or url})
    with urllib.request.urlopen(req, timeout=30) as r:  # nosec B310: marketplace image URLs only
        return r.read(8_000_000)


def store(raw: bytes, img_dir: Path) -> tuple[str, str]:
    """Save a JPEG copy (longest side 640px); returns (file name, phash)."""
    img = Image.open(io.BytesIO(raw))
    img.load()
    img = img.convert("RGB")
    h = phash(img)
    img.thumbnail((MAX_SIDE, MAX_SIDE))
    name = hashlib.sha256(raw).hexdigest()[:24] + ".jpg"
    path = img_dir / name
    if not path.exists():
        tmp = path.with_suffix(".tmp")
        img.save(tmp, "JPEG", quality=85)
        tmp.replace(path)
    return name, h


def download(url: str, img_dir: Path, referer: str = "", quiet: bool = False) -> tuple[str, str]:
    """(file, phash), or ('', '') when the photo cannot be fetched: the listing is kept either way.
    quiet for guessed URLs (Mercari's photo 3), where a missing photo is expected."""
    if not url:
        return "", ""
    try:
        return store(fetch(url, referer), img_dir)
    except Exception as e:  # pylint: disable=broad-exception-caught
        (log.debug if quiet else log.warning)("image %s: %s", url[:120], type(e).__name__)
        return "", ""
