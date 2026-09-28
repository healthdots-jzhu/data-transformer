"""Validate domain values at their boundaries; compare errors as well as outputs."""
from decimal import Decimal

import pytest

from data_transformer.models import FieldSpec
from data_transformer.registry import FIELD_VALIDATORS
from data_transformer.validators import describe


@pytest.mark.parametrize("validator,raw,parameters,error", [
    ("text", 12, {}, "must be text"), ("text", "", {}, "too short"),
    ("text", "abcd", {"max_length": 3}, "too long"),
    ("person_name", None, {}, "must be text"), ("person_name", "Jane123", {}, "not valid"),
    ("identifier", True, {}, "string or an integer"), ("identifier", "@bad", {}, "1-40"),
    ("address", False, {}, "must be text"), ("address", "12345678", {}, "valid address"),
    ("phone", False, {}, "text or an integer"), ("phone", "416hello", {}, "invalid characters"),
    ("phone", "123", {}, "10-15"), ("date", "bad", {}, "recognisable date"),
    ("date", "2027-01-01", {}, "future"), ("date", "2000-01-01", {}, "older than"),
    ("money", "bad", {}, "numeric amount"), ("money", "-12", {}, "negative"),
    ("money", 1, {"limit": "range"}, "below the minimum"),
    ("money", 101, {"limit": "range"}, "exceeds"),
    ("number", "bad", {}, "not numeric"), ("number", 1.5, {"integer": True}, "whole number"),
    ("number", -1, {}, "outside"), ("number", 101, {"maximum": 100}, "outside"),
    ("regex", [], {}, "text or an integer"), ("regex", "bad", {}, "expected format"),
    ("regex", "AA", {"patterns": ["[A-Z]{2}"], "unique_chars": True}, "expected format"),
    ("regex", "12", {"hints": [{"pattern": "[0-9]+", "message": "digits forbidden"}]}, "digits forbidden"),
])
def test_rejected_field_values(context_factory, validator, raw, parameters, error):
    context = context_factory(limits={"range": [2, 100]}).for_document({"en"})
    outcome = FIELD_VALIDATORS[validator](raw, FieldSpec("value", validator, params=parameters), context)
    assert error in outcome.error
    assert outcome.value is None


@pytest.mark.parametrize("validator,raw,parameters,expected,warning", [
    ("text", " abc ", {}, "abc", None),
    ("person_name", "Élodie Tremblay", {}, "Élodie Tremblay", None),
    ("person_name", "Jane", {}, "Jane", "single name"),
    ("identifier", "001-AB", {}, "001-AB", None), ("identifier", 123, {}, "123", None),
    ("address", "Main Street", {}, "Main Street", "no street number"),
    ("phone", "(416) 555-0199 poste 12", {}, "4165550199 x12", None),
    ("date", "2027-01-01", {"allow_future": True}, "2027-01-01", None),
    ("date", "1990-01-01", {"max_age_days": None}, "1990-01-01", None),
    ("money", "12.345", {}, 12.35, "rounded"),
    ("number", "1,000", {"integer": True}, 1000, None),
    ("number", Decimal("1.25"), {}, 1.25, None),
    ("regex", "ab-12", {"strip": "-", "upper": True, "patterns": ["[A-Z]{2}[0-9]{2}"]}, "AB12", None),
])
def test_normalized_field_values(context_factory, validator, raw, parameters, expected, warning):
    context = context_factory().for_document({"en"})
    outcome = FIELD_VALIDATORS[validator](raw, FieldSpec("value", validator, params=parameters), context)
    assert outcome.error is None
    assert outcome.value == expected
    if warning:
        assert any(warning in message for message in outcome.warnings)


@pytest.mark.parametrize("validator", ["money", "number"])
def test_mixed_number_policy_applies_to_each_numeric_validator(context_factory, validator):
    context = context_factory(settings={"number_locale": "mixed", "ambiguous_number_policy": "french"})
    outcome = FIELD_VALIDATORS[validator]("1,234", FieldSpec("value", validator), context)
    assert outcome.error is None and any("ambiguous" in message for message in outcome.warnings)
    context.settings["ambiguous_number_policy"] = "error"
    assert "ambiguous" in FIELD_VALIDATORS[validator]("1,234", FieldSpec("value", validator), context).error


@pytest.mark.parametrize("raw,strict,expected,error", [
    (12, False, None, "must be text"), ("", False, None, "empty"),
    ("massothérapeute", False, "massage", None),
    ("registered massage therapist", False, "massage", None),
    ("unlisted", False, "unlisted", None), ("unlisted", True, None, "not a recognised"),
])
def test_controlled_vocabulary(context_factory, raw, strict, expected, error):
    context = context_factory(vocabularies={"practitioners": {
        "massage": {"aliases": ["massage therapist"], "i18n": {"fr": {"aliases": ["massothérapeute"]}}}}})
    specification = FieldSpec("type", "vocabulary", params={"vocabulary": "practitioners", "strict": strict})
    outcome = FIELD_VALIDATORS["vocabulary"](raw, specification, context)
    if error:
        assert error in outcome.error
    else:
        assert outcome.error is None and outcome.value == expected
        if raw == "unlisted":
            assert outcome.warnings


def test_huge_integer_description_does_not_raise():
    assert "exceeds" in describe(10**5000)
