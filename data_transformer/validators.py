"""Built-in field validators. Each has the signature (raw_value, FieldSpec, Context) -> ValidationOutcome
and is referenced from configs by name ("validator": "money"). Parameters come from the field's "params".

  text          min_length (1), max_length (200)
  person_name   as text; letters (accents included)/spaces/'.,- only; warns on a single name
  identifier    1-40 chars of letters, digits, space, / - .
  address       text >= 8 chars with a letter; warns without a street number
  phone         10-15 digits, optional extension; output = digits (+ " x123")
  date          params: max_age_days (int | null = unlimited; default = setting), allow_future (bool)
                English + French spellings (French when "fr" is an active language)
  money         params: limit = name of an entry in the config's "limits"; rounds to cents with a warning;
                French amounts ("1 234,56 $") when "fr" is active
  number        params: minimum, maximum, integer; decimal comma when "fr" is active
  regex         params: patterns[], upper, strip, unique_chars, hints[{pattern,message}], message
  vocabulary    params: vocabulary (name in the config's "vocabularies"), strict; bilingual values
                ("Massothérapeute / Massage therapist") are understood
"""
from __future__ import annotations

import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal

from .normalization import text_similarity, text_variants
from .registry import fail, ok, register_validator
from .value_parsers import parse_date, parse_money, parse_number


def describe(value) -> str:
    """str(value) that cannot raise on absurdly long integers."""
    try:
        return str(value)
    except ValueError:
        return "<value exceeds the supported numeric text length>"


@register_validator("text")
def validate_text(raw, spec, ctx):
    """Non-empty text within min_length/max_length. Booleans and numbers are not text."""
    if not isinstance(raw, str):
        return fail("value must be text")
    text = raw.strip()
    minimum, maximum = spec.params.get("min_length", 1), spec.params.get("max_length", 200)
    if len(text) < minimum:
        return fail(f"value is too short (minimum {minimum} characters)")
    if len(text) > maximum:
        return fail(f"value is too long (maximum {maximum} characters)")
    return ok(text)


@register_validator("person_name")
def validate_person_name(raw, spec, ctx):
    """Text made of letters, spaces, combining marks and ' ’ . , - only; warns for one-word names."""
    outcome = validate_text(raw, spec, ctx)
    if outcome.error:
        return outcome
    name = outcome.value
    if not any(c.isalpha() for c in name) or any(
            not (c.isalpha() or c.isspace() or c in "'’.,-" or unicodedata.category(c) == "Mn")
            for c in name):
        return fail(f"'{name}' contains characters that are not valid in a name")
    if len(name.split()) < 2:
        return ok(name, f"'{name}' looks like a single name; a full name is expected")
    return ok(name)


