"""Number-format, sign, calendar and type boundaries, independent of field mapping."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from data_transformer.value_parsers import parse_date, parse_money, parse_number


@pytest.mark.parametrize("parser", [parse_money, parse_number])
@pytest.mark.parametrize("raw,locale,expected", [
    (12, "fr", "12"), (1.25, "en", "1.25"), (Decimal("2.30"), "fr", "2.30"),
    ("12.5", "fr", "12.5"), ("1.234,56", "fr", "1234.56"),
    ("1\u202f234,56", "fr", "1234.56"), ("1\u00a0234", "fr", "1234"),
    ("1 234.56", "fr", "1234.56"), ("1,234", "en", "1234"),
    ("1,234", "fr", "1.234"), ("1,234.56", "mixed", "1234.56"),
    ("12,5", "mixed", "12.5"), ("12.50", "mixed", "12.50"),
    ("-12,5", "fr", "-12.5"), ("−12.5", "en", "-12.5"), ("+12.5", "en", "12.5"),
])
def test_numeric_values_preserve_magnitude_and_sign(parser, raw, locale, expected):
    result = parser(raw, locale)
    assert result.value == Decimal(expected)
    assert result.error is None


@pytest.mark.parametrize("parser", [parse_money, parse_number])
@pytest.mark.parametrize("raw", [True, None, {}, [], "", "oops", "1,,234", "1e3", float("inf"),
                                float("nan"), Decimal("NaN"), pytest.param(10**5000, id="huge-integer")])
def test_non_numbers_and_nonfinite_values_are_rejected(parser, raw):
    assert parser(raw, "mixed").value is None


@pytest.mark.parametrize("raw,expected", [("($12.50)", "-12.50"), ("12.50-", "-12.50"),
    ("CAD 12.50", "12.50"), ("12,50 $ CA", "12.50"), ("€12,50", "12.50"),
    ("£12.50", "12.50"), ("12 dollars", "12")])
def test_currency_and_accounting_signs(raw, expected):
    assert parse_money(raw, "mixed").value == Decimal(expected)


@pytest.mark.parametrize("raw", ["(-12)", "-12-", "(+12)", "--12", "(12)-"])
def test_multiple_money_signs_are_rejected(raw):
    assert parse_money(raw).value is None


@pytest.mark.parametrize("parser", [parse_money, parse_number])
@pytest.mark.parametrize("policy,expected", [("english", "1234"), ("french", "1.234")])
def test_ambiguous_policy_is_explicit_and_audited(parser, policy, expected):
    result = parser("1,234", "mixed", policy)
    assert result.value == Decimal(expected)
    assert "ambiguous" in result.note and f"read as {policy.capitalize()}" in result.note
    assert "ambiguous" in parser("1,234", "mixed").error


@pytest.mark.parametrize("raw,expected", [
    (datetime(2026, 5, 1, 23, 59), date(2026, 5, 1)), (date(2026, 5, 1), date(2026, 5, 1)),
    (20260501, date(2026, 5, 1)), ("May 1st, 2026", date(2026, 5, 1)),
    ("2026 May 1", date(2026, 5, 1)), ("2026 1 May", date(2026, 5, 1)),
    ("14/05/2026", date(2026, 5, 14)), ("05/14/2026", date(2026, 5, 14)),
    ("05/05/2026", date(2026, 5, 5)), ("1er mai 2026", date(2026, 5, 1)),
    ("2026-05-01T20:45:30Z", date(2026, 5, 1)), ("14 août 2026 à 23h59", date(2026, 8, 14)),
])
def test_supported_calendar_forms(raw, expected):
    assert parse_date(raw, languages={"en", "fr"}).value == expected


@pytest.mark.parametrize("raw", [True, None, [], pytest.param(10**5000, id="huge-integer"), "x" * 201, "May x 2026", "1 2026 May",
    "May 1 123", "14 05 123", "14 15 2026", "31 February 2026", "May June 2026", "2026-13-01"])
def test_invalid_calendar_values(raw):
    assert parse_date(raw) is None


@pytest.mark.parametrize("day_first,expected", [(True, date(2026, 4, 3)), (False, date(2026, 3, 4))])
def test_ambiguous_numeric_dates_and_short_years_report_assumptions(day_first, expected):
    result = parse_date("03/04/26", day_first=day_first)
    assert result.value == expected
    assert len(result.notes) == 2
    assert "ambiguous" in result.notes[0] and "two-digit year" in result.notes[1]


def test_unknown_language_pack_falls_back_to_english_months():
    assert parse_date("May 1 2026", languages={"zz"}).value == date(2026, 5, 1)
