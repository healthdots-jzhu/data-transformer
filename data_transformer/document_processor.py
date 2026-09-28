"""Processing ONE document for an already-selected schema: map every key, decide the document's
language(s) and number format, then validate, cross-check and build the output JSON."""
from __future__ import annotations

from typing import Any, Dict, Iterable, Optional, Set

from .field_validation import (report_abbreviation_expansions, report_mapping_findings,
                               validate_field_value)
from .line_items import plan_lines, validate_lines
from .matching import map_request_keys_to_fields
from .models import Context
from .post_checks import check_total_against_lines, enforce_target_schema, run_rules
from .results import TransformResult


def process_document(flat: Dict[str, Any], ctx: Context, discriminator_key: Optional[str],
                     result: TransformResult, discriminator_languages: Iterable[str] = ()) -> None:
    """Fill `result` for one flattened document.
      1. MAP (no reporting yet): document-level keys, then the line-item array and every line's keys.
      2. DECIDE THE DOCUMENT'S LANGUAGES from the keys that were mapped (plus the type key): this selects
         the number format ("en", "fr" or "mixed") used for every amount and number in the document.
      3. Report the mapping, validate document-level fields, then lines (only if the schema has them).
      4. Required document fields still missing (allowed if given on every line).
      5. Total vs lines (only when a total exists); build output; declarative/custom rules.
      6. Report ignored keys and detected languages; enforce the target JSON Schema (only if no
         errors so far).
    The output is {type_key: schema, fields_key: {...}} plus {lines_key: [...]} for schemas with lines.
    Output field names and vocabulary terms are canonical whatever language the source used."""
    base_ctx = ctx
    report_abbreviation_expansions(flat, base_ctx, result)
    consumed: Set[str] = set()
    if discriminator_key and discriminator_key in flat:
        consumed.add(discriminator_key)
        result.field_mapping[discriminator_key] = "schema_type"

    # ---- 1. map -----------------------------------------------------------------------------------
    outcome = map_request_keys_to_fields(flat, base_ctx.cfg.document_fields, base_ctx, True, consumed)
    consumed |= outcome.consumed_keys
    plan = plan_lines(flat, base_ctx, consumed)

    # ---- 2. document languages -> number format -------------------------------------------------------
    languages: Set[str] = set(discriminator_languages) | outcome.languages() | plan.languages()
    ctx = base_ctx.for_document(languages)
    cfg = ctx.cfg
    result.number_locale = ctx.number_locale

    # ---- 3. report + validate ----------------------------------------------------------------------------
    report_mapping_findings(outcome, result, ctx)
    doc_values: Dict[str, Any] = {}
    for spec in cfg.document_fields:
        m = outcome.matches.get(spec.name)
        if m:
            doc_values[spec.name] = validate_field_value(spec, m.value, ctx, result,
                                                         f"{spec.name} ('{m.request_key}')")
    lines = validate_lines(plan, ctx, doc_values, outcome, result)

    # ---- 4. required document fields ---------------------------------------------------------------------
    for spec in cfg.document_fields:
        if spec.name in outcome.matches:
            continue
        if bool(lines) and all(l.get(spec.name) is not None for l in lines):
            continue                       # supplied on every line instead
        if spec.required:
            result.errors.append(f"required field '{spec.name}' is missing")
        elif spec.warn_if_missing:
            result.warnings.append(f"'{spec.name}' was not provided")

    # ---- 5. cross-checks, output, rules ----------------------------------------------------------------------
    check_total_against_lines(ctx, doc_values, lines, result)
    out = cfg.output
    result.normalized = {out["type_key"]: cfg.name, out["fields_key"]: doc_values}
    if cfg.has_lines:
        result.normalized[out["lines_key"]] = lines
    run_rules(doc_values, lines, ctx, result)

    # ---- 6. wrap up ------------------------------------------------------------------------------------------------
    ignored = [k for k in flat if k not in consumed]
    result.unmapped_fields.extend(ignored)
    if ignored:
        result.warnings.append(f"ignored unrecognised field(s): {', '.join(ignored)}")
    result.languages_detected = sorted(set(result.languages_detected) | languages)
    if not result.errors:
        enforce_target_schema(result.normalized, ctx, result)
    result.is_valid = not result.errors
