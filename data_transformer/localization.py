"""Language-tagged word lists. A "Localized" list is a dict {language: [words]}; the pseudo-language "*"
means 'always active'. Everything language dependent in a schema (aliases, stop-words, owner words,
type values, ...) is stored this way and resolved against the ACTIVE languages of a run."""
from __future__ import annotations

from typing import Dict, Iterable, List, Tuple

from .normalization import fold_text

WILDCARD = "*"
Localized = Dict[str, List[str]]


def active_items(localized: Localized, languages: Iterable[str]) -> List[Tuple[str, str]]:
    """Flatten to [(word, language)] for the active languages (+ "*"), preserving order and removing
    duplicates (compared accent- and case-insensitively). A word present in several languages
    ('telephone' in en and fr) gets the language "*": it says nothing about the language."""
    active = set(languages)
    seen: Dict[str, list] = {}
    for lang, words in localized.items():
        if lang != WILDCARD and lang not in active:
            continue
        for word in words:
            key = fold_text(word)
            if key in seen:
                seen[key][1].add(lang)
            else:
                seen[key] = [word, {lang}]
    return [(word, next(iter(langs)) if len(langs) == 1 else WILDCARD) for word, langs in seen.values()]


def active_words(localized: Localized, languages: Iterable[str]) -> List[str]:
    """Like active_items but only the words."""
    return [word for word, _ in active_items(localized, languages)]


def all_words(localized: Localized) -> List[str]:
    """Every word in every language (used where the language is not yet known, e.g. XML hints)."""
    return [word for words in localized.values() for word in words]


def has_entries(localized: Localized) -> bool:
    """True if any language has at least one word."""
    return any(localized.values())
