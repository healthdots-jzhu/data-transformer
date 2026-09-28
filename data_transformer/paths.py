"""Key paths: splitting 'a > b', 'a.b' into segments and flattening nested objects."""
from __future__ import annotations

import re
from typing import Any, Dict, List

PATH_SEP = " > "   # canonical display separator; input may also use dotted paths ("patient.name")
# A period followed by whitespace or end-of-key is punctuation ("Dr. Name", "Unique No."); a period
# directly followed by a word character starts a new path segment.
PATH_SPLIT = re.compile(r"\s*>\s*|\.(?=\w)")


def split_path(key: str) -> List[str]:
    """Split a key into non-empty, stripped path segments."""
    return [s.strip() for s in PATH_SPLIT.split(key) if s.strip()]


def flatten(nested: Dict[str, Any], parent_path: str = "") -> Dict[str, Any]:
    """Flatten nested dicts into {"patient > name": value}. Lists are kept intact (they are line items).
    Raises ValueError if two different spellings collapse to the same path (e.g. literal
    'patient.name' next to {'patient': {'name': ...}}) instead of silently overwriting one."""
    flat: Dict[str, Any] = {}
    for key, value in nested.items():
        if not isinstance(key, str):
            raise ValueError("object field names must be strings")
        path = PATH_SEP.join(p for p in (parent_path, PATH_SEP.join(split_path(key))) if p)
        additions = flatten(value, path) if isinstance(value, dict) else {path: value}
        if flat.keys() & additions.keys():
            raise ValueError(f"conflicting flattened paths under '{path}'")
        flat.update(additions)
    return flat
