"""Regression checks for Solution 12's optional total cross-check behavior.

Summable line fields are useful even when the source schema has no total field.
When a total is configured and supplied, the normal consistency check still runs.
"""

import pytest

from data_transformer import transform


@pytest.mark.parametrize(
    "total_configuration, submitted_total, expected_valid",
    [
        ({}, None, True),
        ({"total_field": None}, None, True),
        ({"document_fields": {"total_amount": {"validator": "money", "required": False}}}, None, True),
        ({"document_fields": {"total_amount": {"validator": "money", "required": False}}}, 30, True),
        ({"document_fields": {"total_amount": {"validator": "money", "required": False}}}, 31, False),
        ({"document_fields": {"total_amount": {"validator": "money"}}}, None, False),
    ],
    ids=["no-total-field", "disabled-total", "optional-total-absent", "matching-total",
         "mismatching-total", "required-total-absent"],
)
def test_line_sum_does_not_require_a_total(total_configuration, submitted_total, expected_valid):
    """Absence is allowed only when the schema does not require a total."""
    configuration = {
        "schema_name": "sale",
        "line_fields": {
            "price": {"validator": "money", "signature": True, "sums_to_total": True}
        },
        **total_configuration,
    }
    source = {"items": [{"price": 10}, {"price": 20}]}
    if submitted_total is not None:
        source["total_amount"] = submitted_total

    report = transform(source, configuration)

    assert report.is_valid is expected_valid, report.to_dict()
    assert report.results, report.to_dict()
    assert [line["price"] for line in report.results[0].normalized["lines"]] == [10, 20]
    if not expected_valid:
        assert report.results[0].errors, report.to_dict()
