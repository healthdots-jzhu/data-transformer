"""Helpers shared by document- and line-level processing: validating one value, comparing values that
competed for one field, and turning matcher findings into errors/warnings/audit entries."""
from __future__ import annotations

from typing import Any

from .matching import FieldMappingOutcome, key_variants
from .models import Context, FieldSpec
from .paths import PATH_SEP
from .registry import FIELD_VALIDATORS
from .results import TransformResult
from .value_parsers import parse_date, parse_money


def is_empty(value: Any) -> bool:
    """None or blank text."""
    return value is None or (isinstance(value, str) and not value.strip())


def validate_field_value(spec: FieldSpec, raw: Any, ctx: Context, result: TransformResult, label: str) -> Any:
    """Run the field's validator; append its error/warnings (prefixed with `label`) to the result and
    return the normalised value (None on failure/empty). Empty required values are errors."""
    if is_empty(raw):
        if spec.required:
            result.errors.append(f"{label} is empty")
        return None
    outcome = FIELD_VALIDATORS[spec.validator](raw, spec, ctx)
    if outcome.error:
        result.errors.append(f"{label}: {outcome.error}")
    result.warnings.extend(f"{label}: {w}" for w in outcome.warnings)
    return outcome.value


def values_are_equivalent(left: Any, right: Any, name: str, ctx: Context) -> bool:
    """Are two raw values the same thing for this field? Money/dates/numbers/phones/vocabulary are
    compared by meaning (in the document's number locale); everything else conservatively
    ('AB-12' != 'AB12', leading zeros matter)."""
    spec = next(s for s in ctx.cfg.all_specs if s.name == name)
    if spec.validator == "money":
        policy = ctx.settings["ambiguous_number_policy"]
        a = parse_money(left, ctx.number_locale, policy).value
        b = parse_money(right, ctx.number_locale, policy).value
        return a is not None and b is not None and a == b
    if spec.validator == "date":
        a = parse_date(left, ctx.settings["day_first"], ctx.languages)
        b = parse_date(right, ctx.settings["day_first"], ctx.languages)
        return a is not None and b is not None and a.value == b.value
    if spec.validator in {"phone", "number", "vocabulary"}:
        a, b = (FIELD_VALIDATORS[spec.validator](v, spec, ctx) for v in (left, right))
        return not a.error and not b.error and a.value == b.value
    if type(left) is not type(right):
        return False
    return left.strip() == right.strip() if isinstance(left, str) else left == right


def report_competing_values(name, winning_key, winning_value, losing_key, losing_value,
                            ctx: Context, result: TransformResult, where: str,
                            winning_language=None, losing_language=None) -> None:
    """Two keys mapped to one field. Same value in DIFFERENT languages ("Patient Name" + "Nom du
    patient"): expected bilingual redundancy, reported silently (the mapping audit still lists both).
    Same value otherwise: warning (duplicate ignored). Different values: error or warning according
    to settings.conflicting_values_policy."""
    if values_are_equivalent(winning_value, losing_value, name, ctx):
        if winning_language and losing_language and winning_language != losing_language:
            return
        result.warnings.append(f"{where}'{losing_key}' repeats '{winning_key}' (same value for "
                               f"'{name}'); the duplicate was ignored")
        return
    message = (f"{where}conflicting values for '{name}': '{winning_key}'={winning_value!r} "
               f"vs '{losing_key}'={losing_value!r}")
    if ctx.settings["conflicting_values_policy"] == "error":
        result.errors.append(message)
    else:
        result.warnings.append(f"{message}; using '{winning_key}'")


def report_mapping_findings(outcome: FieldMappingOutcome, result: TransformResult, ctx: Context,
                            location: str = "") -> None:
    """Turn a mapping outcome into the response: the key -> field audit trail, detected languages,
    translation pairs, warnings for generic names, errors for ambiguous keys, duplicate/conflict
    reports, and explanations for keys that were refused (ownership/parent rules, or language variants
    that disagree) and ended up unused."""
    where = f"{location}: " if location else ""
    for key in outcome.ambiguous_keys:
        result.errors.append(f"{where}'{key}' has ambiguous field meanings")
    for match in outcome.matches.values():
        label = f"{location}{PATH_SEP}{match.request_key}" if location else match.request_key
        result.field_mapping[label] = match.canonical_name
        for language in match.languages:
            if language not in result.languages_detected:
                result.languages_detected.append(language)
        if match.matched_via_weak_alias:
            result.warnings.append(f"{where}generic field name '{match.request_key}' was interpreted "
                                   f"as '{match.canonical_name}' using {ctx.cfg.name} context")
    for name, by_language in outcome.translations.items():
        label = f"{location}{PATH_SEP}{name}" if location else name
        result.translations[label] = dict(by_language)
        for language in by_language:
            if language not in result.languages_detected:
                result.languages_detected.append(language)
    for c in outcome.competing_matches:
        report_competing_values(c.canonical_name, c.winning_key, c.winning_value, c.losing_key,
                                c.losing_value, ctx, result, where, c.winning_language, c.losing_language)
    consumed = outcome.consumed_keys
    for key, reasons in outcome.refused_matches.items():
        if key not in consumed:
            result.warnings.append(f"{where}'{key}' was not used: {'; '.join(reasons)}")


def report_abbreviation_expansions(record: dict, ctx: Context, result: TransformResult,
                                   location: str = "") -> None:
    """Warn about every low-confidence abbreviation expansion in the source keys (including keys
    inside line items and each language variant of a bilingual key), even if the key ends up unmapped."""
    for key, value in record.items():
        seen = set()
        for variant in key_variants(str(key)):
            for e in ctx.norm(variant).expansions:
                if e.confidence < ctx.settings["abbreviation_confidence_without_warning"] \
                        and (e.abbreviation, e.expansion) not in seen:
                    seen.add((e.abbreviation, e.expansion))
                    result.warnings.append(f"{location}{key}: abbreviation '{e.abbreviation}' was "
                                           f"interpreted as '{e.expansion}' ({e.confidence:.0%} confidence)")
        items = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
        for index, item in enumerate(items, start=1):
            if isinstance(item, dict):
                report_abbreviation_expansions({str(k): v for k, v in item.items()}, ctx, result,
                                               f"{location}{key}[{index}].")
