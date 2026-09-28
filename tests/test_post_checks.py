"""Cross-field limits, totals, extension hooks and output-schema enforcement."""
import pytest

from data_transformer import build_schema_config, register_rule, register_validator, transform
from data_transformer.post_checks import check_total_against_lines, enforce_target_schema, run_rules
from data_transformer.registry import FIELD_VALIDATORS, POST_RULES, ok
from data_transformer.results import TransformResult


@pytest.mark.parametrize("severity", ["error", "warning"])
def test_vocabulary_limits_and_rate_rules(context_factory, severity):
    context = context_factory(vocabularies={"types": {"massage": {"max": 100}}}, rules=[
        {"kind": "max_by_vocabulary", "amount_field": "price", "type_field": "type",
         "vocabulary": "types", "attribute": "max", "default": 50, "severity": severity},
        {"kind": "rate_limit", "amount_field": "price", "unit_field": "minutes", "max_rate": 5}])
    result = TransformResult()
    run_rules({}, [{"price": None}, {"price": 10, "minutes": 0},
        {"price": 120, "type": "massage", "minutes": 10}, {"price": 60, "minutes": 30}], context, result)
    messages = result.errors if severity == "error" else result.warnings
    assert sum("exceeds" in message for message in messages) == 2
    assert any("unusually high rate" in warning for warning in result.warnings)
    # A document-level vocabulary term also supplies the line limit.
    result = TransformResult()
    run_rules({"type": "massage"}, [{"price": 75}], context, result)
    assert not result.errors and not result.warnings


def test_totals_skip_missing_primary_amount_and_can_warn(context_factory):
    context = context_factory(line_fields={"price": {"validator": "money", "sums_to_total": True},
        "fee": {"validator": "money", "required": False, "sums_to_total": True}},
        settings={"total_mismatch_severity": "warning"})
    result = TransformResult()
    check_total_against_lines(context, {"total_amount": 20}, [{"price": None}], result)
    assert not result.errors and not result.warnings
    check_total_against_lines(context, {"total_amount": 20}, [{"price": 10, "fee": None}], result)
    assert not result.errors and "sum of the line items 10.00" in result.warnings[0]


def test_custom_validator_and_rule_integrate_through_public_api(monkeypatch):
    # Restore registries even if the test fails, keeping other schemas independent.
    monkeypatch.setitem(FIELD_VALIDATORS, "test_uppercase", None)
    monkeypatch.setitem(POST_RULES, "test_require_prefix", None)

    @register_validator("test_uppercase")
    def uppercase(raw, specification, context):
        return ok(raw.upper())

    @register_rule("test_require_prefix")
    def require_prefix(document, lines, context, result, rule):
        if not document["label"].startswith("ON-"):
            result.errors.append("Ontario prefix required")

    configuration = {"schema_name": "custom", "document_fields": {"label": {"validator": "test_uppercase"}},
                     "rules": [{"kind": "custom", "name": "test_require_prefix"}]}
    report = transform({"label": "on-123"}, configuration)
    assert report.is_valid and report.results[0].normalized["fields"]["label"] == "ON-123"
    report = transform({"label": "qc-123"}, configuration)
    assert not report.is_valid and report.results[0].errors == ["Ontario prefix required"]


def test_target_schema_errors_include_paths_and_missing_validator_is_not_silently_skipped(context_factory):
    context = context_factory(target_schema={"type": "object", "properties": {"fields": {
        "type": "object", "properties": {"label": {"type": "integer"}}}}})
    result = TransformResult()
    enforce_target_schema({"fields": {"label": "bad"}}, context, result)
    assert "fields/label" in result.errors[0]
    result = TransformResult()
    enforce_target_schema([], context, result)
    assert "<root>" in result.errors[0]
    context.cfg.target_validator = None
    result = TransformResult()
    enforce_target_schema({}, context, result)
    assert "no compiled validator" in result.errors[0]
