from __future__ import annotations

import re
from typing import Optional


ZIP_RE = re.compile(r"\b(\d{5})\b")


def derive_neighborhood_label(address: str | None) -> str:
    """Return a coarse neighborhood label derived from a free-form address.

    This is intentionally simple and used only for aggregated analytics:
    - Prefer a 5-digit sequence that looks like a postal/ZIP code.
    - Otherwise, if there is a comma, use the text after the last comma.
    - Fallback to "unspecified" when nothing reasonable can be inferred.
    """
    if not address:
        return "unspecified"
    text = str(address).strip()
    if not text:
        return "unspecified"

    m = ZIP_RE.search(text)
    if m:
        return m.group(1)

    if "," in text:
        tail = text.split(",")[-1].strip()
        if tail:
            return tail

    return "unspecified"