@register_validator("identifier")
def validate_identifier(raw, spec, ctx):
    """1-40 character identifier; keeps leading zeros and punctuation exactly as given."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return fail("identifier must be a string or an integer")
    text = describe(raw).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9\-/ .]{0,39}", text):
        return fail("identifier must contain 1-40 alphanumeric, space, slash, hyphen or period characters")
    return ok(text)


@register_validator("address")
def validate_address(raw, spec, ctx):
    """Plausible postal address: >= 8 characters including a letter; warns when no digit is present."""
    outcome = validate_text(raw, spec, ctx)
    if outcome.error:
        return outcome
    text = outcome.value
    if len(text) < 8 or not any(c.isalpha() for c in text):
        return fail(f"'{text}' does not look like a valid address")
    if not any(c.isdigit() for c in text):
        return ok(text, f"address '{text}' has no street number")
    return ok(text)


@register_validator("phone")
def validate_phone(raw, spec, ctx):
    """10-15 digits with optional punctuation and extension ('ext 12', 'poste 12', 'x12')."""
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return fail("phone number must be text or an integer")
    text, extension = describe(raw).strip(), ""
    m = re.search(r"(?i)\s*(?:ext\.?|poste|p\.|x)\s*(\d+)\s*$", text)
    if m:
        extension, text = " x" + m.group(1), text[:m.start()]
    if re.search(r"[^\d\s()+\-.]", text):
        return fail("phone number contains invalid characters")
    digits = re.sub(r"\D", "", text)
    if not 10 <= len(digits) <= 15:
        return fail("phone number requires 10-15 digits")
    return ok(digits + extension)


@register_validator("date")
def validate_date(raw, spec, ctx):
    """Parse to ISO yyyy-mm-dd. Rejects future dates (unless allowed) and dates older than the limit.
    Per-field params override the global settings, e.g. a birth date: {"max_age_days": null}."""
    parsed = parse_date(raw, ctx.settings["day_first"], ctx.languages)
    if parsed is None:
        return fail(f"'{describe(raw)}' is not a recognisable date")
    allow_future = spec.params.get("allow_future", ctx.settings["allow_future_dates"])
    if parsed.value > ctx.today and not allow_future:
        return fail(f"date {parsed.value.isoformat()} is in the future")
    limit = spec.params["max_age_days"] if "max_age_days" in spec.params else ctx.settings["max_age_days"]
    if limit is not None and (ctx.today - parsed.value).days > limit:
        return fail(f"date {parsed.value.isoformat()} is older than {limit} days")
    return ok(parsed.value.isoformat(), *parsed.notes)


@register_validator("money")
def validate_money(raw, spec, ctx):
    """Non-negative amount within the named limit of the config's "limits"; rounded half-up to cents
    (a warning is added when rounding changed the value). The digits are read in the document's number
    locale (ctx.number_locale): English "1,234.56", French "1 234,56" (decimal comma first; a comma is
    never a thousands separator), or - in a bilingual document - either, with ambiguous values such as
    "1,234" handled by settings.ambiguous_number_policy. Output is a float with 2 decimals."""
    parsed = parse_money(raw, ctx.number_locale, ctx.settings["ambiguous_number_policy"])
    if parsed.error:
        return fail(f"amount {parsed.error}")
    if parsed.value is None:
        return fail(f"'{describe(raw)}' is not a valid numeric amount (number format: {ctx.number_locale})")
    amount, notes = parsed.value, ([parsed.note] if parsed.note else [])
    if amount < 0:
        return fail(f"amount {amount} is negative; negative amounts are not accepted")
    minimum, maximum = ctx.cfg.limits.get(spec.params.get("limit"), (0, float("inf")))
    if amount < Decimal(str(minimum)):
        return fail(f"amount {amount} is below the minimum {minimum:.2f}")
    if maximum != float("inf") and amount > Decimal(str(maximum)):
        return fail(f"amount {amount} exceeds the reasonable maximum {maximum:.2f} for {spec.name}")
    rounded = amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if rounded != amount:
        notes.append(f"amount {amount} has more than 2 decimal places; rounded")
    return ok(float(rounded), *notes)


@register_validator("number")
def validate_number(raw, spec, ctx):
    """Plain decimal number (quantity, duration, ...) within [minimum, maximum]; `integer: true` forbids
    fractions. Uses EXACTLY the same number locale rules as amounts, so '1,5' (French), '1,000'
    (English) and bilingual ambiguities behave identically for quantities and prices."""
    parsed = parse_number(raw, ctx.number_locale, ctx.settings["ambiguous_number_policy"])
    if parsed.error:
        return fail(f"value {parsed.error}")
    if parsed.value is None:
        return fail(f"value is not numeric (number format: {ctx.number_locale})")
    number, p = parsed.value, spec.params
    if p.get("integer") and number != number.to_integral_value():
        return fail("value must be a whole number")
    minimum, maximum = Decimal(str(p.get("minimum", 0))), Decimal(str(p.get("maximum", 1000000)))
    if not minimum <= number <= maximum:
        return fail(f"value is outside the reasonable range {minimum}-{maximum}")
    value = int(number) if number == number.to_integral_value() else float(number)
    return ok(value, *([parsed.note] if parsed.note else []))


@register_validator("regex")
def validate_regex(raw, spec, ctx):
    """Generic pattern validator (replaces hard-coded code/ID validators).
    params: patterns  - list of regexes; the value must FULL-match at least one
            upper     - upper-case before matching
            strip     - regex of characters removed before matching (e.g. separators)
            unique_chars - no character may repeat
            hints     - [{pattern, message}] friendlier message for known near misses
            message   - default error message"""
    if isinstance(raw, bool) or not isinstance(raw, (str, int)):
        return fail("value must be text or an integer")
    original = describe(raw)
    text, p = original.strip(), spec.params
    if p.get("strip"):
        text = re.sub(p["strip"], "", text)
    if p.get("upper"):
        text = text.upper()
    for pattern in p.get("patterns", []):
        if re.fullmatch(pattern, text) and not (p.get("unique_chars") and len(set(text)) != len(text)):
            return ok(text)
    for hint in p.get("hints", []):
        if re.fullmatch(hint["pattern"], text):
            return fail(hint["message"])
    return fail(p.get("message", f"'{original}' does not match the expected format"))


@register_validator("vocabulary")
def validate_vocabulary(raw, spec, ctx):
    """Fuzzy-map free text to a canonical term of cfg.vocabularies[params.vocabulary], using the aliases
    of every ACTIVE language ('RMT' / 'massothérapeute' -> 'massage therapist'). Bilingual values
    ('Massothérapeute / Massage therapist') are split and the best part wins. Unknown terms: warning +
    normalised text kept, or an error if params.strict. The output is always the canonical term."""
    if not isinstance(raw, str):
        return fail("value must be text")
    name = spec.params["vocabulary"]
    value_texts = [t for t in (ctx.norm_plain(v).text for v in text_variants(raw, include_whole=True)) if t]
    if not value_texts:
        return fail("value is empty")
    best_score, best_term = 0.0, None
    for canonical, aliases in ctx.vocabulary(name).items():
        for alias in aliases:
            alias_text = ctx.norm_plain(alias).text
            for value_text in value_texts:
                score = text_similarity(value_text, alias_text)
                if len(alias_text) >= 4 and (alias_text in value_text or value_text in alias_text):
                    score = max(score, 0.9)
                if score > best_score:
                    best_score, best_term = score, canonical
    if best_score >= ctx.settings["vocabulary_threshold"]:
        return ok(best_term)
    if spec.params.get("strict"):
        return fail(f"'{raw}' is not a recognised {name} value")
    return ok(value_texts[0], f"'{raw}' is not a recognised {name} value; defaults apply")
