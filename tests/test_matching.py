"""Ownership, alias ambiguity, language tags and duplicate-value contracts."""
import pytest

from data_transformer.field_validation import (report_abbreviation_expansions, report_competing_values,
    report_mapping_findings, validate_field_value, values_are_equivalent)
from data_transformer.matching import key_variants, map_request_keys_to_fields
from data_transformer.models import FieldSpec
from data_transformer.normalization import AbbreviationRule, normalize_key, text_similarity
from data_transformer.results import TransformResult


def map_fields(source, context, use_weak=True):
    return map_request_keys_to_fields(source, context.cfg.document_fields, context, use_weak)


@pytest.mark.parametrize("key,accepted", [
    ("provider.address", True), ("provider.contact.address", True),
    ("patient.address", False), ("provider.patient.address", False),
    ("customer.address", False), ("address", False),
])
def test_nested_ownership_never_disappears(context_factory, key, accepted):
    context = context_factory(owner_role_keywords={"provider": ["provider"], "patient": ["patient"]},
        neutral_container_words=["contact"], document_fields={"provider_address": {
            "validator": "text", "owner": "provider", "explicit_owner": True,
            "aliases": ["provider address", "address"]}})
    outcome = map_fields({key: "123 Main Street"}, context)
    assert bool(outcome.matches) is accepted
    if not accepted:
        assert outcome.refused_matches[key]


def test_unknown_parent_requires_exact_full_descriptive_alias(context_factory):
    context = context_factory(document_fields={"reference": {"validator": "text", "aliases": ["customer code"]}})
    assert map_fields({"customer.code": "ABC"}, context).matches["reference"].value == "ABC"
    assert not map_fields({"customer.cod": "ABC"}, context).matches


@pytest.mark.parametrize("key,language", [("label_fr", "fr"), ("fr_label", "fr"), ("label.fr", "fr"),
                                         ("label.en", "en")])
def test_language_suffix_prefix_and_nested_tags(context_factory, key, language):
    context = context_factory()
    outcome = map_fields({key: "Value"}, context)
    assert outcome.matches["label"].language == language


def test_tied_meanings_are_not_arbitrarily_selected(context_factory):
    context = context_factory(document_fields={
        "first": {"validator": "text", "aliases": ["shared label"]},
        "second": {"validator": "text", "aliases": ["shared label"]}})
    outcome = map_fields({"shared label": "value"}, context)
    assert outcome.ambiguous_keys == ["shared label"] and not outcome.matches
    result = TransformResult()
    report_mapping_findings(outcome, result, context)
    assert "ambiguous field meanings" in result.errors[0]


def test_generic_aliases_are_exact_and_low_confidence_expansion_cannot_establish_match(context_factory):
    context = context_factory(document_fields={"price": {"validator": "money", "weak_aliases": ["amount"]}},
        abbreviation_rules=[{"abbreviation": "amt", "expansion": "amount", "confidence": 0.7}])
    outcome = map_fields({"amount": 10}, context)
    assert outcome.matches["price"].matched_via_weak_alias
    assert not map_fields({"amt": 10}, context).matches
    assert not map_fields({"amount": 10}, context, use_weak=False).matches
    assert not map_fields({"min amount": 10}, context).matches
    result = TransformResult()
    report_mapping_findings(outcome, result, context)
    assert "generic field name" in result.warnings[0]


def test_disqualifying_word_cannot_replace_descriptive_meaning(context_factory):
    context = context_factory(disqualifying_key_words=["max"],
        document_fields={"price": {"validator": "money", "aliases": ["tax amount"]}})
    assert not map_fields({"max amount": 10}, context).matches


@pytest.mark.parametrize("validator,left,right,equal", [
    ("money", "$12.50", 12.5, True), ("money", "bad", "bad", False),
    ("date", "May 1 2026", "2026-05-01", True), ("date", "bad", "bad", False),
    ("phone", "416-555-0199", "4165550199", True), ("phone", "bad", "bad", False),
    ("number", "12", 12, True), ("text", " A ", "A", True),
    ("identifier", "001", 1, False), ("identifier", "AB-12", "AB12", False),
    ("identifier", 12, 12, True),
])
def test_duplicate_values_use_field_specific_meaning(context_factory, validator, left, right, equal):
    context = context_factory(document_fields={"value": {"validator": validator}}).for_document({"en"})
    assert values_are_equivalent(left, right, "value", context) is equal


@pytest.mark.parametrize("policy", ["error", "warning"])
def test_conflicting_values_follow_configured_severity(context_factory, policy):
    context = context_factory(settings={"conflicting_values_policy": policy})
    result = TransformResult()
    report_competing_values("label", "label", "First", "other label", "Second", context, result, "")
    messages = result.errors if policy == "error" else result.warnings
    assert len(messages) == 1 and "conflicting values" in messages[0]


def test_duplicate_and_translation_reporting(context_factory):
    context = context_factory(document_fields={"label": {"validator": "text", "translatable": True,
        "aliases": ["name"], "i18n": {"fr": {"aliases": ["nom"]}}}})
    outcome = map_fields({"name": "Frame", "nom": "Monture"}, context)
    result = TransformResult()
    report_mapping_findings(outcome, result, context)
    assert result.translations["label"] == {"en": "Frame", "fr": "Monture"}
    assert outcome.languages() == {"en", "fr"}
    assert outcome.consumed_keys == {"name", "nom"}
    report_competing_values("label", "name", "same", "nom", "same", context, result, "", "en", "fr")
    assert not result.errors and not result.warnings
    report_competing_values("label", "name", "same", "label", "same", context, result, "")
    assert "duplicate was ignored" in result.warnings[0]


@pytest.mark.parametrize("required", [True, False])
def test_empty_value_is_only_an_error_when_required(context_factory, required):
    result = TransformResult()
    assert validate_field_value(FieldSpec("label", "text", required=required), " ", context_factory(), result, "label") is None
    assert bool(result.errors) is required


def test_contextual_abbreviations_and_recursive_warning_audit(context_factory):
    context = context_factory(abbreviation_rules=[
        {"abbreviation": "dt", "expansion": "date", "confidence": 0.7, "previous": ["service"]},
        {"abbreviation": "pt", "expansion": "patient", "confidence": 0.8, "following": ["name"]}])
    assert context.norm("service dt").text == "service date"
    assert context.norm("dt").text == "dt"
    assert context.norm("pt name").text == "patient name"
    result = TransformResult()
    report_abbreviation_expansions({"service dt / service dt": 1,
        "items": [{"pt name": "Ada"}, 12], "nested": {"service dt": 2}}, context, result)
    assert len(result.warnings) == 3
    assert all("confidence" in warning for warning in result.warnings)


def test_basic_key_normalization_preserves_meaning():
    rules = (AbbreviationRule("id", "identifier", 0.8), AbbreviationRule("id", "identity", 0.95))
    result = normalize_key("PatientID # co-pay co-insurance", rules, ())
    assert "copay" in result.tokens and "coinsurance" in result.tokens
    assert normalize_key("id", rules, ()).text == "identity"
    assert key_variants("") == [""]
    assert text_similarity("", "word") == 0
