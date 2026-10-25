"""Turn the place names the note reader returns into one canonical slug, in code rather than in the model.

"North field depot", "Northfield sort centre" and "NFD hub" all become "northfield", so incident grouping
and rules on location work no matter how the notes (or the model) spelled it.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

ALIASES_FILE = Path(__file__).resolve().parent.parent / "rules" / "place_aliases.yaml"
_GENERIC = {"depot", "hub", "centre", "center", "sort", "sorting", "dc", "distribution", "facility",
            "warehouse", "station", "terminal"}


def _slug(text: str) -> str:
    words = [w for w in re.split(r"[\s\-_/.,]+", text.lower()) if w and w not in _GENERIC]
    return re.sub(r"[^\w]", "", "".join(words))


@lru_cache(maxsize=1)
def _aliases() -> dict[str, str]:
    data = yaml.safe_load(ALIASES_FILE.read_text(encoding="utf-8")) if ALIASES_FILE.exists() else {}
    return {_slug(str(k)): _slug(str(v)) for k, v in (data or {}).items()}


def normalise_place(raw: str | None) -> str | None:
    if raw is None:
        return None
    slug = _slug(raw)
    if not slug:
        return None
    return _aliases().get(slug, slug)
