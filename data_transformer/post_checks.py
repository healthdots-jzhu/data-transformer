"""Checks that run after individual fields are validated: the total vs. its lines, the declarative and
custom rules from the config, and the optional target JSON Schema."""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Dict, List

from .models import Context
from .registry import POST_RULES
from .results import TransformResult


def check_total_against_lines(ctx: Context, doc_values: Dict[str, Any], lines: List[Dict[str, Any]],
                              result: TransformResult) -> None:
    """If the document states a total, it must equal the sum of all line fields flagged
    "sums_to_total". Skipped when any line lacks its primary amount (already reported) or the schema
    has no lines. Severity: settings.total_mismatch_severity."""
    cfg = ctx.cfg
    claimed = doc_values.get(cfg.total_field) if cfg.total_field else None
    sum_fields = [s.name for s in cfg.line_fields if s.sums_to_total]
    if claimed is None or not lines or not sum_fields:
        return
    if any(line.get(sum_fields[0]) is None for line in lines):
        return
    line_sum = sum(Decimal(str(l[n])) for l in lines for n in sum_fields if l.get(n) is not None)
    if line_sum != Decimal(str(claimed)):
        message = f"{cfg.total_field} {claimed:.2f} does not equal the sum of the line items {line_sum:.2f}"
        (result.errors if ctx.settings["total_mismatch_severity"] == "error" else result.warnings).append(message)


def run_rules(doc_values: Dict[str, Any], lines: List[Dict[str, Any]], ctx: Context,
              result: TransformResult) -> None:
    """Execute the config's "rules" in order.
      max_by_vocabulary  per line: amount <= vocabulary[type][attribute] (else default)
      rate_limit         per line: amount / unit_field <= max_rate (warning)
      custom             calls the function registered with @register_rule"""
    for rule in ctx.cfg.rules:
        kind = rule["kind"]
        if kind == "custom":
            POST_RULES[rule["name"]](doc_values, lines, ctx, result, rule)
            continue
        for number, line in enumerate(lines, start=1):
            amount = line.get(rule["amount_field"])
            if amount is None:
                continue
            if kind == "max_by_vocabulary":
                term = line.get(rule["type_field"]) or doc_values.get(rule["type_field"])
                maximum = (ctx.cfg.vocabularies[rule["vocabulary"]].get(term, {})
                           .get(rule["attribute"], rule["default"]))
                if amount > maximum:
                    message = (f"line {number}: {rule['amount_field']} {amount:.2f} exceeds the "
                               f"reasonable maximum {maximum:.2f} for '{term or 'unknown ' + rule['type_field']}'")
                    (result.errors if rule.get("severity", "error") == "error" else result.warnings).append(message)
            elif kind == "rate_limit":
                units = line.get(rule["unit_field"])
                if units and amount / units > rule["max_rate"]:
                    result.warnings.append(f"line {number}: {amount:.2f} for {units:g} "
                                           f"{rule.get('unit_label', 'units')} is an unusually high rate")


def enforce_target_schema(normalized: Dict[str, Any], ctx: Context, result: TransformResult) -> None:
    """Validate the final output against the config's target JSON Schema using the validator compiled
    at load time. A schema without a compiled validator is an ERROR (never silently skipped)."""
    cfg = ctx.cfg
    if not cfg.target_schema:
        return
    if cfg.target_validator is None:   # config object built by hand, bypassing the loader
        result.errors.append("target schema could not be enforced (no compiled validator); "
                             "output rejected rather than unchecked")
        return
    for error in sorted(cfg.target_validator.iter_errors(normalized), key=lambda e: [str(p) for p in e.path]):
        location = "/".join(str(p) for p in error.path) or "<root>"
        result.errors.append(f"target schema: {location}: {error.message}")
