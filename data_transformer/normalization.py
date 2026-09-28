"""Turning raw source keys into comparable text: accent folding, spelling/punctuation normalisation,
context-aware abbreviation expansion and a similarity measure. Pure functions, no dependency on the
rest of the package."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import FrozenSet, List, Optional, Tuple


def fold_accents(text: str) -> str:
    """Remove accents so French and English spellings compare equal: 'Téléphone' -> 'Telephone',
    'Œuvre' -> 'Oeuvre'. Case is preserved."""
    text = text.replace("œ", "oe").replace("Œ", "OE").replace("æ", "ae").replace("Æ", "AE")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if unicodedata.category(c) != "Mn")


def fold_text(text: str) -> str:
    """fold_accents + lower-case: the canonical form for comparing words."""
    return fold_accents(text).lower()


_VARIANT_SPLIT = re.compile(r"\s*[/|]\s*|\s+[-\u2013\u2014]\s+")


def text_variants(text: str, include_whole: bool = False) -> List[str]:
    """Split a BILINGUAL label into its parts: 'Full name / Nom complet' -> ['Full name', 'Nom complet'];
    'Dental - Dentaire' and 'Dental|Dentaire' likewise. Separators: '/', '|', and ' - ' / en-dash / em-dash
    surrounded by spaces. Text without a separator returns [text]. include_whole=True also returns the
    unsplit text first (used for VALUES, where 'Dental Claim - Basic' may be a single phrase)."""
    parts = [p.strip() for p in _VARIANT_SPLIT.split(text) if p and re.search(r"\w", p)]
    if len(parts) <= 1:
        return [text]
    return ([text] + parts) if include_whole else parts


@dataclass(frozen=True)
class AbbreviationRule:
    """One user-defined abbreviation. `confidence` is a curated weight (0..1), not a probability.
    Without context the rule applies anywhere ("qty" -> quantity); with context it applies only
    next to the listed words ("pt" -> patient only before name/phone/...; EITHER side may match)."""
    abbreviation: str
    expansion: str
    confidence: float
    required_previous_tokens: FrozenSet[str] = frozenset()
    required_next_tokens: FrozenSet[str] = frozenset()

    @property
    def needs_context(self) -> bool:
        return bool(self.required_previous_tokens or self.required_next_tokens)


@dataclass(frozen=True)
class AbbreviationExpansion:
    """Audit record: which abbreviation was expanded, to what, with which confidence."""
    abbreviation: str
    expansion: str
    confidence: float


@dataclass(frozen=True)
class NormalizedKey:
    """A key after normalisation, e.g. "Svc_Dt" -> text "service date", tokens ("service","date")."""
    text: str
    tokens: Tuple[str, ...]
    expansions: Tuple[AbbreviationExpansion, ...]


def _context_ok(rule: AbbreviationRule, previous: Optional[str], following: Optional[str]) -> bool:
    """True if the rule needs no context, or a neighbouring word satisfies it."""
    return (not rule.needs_context or previous in rule.required_previous_tokens
            or following in rule.required_next_tokens)


@lru_cache(maxsize=16384)
def normalize_key(raw_key: str, rules: Tuple[AbbreviationRule, ...],
                  stopwords: Tuple[str, ...]) -> NormalizedKey:
    """Normalise a key: fold accents, '#' -> 'number', split camelCase, unify co-pay/co-insurance, drop
    punctuation (apostrophes included: "l'assuré" -> "l", "assure"), lower-case, remove stop-words, then
    expand abbreviations (highest-confidence applicable rule wins) while recording every expansion.
    `rules` and `stopwords` must already be accent-folded and lower-case (Context does this)."""
    text = fold_accents(raw_key).replace("#", " number ")
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = re.sub(r"(?i)\bco[\s_-]+pay\b", "copay", text)
    text = re.sub(r"(?i)\bco[\s_-]+insurance\b", "coinsurance", text)
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).lower()
    original = [t for t in text.split() if t not in stopwords]
    tokens: List[str] = []
    expansions: List[AbbreviationExpansion] = []
    for i, token in enumerate(original):
        previous = original[i - 1] if i else None
        following = original[i + 1] if i + 1 < len(original) else None
        matching = [r for r in rules if r.abbreviation == token and _context_ok(r, previous, following)]
        if matching:
            rule = max(matching, key=lambda r: r.confidence)
            expansions.append(AbbreviationExpansion(token, rule.expansion, rule.confidence))
            tokens.extend(rule.expansion.split())
        else:
            tokens.append(token)
    return NormalizedKey(" ".join(tokens), tuple(tokens), tuple(expansions))


def text_similarity(left: str, right: str) -> float:
    """0..1 similarity of two normalised texts: the better of word-set overlap (makes
    'service date' == 'date service') and character similarity (tolerates typos)."""
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_words, right_words = set(left.split()), set(right.split())
    overlap = len(left_words & right_words) / len(left_words | right_words)
    ratio = SequenceMatcher(None, left.replace(" ", ""), right.replace(" ", "")).ratio()
    return max(overlap, ratio)


_PLURALS = {"amounts": "amount", "costs": "cost", "fees": "fee", "charges": "charge",
            "prices": "price", "dates": "date", "codes": "code", "items": "item",
            "montants": "montant", "couts": "cout", "tarifs": "tarif", "articles": "article"}


def fold_plurals(text: str) -> str:
    """Fold only explicitly supported generic plurals ('amounts' -> 'amount', 'montants' -> 'montant');
    never guess by deleting a trailing 's'."""
    return " ".join(_PLURALS.get(t, t) for t in text.split())
