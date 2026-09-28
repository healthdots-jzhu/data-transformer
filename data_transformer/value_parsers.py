"""Tolerant parsing of dates, amounts and numbers in English and French. Parsers return None / an empty
NumberParse when the text is not a real date/number; they never guess beyond what is documented.

NUMBER FORMATS depend on a LOCALE chosen per document (see Context.number_locale):
  "en"     1,234.56   comma = thousands separator, dot = decimal mark, no spaces.
  "fr"     1 234,56   decimal COMMA is checked first; a comma is never a thousands separator.
                      Space / NBSP / narrow-NBSP group thousands; a dot is accepted as decimal mark
                      ("12.5") and, with a comma decimal, as a grouping dot ("1.234,56").
  "mixed"  both languages occur in the document: an amount that is valid in only one locale is read
           in that locale; one that means different values in the two (e.g. "1,234" = 1234 or 1.234)
           is AMBIGUOUS and handled by the ambiguous_policy ("error" | "english" | "french")."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from functools import lru_cache
from typing import Any, Dict, FrozenSet, Iterable, List, NamedTuple, Optional, Set, Tuple

from .normalization import fold_accents

# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------
# Month names per language, WITHOUT accents (input is accent-folded first). Position = month number.
_MONTH_NAMES = {
    "en": [("january", "jan"), ("february", "feb"), ("march", "mar"), ("april", "apr"), ("may",),
           ("june", "jun"), ("july", "jul"), ("august", "aug"), ("september", "sept", "sep"),
           ("october", "oct"), ("november", "nov"), ("december", "dec")],
    "fr": [("janvier", "janv"), ("fevrier", "fevr", "fev"), ("mars",), ("avril", "avr"), ("mai",),
           ("juin",), ("juillet", "juil"), ("aout",), ("septembre", "sept", "sep"), ("octobre", "oct"),
           ("novembre", "nov"), ("decembre", "dec")],
}
# Weekday words ignored in dates. ("mar" is NOT listed for French: it is English March.)
_WEEKDAYS = {
    "en": {"monday", "mon", "tuesday", "tue", "tues", "wednesday", "wed", "thursday", "thu", "thur",
           "thurs", "friday", "fri", "saturday", "sat", "sunday", "sun"},
    "fr": {"lundi", "lun", "mardi", "mercredi", "mer", "jeudi", "jeu", "vendredi", "ven", "samedi",
           "sam", "dimanche", "dim"},
}
_FILLER = {"en": {"of", "the"}, "fr": {"le", "la", "du", "de", "d", "l", "au", "a"}}

# Time of day: hours 0-23 (the alternation covers 20-23), minutes 00-59.
_HOUR = r"(?:[01]?\d|2[0-3])"
_MINUTE = r"[0-5]\d"
_TIME_EN = re.compile(
    rf"(?i)(?:t|\s){_HOUR}:{_MINUTE}(?::{_MINUTE}(?:\.\d+)?)?"
    r"(?:\s*(?:z|utc|gmt|est|edt|[+-]\d{2}:?\d{2}))?$")
# French: "à 20 h", "a 21h30", "à 23 h 59", "14 h 05." (input is accent-folded: "à" -> "a")
_TIME_FR = re.compile(rf"(?i)\s+(?:a\s+)?{_HOUR}\s*h\s*(?:{_MINUTE})?[\s.]*$")


@lru_cache(maxsize=None)
def _tables(languages: FrozenSet[str]) -> Tuple[Dict[str, int], Set[str]]:
    """(month-name -> number, ignorable words) for the given languages (English if none is known)."""
    known = [l for l in sorted(languages) if l in _MONTH_NAMES] or ["en"]
    months: Dict[str, int] = {}
    ignored: Set[str] = set()
    for lang in known:
        for number, names in enumerate(_MONTH_NAMES[lang], start=1):
            for name in names:
                months[name] = number
        ignored |= _WEEKDAYS[lang] | _FILLER[lang]
    return months, ignored


@dataclass(frozen=True)
class ParsedDate:
    """A parsed date plus the assumptions made (reported as warnings)."""
    value: date
    notes: Tuple[str, ...] = ()


def _split_textual_month_date(tokens: List[str], pos: int, months: Dict[str, int]):
    """['may','1','2026'] / ['1','mai','2026'] / ['2026','may','1'] -> (year_text, month, day_text)."""
    month = months[tokens[pos]]
    others = [t for i, t in enumerate(tokens) if i != pos]
    if not all(t.isdigit() for t in others):
        return None
    first, second = others
    if pos == 0:
        return second, month, first
    if pos == 1:
        return (first, month, second) if len(first) == 4 else (second, month, first)
    return (first, month, second) if len(first) == 4 else None


def _split_numeric_date(tokens: List[str], day_first: bool, notes: List[str], original: str):
    """Numeric triples such as 2024 05 14 or 14 05 2024. Genuinely ambiguous ones (both parts <= 12
    and different) are read according to `day_first` and a note is recorded."""
    if len(tokens[0]) == 4:
        return tokens[0], int(tokens[1]), tokens[2]
    if len(tokens[2]) not in (2, 4):
        return None
    first, second = int(tokens[0]), int(tokens[1])
    if first > 12 and second <= 12:
        return tokens[2], second, tokens[0]
    if second > 12 and first <= 12:
        return tokens[2], first, tokens[1]
    if first > 12 and second > 12:
        return None
    if first != second:
        notes.append(f"date '{original}' is ambiguous; interpreted as "
                     f"{'day-first' if day_first else 'month-first'}")
    return (tokens[2], second, tokens[0]) if day_first else (tokens[2], first, tokens[1])


def parse_date(raw: Any, day_first: bool = True, languages: Iterable[str] = ("en",)) -> Optional[ParsedDate]:
    """Parse common spellings, e.g. 2024-05-14, 20240514, 14/05/2024, 14.05.2024, May 1st 2026,
    12-May-2025, 'Tuesday, May 14, 2024', 2024-05-14T10:30:00Z and - with French enabled -
    '1er mai 2025', '14 août 2024', 'le 3 janv. 2024', '14 mai 2024 à 20 h 15'.
    Accents are ignored. Month names of all given languages are accepted together (they never clash).
    None if the text is not a real calendar date."""
    if isinstance(raw, datetime):
        return ParsedDate(raw.date())
    if isinstance(raw, date):
        return ParsedDate(raw)
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return None
    try:
        original = str(raw).strip()
    except ValueError:
        return None
    if len(original) > 200:
        return None
    languages = frozenset(languages)
    months, ignored = _tables(languages)
    french = "fr" in languages
    text = _TIME_EN.sub("", fold_accents(original))                      # drop "T10:30:00Z" / " 10:30"
    if french:
        text = _TIME_FR.sub("", text)                                      # drop " a 20 h 15"
    suffixes = "st|nd|rd|th" + ("|ieme|eme|er|re|e" if french else "")
    text = re.sub(rf"(?i)(?<=\d)(?:{suffixes})\b", "", text)              # 1st / 1er -> 1
    text = re.sub(r"[,./\-\s]+", " ", text).lower().strip()
    text = re.sub(r"(?<=\d)(?=[a-z])|(?<=[a-z])(?=\d)", " ", text)
    tokens = [t for t in text.split() if t not in ignored]
    notes: List[str] = []
    parts = None
    if len(tokens) == 1 and re.fullmatch(r"\d{8}", tokens[0]):
        parts = (tokens[0][:4], int(tokens[0][4:6]), tokens[0][6:])
    elif len(tokens) == 3:
        month_positions = [i for i, t in enumerate(tokens) if t in months]
        if len(month_positions) == 1:
            parts = _split_textual_month_date(tokens, month_positions[0], months)
        elif not month_positions and all(t.isdigit() for t in tokens):
            parts = _split_numeric_date(tokens, day_first, notes, original)
    if parts is None:
        return None
    year_text, month, day_text = parts
    if not day_text.isdigit() or len(year_text) not in (2, 4):
        return None
    year = int(year_text)
    if len(year_text) == 2:
        year += 2000
        notes.append(f"two-digit year in '{original}' interpreted as {year}")
    try:
        return ParsedDate(date(year, month, int(day_text)), tuple(notes))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# numbers and amounts
# ---------------------------------------------------------------------------
class NumberParse(NamedTuple):
    """Result of parsing a number: the value (None = not a number), an assumption made (reported as a
    warning) and an error message for a number that is a number but ambiguous."""
    value: Optional[Decimal] = None
    note: Optional[str] = None
    error: Optional[str] = None


_EN = re.compile(r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")               # 1,234.56  1234.5  1234
_FR_COMMA = re.compile(r"\d+,\d+")                                      # 12,5  1234,567  1,234 (=1.234)
_FR_SPACED = re.compile(r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?")  # 1 234,56  1 234 567
_FR_DOT_GROUPED = re.compile(r"\d{1,3}(?:\.\d{3})+,\d+")                # 1.234,56
_PLAIN = re.compile(r"\d+(?:\.\d+)?")                                   # 12.5  12


def _english_digits(text: str) -> Optional[str]:
    """English reading as digits-and-dot text, or None. Comma is a thousands separator (strict groups)."""
    return text.replace(",", "") if _EN.fullmatch(text) else None


def _french_digits(text: str) -> Optional[str]:
    """French reading as digits-and-dot text, or None. The DECIMAL COMMA is checked first, so "1,234"
    is 1.234; a comma is never a thousands separator ("1,234.56" is rejected). Thousands are grouped
    with spaces (incl. NBSP / narrow NBSP) or, with a decimal comma, dots."""
    if _FR_COMMA.fullmatch(text):
        return text.replace(",", ".")
    if _FR_SPACED.fullmatch(text):
        return re.sub(r"[ \u00a0\u202f]", "", text).replace(",", ".")
    if _FR_DOT_GROUPED.fullmatch(text):
        return text.replace(".", "").replace(",", ".")
    if _PLAIN.fullmatch(text):
        return text
    return None


def parse_decimal_text(text: str, locale: str = "en", ambiguous_policy: str = "error") -> NumberParse:
    """Parse unsigned digits text ('1 234,56') according to the locale ("en" | "fr" | "mixed").
    In "mixed" the text is read in both locales: if only one accepts it, that reading is used; if both
    accept it with the SAME value ('12.5', '12') fine; if they give DIFFERENT values ('1,234') the
    `ambiguous_policy` decides: "english" / "french" pick that reading (with a note), anything else is
    an error."""
    if locale == "en":
        digits = _english_digits(text)
        return NumberParse(Decimal(digits)) if digits is not None else NumberParse()
    if locale == "fr":
        digits = _french_digits(text)
        return NumberParse(Decimal(digits)) if digits is not None else NumberParse()
    english, french = _english_digits(text), _french_digits(text)
    if english is None and french is None:
        return NumberParse()
    if french is None:
        return NumberParse(Decimal(english))
    if english is None or Decimal(english) == Decimal(french):
        return NumberParse(Decimal(french))
    message = f"'{text}' is ambiguous: {english} in English format, {french} in French format"
    if ambiguous_policy == "english":
        return NumberParse(Decimal(english), note=f"{message}; read as English")
    if ambiguous_policy == "french":
        return NumberParse(Decimal(french), note=f"{message}; read as French")
    return NumberParse(error=f"{message}; use an unambiguous format or set settings.ambiguous_number_policy")


def _plain_number(raw: Any) -> Optional[NumberParse]:
    """int/float/Decimal -> NumberParse; bool -> empty; anything else (incl. str) -> None (caller parses)."""
    if isinstance(raw, bool):
        return NumberParse()
    if isinstance(raw, (int, float, Decimal)):
        try:
            amount = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return NumberParse()
        return NumberParse(amount) if amount.is_finite() else NumberParse()
    return None


def _strip_currency(text: str) -> str:
    """Remove currency markers before or after the number: $, €, £, CAD, USD, 'dollars', '$ CA'."""
    text = re.sub(r"(?i)^(?:CAD|USD)\s*|\s*(?:CAD|USD|dollars?)$", "", text).strip()
    text = re.sub(r"^([(-]?\s*)[$€£]\s*", r"\1", text).strip()
    return re.sub(r"\s*[$€£]\s*(?:CAD|USD|CA|US)?$", "", text).strip()


def parse_money(raw: Any, locale: str = "en", ambiguous_policy: str = "error") -> NumberParse:
    """Parse an amount, keeping its sign. Accepts currency markers before/after the number,
    accounting negatives '(12.00)' and trailing minus; multiple sign markers are rejected. The digits
    are read by `parse_decimal_text` in the given locale."""
    plain = _plain_number(raw)
    if plain is not None:
        return plain
    if not isinstance(raw, str):
        return NumberParse()
    text = _strip_currency(raw.strip().replace("\u2212", "-"))
    negatives = 0
    if text.startswith("(") and text.endswith(")"):
        negatives, text = negatives + 1, _strip_currency(text[1:-1].strip())
    if text.startswith("-"):
        negatives, text = negatives + 1, _strip_currency(text[1:].strip())
    if text.endswith("-"):
        negatives, text = negatives + 1, _strip_currency(text[:-1].strip())
    if negatives > 1 or (negatives and text.startswith("+")):
        return NumberParse()
    if text.startswith("+"):
        text = text[1:]
    parsed = parse_decimal_text(text, locale, ambiguous_policy)
    if parsed.value is None:
        return parsed
    return NumberParse(parsed.value.copy_negate() if negatives else parsed.value, parsed.note)


def parse_number(raw: Any, locale: str = "en", ambiguous_policy: str = "error") -> NumberParse:
    """Parse a plain number (quantity, duration, ...) with an optional leading sign, using EXACTLY the
    same locale rules as amounts (no currency markers), so '1,5' / '1 000' behave identically for
    quantities and prices."""
    plain = _plain_number(raw)
    if plain is not None:
        return plain
    if not isinstance(raw, str):
        return NumberParse()
    text = raw.strip().replace("\u2212", "-")
    sign = "-" if text.startswith("-") else ""
    if text[:1] in "+-":
        text = text[1:]
    parsed = parse_decimal_text(text, locale, ambiguous_policy)
    if parsed.value is None:
        return parsed
    return NumberParse(parsed.value.copy_negate() if sign else parsed.value, parsed.note)
