"""Line items: locating the array, mapping each item's keys (stage 1: plan_lines), then validating
(stage 2: validate_lines). Everything here is SKIPPED for schemas without "line_fields"
(SchemaConfig.has_lines is False)."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, List, Optional, Set, Tuple

from .field_validation import report_competing_values, report_mapping_findings, validate_field_value
from .matching import (FieldMappingOutcome, FieldMatch, analyze_key_path, key_variants,
                       map_request_keys_to_fields)
from .models import Context, FieldSpec, SchemaConfig
from .paths import PATH_SEP, flatten
from .results import TransformResult


def line_specs(cfg: SchemaConfig) -> List[FieldSpec]:
    """Fields that may appear on a line: the line fields, plus document fields that can repeat per line
    (not patient-owned, not the total) - made optional there (e.g. a different practitioner per visit)."""
    repeatable = [replace(s, required=False, warn_if_missing=False) for s in cfg.document_fields
                  if s.owner != "patient" and s.name != cfg.total_field]
    return list(cfg.line_fields) + repeatable


def is_container_key(key: str, names: List[str], ctx: Context) -> bool:
    """Is `key` the name of a line-item array? Only under understood, unambiguous parents, and only
    for explicitly configured names (any active language) - an array of objects is never assumed to be
    line items. A bilingual name ("Procedures / Procédures") counts only if EVERY variant is a
    recognised container name (same rule as for field labels)."""
    for variant in key_variants(key):
        analysis = analyze_key_path(variant, ctx)
        if analysis.unrecognised_parent_segments or len(analysis.all_owner_roles) > 1:
            return False
        leaf = analysis.leaf_key or analysis.full_key
        if not any(leaf.text == ctx.norm(n).text for n in names):
            return False
    return True


# ---------------------------------------------------------------------------
# stage 1: plan (pure mapping, no reporting)
# ---------------------------------------------------------------------------
@dataclass
class PlannedLine:
    """One line item after key mapping (or the reason it could not be mapped)."""
    number: int
    flat: Optional[Dict[str, Any]] = None
    outcome: Optional[FieldMappingOutcome] = None
    error: Optional[str] = None


@dataclass
class LinePlan:
    """Everything stage 1 found out about the line items of one document."""
    enabled: bool = False                      # False for schemas without line_fields
    container_key: Optional[str] = None
    raw_lines: Optional[List[Any]] = None      # None = no array found (top-level fields = ONE line)
    errors: List[str] = field(default_factory=list)     # structural errors found while planning
    top: FieldMappingOutcome = field(default_factory=FieldMappingOutcome)  # root keys that look like line fields
    lines: List[PlannedLine] = field(default_factory=list)

    def languages(self) -> Set[str]:
        """Languages evidenced by all mapped line keys (input to the document's number format)."""
        found = set(self.top.languages())
        for planned in self.lines:
            if planned.outcome is not None:
                found |= planned.outcome.languages()
        return found


def plan_lines(flat: Dict[str, Any], ctx: Context, consumed: Set[str]) -> LinePlan:
    """Stage 1. Locate the line-item array (several recognised arrays = error, a non-array = error),
    map root keys that look like line fields, and map every line's keys. `consumed` is updated with the
    keys that were used. Nothing is validated or reported here."""
    cfg = ctx.cfg
    plan = LinePlan(enabled=cfg.has_lines)
    if not cfg.has_lines:
        return plan
    matches = [k for k in flat if k not in consumed and is_container_key(k, ctx.line_container_names, ctx)]
    if len(matches) > 1:
        plan.errors.append(f"multiple line-item containers: {', '.join(matches)}")
        plan.raw_lines, plan.container_key = [], matches[0]
    elif matches:
        plan.container_key = matches[0]
        if isinstance(flat[plan.container_key], list):
            plan.raw_lines = flat[plan.container_key]
        else:
            plan.errors.append(f"line-item container '{plan.container_key}' must be an array")
            plan.raw_lines = []
    if plan.container_key:
        consumed.add(plan.container_key)
    plan.top = map_request_keys_to_fields(flat, cfg.line_fields, ctx, True, consumed)
    consumed |= plan.top.consumed_keys

    if plan.raw_lines is not None:
        specs = line_specs(cfg)
        parent_path = (plan.container_key or "").rpartition(PATH_SEP)[0]
        for number, raw_line in enumerate(plan.raw_lines, start=1):
            if not isinstance(raw_line, dict):
                plan.lines.append(PlannedLine(number, error=f"line {number} must be an object"))
                continue
            try:
                flat_line = flatten(raw_line, parent_path)
            except ValueError as error:
                plan.lines.append(PlannedLine(number, error=f"line {number}: {error}"))
                continue
            plan.lines.append(PlannedLine(number, flat_line,
                                          map_request_keys_to_fields(flat_line, specs, ctx, True)))
    return plan


# ---------------------------------------------------------------------------
# stage 2: validate and report
# ---------------------------------------------------------------------------
def validate_line(label: str, matches: Dict[str, FieldMatch], ctx: Context, doc_values: Dict[str, Any],
                  inherited: Dict[str, Tuple[str, Any]], result: TransformResult,
                  unmapped_keys: List[str]) -> Dict[str, Any]:
    """Validate ONE line's matched fields. Order of precedence per field: the line's own value
    (warns if it overrides a different document-level value) > an inherited shared value (opt-in) >
    the document-level value > 'missing' error/warning for required/warn_if_missing line fields."""
    cfg = ctx.cfg
    line_names = {s.name for s in cfg.line_fields}
    values: Dict[str, Any] = {}
    for spec in line_specs(cfg):
        is_line = spec.name in line_names
        if spec.name in matches:
            m = matches[spec.name]
            values[spec.name] = validate_field_value(spec, m.value, ctx, result,
                                                     f"{label} {spec.name} ('{m.request_key}')")
            if not is_line and spec.name in doc_values and values[spec.name] != doc_values[spec.name]:
                result.warnings.append(f"{label}: explicit '{spec.name}' overrides the document-level value")
        elif is_line and spec.name in inherited:
            key, value = inherited[spec.name]
            values[spec.name] = value
            result.warnings.append(f"{label}: '{spec.name}' was not given; inherited from "
                                   f"document-level field '{key}'")
        elif doc_values.get(spec.name) is not None:
            continue
        elif is_line and spec.required:
            hint = f" (unrecognised keys on this line: {', '.join(unmapped_keys)})" if unmapped_keys else ""
            result.errors.append(f"{label}: required field '{spec.name}' is missing{hint}")
        elif is_line and spec.warn_if_missing:
            result.warnings.append(f"{label}: '{spec.name}' was not provided")
    return values


def _treat_top_level_value_as_total(m: FieldMatch, ctx: Context, doc_values: Dict[str, Any],
                                    doc_outcome: FieldMappingOutcome, result: TransformResult) -> None:
    """(opt-in) A top-level amount beside a line list is taken as the document total; the total
    cross-check then confirms it against the lines."""
    spec = ctx.cfg.document_spec(ctx.cfg.total_field)
    value = validate_field_value(spec, m.value, ctx, result, f"{spec.name} ('{m.request_key}')")
    existing = doc_outcome.matches.get(spec.name)
    if existing is None:
        doc_values[spec.name] = value
        result.warnings.append(f"top-level '{m.request_key}' was interpreted as '{spec.name}' because "
                               f"the document has line items; it is not applied to individual lines")
    else:
        report_competing_values(spec.name, existing.request_key, existing.value, m.request_key,
                                m.value, ctx, result, "", existing.language, m.language)


def validate_lines(plan: LinePlan, ctx: Context, doc_values: Dict[str, Any],
                   doc_outcome: FieldMappingOutcome, result: TransformResult) -> List[Dict[str, Any]]:
    """Stage 2: report the plan's findings and validate every line; returns the normalised lines.
    `ctx` must be the DOCUMENT context (number format already chosen).
      * Schema without line_fields -> [].
      * A recognised array -> each element validated.  * No array -> top-level line fields form ONE line."""
    cfg, s = ctx.cfg, ctx.settings
    if not plan.enabled:
        return []
    result.errors.extend(plan.errors)
    if plan.container_key:
        result.field_mapping[plan.container_key] = "lines"
    top = plan.top
    report_mapping_findings(top, result, ctx)
    by_name = {sp.name: sp for sp in cfg.line_fields}
    lines: List[Dict[str, Any]] = []

    if plan.raw_lines is not None:
        # The document has its own lines, so top-level line-type values are NOT copied into them.
        inherited: Dict[str, Tuple[str, Any]] = {}
        for name, m in top.matches.items():
            spec = by_name[name]
            if spec.inherit and s["inherit_shared_fields"]:
                inherited[name] = (m.request_key, validate_field_value(
                    spec, m.value, ctx, result, f"{name} ('{m.request_key}')"))
            elif spec.total_equivalent and s["interpret_root_amount_as_total"] and cfg.document_spec(cfg.total_field):
                _treat_top_level_value_as_total(m, ctx, doc_values, doc_outcome, result)
            else:
                result.warnings.append(f"top-level '{m.request_key}' looks like the line field "
                                       f"'{name}', but the document has its own lines; not applied")
        if not plan.raw_lines:
            result.errors.append("the list of line items is empty")
        for planned in plan.lines:
            label = f"line {planned.number}"
            if planned.error:
                result.errors.append(planned.error)
                continue
            report_mapping_findings(planned.outcome, result, ctx, location=label)
            unmapped = [k for k in planned.flat if k not in planned.outcome.consumed_keys]
            if unmapped:
                result.unmapped_fields.extend(f"{label}{PATH_SEP}{k}" for k in unmapped)
                result.warnings.append(f"{label}: ignored unrecognised field(s): {', '.join(unmapped)}")
            lines.append(validate_line(label, planned.outcome.matches, ctx, doc_values, inherited,
                                       result, unmapped))
        return lines

    # No array: the top-level fields describe ONE line item.
    single = dict(top.matches)
    equivalent = next((sp.name for sp in cfg.line_fields if sp.total_equivalent), None)
    total_match = doc_outcome.matches.get(cfg.total_field) if cfg.total_field else None
    if s["single_line_total_as_amount"] and equivalent and equivalent not in single and total_match:
        single[equivalent] = replace(total_match, canonical_name=equivalent)
        result.warnings.append(f"single line without its own amount: '{total_match.request_key}' "
                               f"was used as '{equivalent}'")
    if not single:
        result.errors.append(f"no line items found: supply a list (e.g. '{ctx.primary_container_name}') "
                             f"or the line fields themselves")
        return []
    lines.append(validate_line("line 1", single, ctx, doc_values, {}, result, []))
    return lines
