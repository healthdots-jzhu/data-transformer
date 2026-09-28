"""End-to-end documents, line containers, dataset selection and command-line reports."""
import json
import runpy
import sys
from datetime import date

import pytest

from data_transformer import ConfigError, transform
from data_transformer.__main__ import main

TODAY = date(2026, 9, 28)


def sale_configuration(**overrides):
    return {
        "schema_name": "sale", "languages": ["en", "fr"], "line_containers": ["purchases"],
        "discriminator": {"aliases": ["document kind"], "values": ["sale"]},
        "document_fields": {
            "buyer": {"validator": "text", "owner": "patient"},
            "provider": {"validator": "text", "required": False},
            "total_amount": {"validator": "money", "required": False},
            "reference": {"validator": "identifier", "required": False, "warn_if_missing": True},
        },
        "line_fields": {
            "product": {"validator": "text", "signature": True},
            "price": {"validator": "money", "signature": True, "sums_to_total": True,
                      "total_equivalent": True, "weak_aliases": ["amount"]},
            "date": {"validator": "date", "required": False, "inherit": True},
            "note": {"validator": "text", "required": False, "warn_if_missing": True},
        }, **overrides,
    }


def sale_data(**overrides):
    return {"buyer": "Ada", "purchases": [{"product": "Frame", "price": 10}], **overrides}


def process(source, configuration=None, **options):
    return transform(source, configuration or sale_configuration(), today=TODAY, schema="sale", **options)


@pytest.mark.parametrize("source,error", [
    (sale_data(purchases=[]), "empty"),
    (sale_data(purchases="not an array"), "must be an array"),
    (sale_data(items=[{"product": "Lens", "price": 3}]), "multiple line-item containers"),
    (sale_data(purchases=[12]), "must be an object"),
    (sale_data(purchases=[{"product": "Frame", "nested.price": 10, "nested": {"price": 20}}]), "conflicting flattened"),
    (sale_data(purchases=[{"product": "Frame"}]), "required field 'price' is missing"),
    ({"buyer": "Ada"}, "no line items"),
    (sale_data(buyer=""), "buyer ('buyer') is empty"),
])
def test_invalid_line_structures_and_required_values(source, error):
    report = process(source)
    assert not report.is_valid
    assert any(error in message for message in report.results[0].errors)


def test_flat_document_can_represent_one_line():
    report = process({"buyer": "Ada", "product": "Frame", "price": 10, "reference": None})
    assert report.is_valid, report.to_dict()
    assert report.results[0].normalized["lines"][0]["price"] == 10
    assert any("note" in warning for warning in report.results[0].warnings)


@pytest.mark.parametrize("total,expected_valid", [(10, True), (11, False)])
def test_opt_in_root_amount_is_total_and_never_inherited(total, expected_valid):
    report = process(sale_data(price=total), settings={"interpret_root_amount_as_total": True})
    result = report.results[0]
    assert report.is_valid is expected_valid
    assert result.normalized["fields"]["total_amount"] == total
    assert result.normalized["lines"][0]["price"] == 10
    assert any("not applied to individual lines" in warning for warning in result.warnings)


def test_root_amount_conflict_with_declared_total_is_reported():
    report = process(sale_data(price=11, total_amount=10), settings={"interpret_root_amount_as_total": True})
    assert not report.is_valid
    assert any("conflicting values" in error for error in report.results[0].errors)


def test_root_amount_does_not_fill_missing_line_amount_by_default():
    report = process(sale_data(price=100, purchases=[{"product": "Frame"}]))
    assert not report.is_valid
    assert "price" not in report.results[0].normalized["lines"][0]
    assert any("not applied" in warning for warning in report.results[0].warnings)


def test_opt_in_inheritance_keeps_explicit_line_dates():
    report = process(sale_data(date="2026-05-01", purchases=[
        {"product": "Frame", "price": 10}, {"product": "Lens", "price": 20, "date": "2026-06-01"}]),
        settings={"inherit_shared_fields": True})
    assert report.is_valid, report.to_dict()
    assert [line["date"] for line in report.results[0].normalized["lines"]] == ["2026-05-01", "2026-06-01"]
    assert sum("inherited" in message for message in report.results[0].warnings) == 1


def test_explicit_provider_overrides_document_value():
    report = process(sale_data(provider="Shared", purchases=[{"product": "Frame", "price": 10, "provider": "Local"}]))
    assert report.is_valid
    assert report.results[0].normalized["lines"][0]["provider"] == "Local"
    assert any("overrides" in warning for warning in report.results[0].warnings)


def test_required_document_field_can_be_supplied_on_every_line():
    configuration = sale_configuration()
    configuration["document_fields"]["provider"]["required"] = True
    report = process(sale_data(purchases=[{"product": "Frame", "price": 10, "provider": "Local"}]), configuration)
    assert report.is_valid, report.to_dict()


def test_total_as_amount_is_only_enabled_for_single_line():
    report = process({"buyer": "Ada", "product": "Frame", "total_amount": 10},
                     settings={"single_line_total_as_amount": True})
    assert report.is_valid
    assert report.results[0].normalized["lines"][0]["price"] == 10
    assert any("single line" in warning for warning in report.results[0].warnings)


@pytest.mark.parametrize("source,message", [
    ([42], "must be an object"), ([], "no usable records"),
    ([{"buyer": "Ada", 1: "bad"}], "field names must be strings"),
    ([{"a.b": 1, "a": {"b": 2}}], "conflicting flattened"),
    ("{bad}", "not valid JSON"),
])
def test_dataset_errors(source, message):
    report = process(source)
    assert not report.is_valid and not report.results
    assert any(message in error for error in report.errors)


def test_bad_record_does_not_disappear_from_mixed_batch():
    report = process([sale_data(), 42])
    assert not report.is_valid
    assert len(report.results) == 2 and report.results[0].is_valid
    assert report.output == [report.results[0].normalized]
    assert report.results[1].record_index == 2


def test_unknown_schema_language_and_disallowed_source_format():
    with pytest.raises(ConfigError, match="unknown schema"):
        transform({}, sale_configuration(), schema="missing")
    with pytest.raises(ConfigError, match="does not enable"):
        transform({}, sale_configuration(), languages=["de"])
    report = process(sale_data(), sale_configuration(source_formats=["xml"]))
    assert not report.is_valid and "no configured schema accepts" in report.errors[0]


def test_schema_selection_rejects_ties_low_scores_and_unknown_types():
    first = sale_configuration()
    second = sale_configuration(schema_name="other")
    report = transform(sale_data(), [first, second], today=TODAY)
    assert "ambiguous between" in report.errors[0]
    report = transform({"unrelated": "data"}, first)
    assert "no configured schema is similar enough" in report.errors[0]
    report = transform(sale_data(**{"document kind": "sale"}), [first, second])
    assert "record type matches several schemas" in report.errors[0]
    report = transform(sale_data(**{"document kind": "unrecognized"}), first)
    assert "matches no candidate schema" in report.errors[0]


def test_outlier_without_type_is_rejected_individually():
    report = transform([sale_data(**{"document kind": "sale"}), {"unrelated": "data"}], sale_configuration())
    assert len(report.results) == 2
    assert report.results[0].is_valid and not report.results[1].is_valid
    assert "does not fit schema" in report.results[1].errors[0]


@pytest.mark.parametrize("providers", [("Shared", "Shared"), ("First", "Second")])
def test_csv_lines_hoists_only_identical_document_fields(providers):
    csv = "document kind,buyer,provider,product,price\nsale,Ada," + providers[0] + ",Frame,10\nsale,Ada," + providers[1] + ",Lens,20\n"
    report = transform(csv, sale_configuration(csv={"layout": "lines"}), source_format="csv", today=TODAY)
    assert report.is_valid, report.to_dict()
    result = report.results[0]
    assert len(result.normalized["lines"]) == 2
    assert result.normalized["fields"]["buyer"] == "Ada"
    if providers[0] == providers[1]:
        assert result.normalized["fields"]["provider"] == "Shared"
    else:
        assert [line["provider"] for line in result.normalized["lines"]] == list(providers)


def test_csv_blank_line_values_are_validated_after_combining_rows():
    # A blank weak type is ignored during schema selection, but required line
    # values must still be checked when the rows become one document.
    configuration = sale_configuration(csv={"layout": "lines"},
        discriminator={"weak_aliases": ["type"], "values": ["sale"]})
    report = transform("type,buyer,product,price\nsale,Ada,Frame,10\n,Ada,,\n", configuration, source_format="csv")
    # Empty values still have a schema shape, so a record-level validation error is expected.
    assert not report.is_valid
    assert any("empty" in message for result in report.results for message in result.errors)


def test_cli_exit_codes_and_json_output(tmp_path, capsys):
    configuration = tmp_path / "schema.json"
    configuration.write_text(json.dumps(sale_configuration()), encoding="utf-8")
    source = tmp_path / "source.json"
    source.write_text(json.dumps(sale_data()), encoding="utf-8")
    assert main([str(source), "--config", str(configuration), "--languages", "en,fr", "--today", "2026-09-28"]) == 0
    assert json.loads(capsys.readouterr().out)["is_valid"]
    source.write_text("{bad}", encoding="utf-8")
    assert main([str(source), "--config", str(configuration)]) == 1
    assert not json.loads(capsys.readouterr().out)["is_valid"]
    configuration.write_text("{}", encoding="utf-8")
    assert main([str(source), "--config", str(configuration)]) == 2
    assert "configuration error" in capsys.readouterr().err


def test_module_entry_point_exits_successfully(tmp_path, monkeypatch, capsys):
    # run_module executes the actual __main__ guard under coverage without relying on
    # subprocess coverage configuration or a second interpreter's environment.
    configuration = tmp_path / "schema.json"
    configuration.write_text(json.dumps(sale_configuration()), encoding="utf-8")
    source = tmp_path / "source.json"
    source.write_text(json.dumps(sale_data()), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["data-transformer", str(source), "--config", str(configuration)])
    monkeypatch.delitem(sys.modules, "data_transformer.__main__", raising=False)
    with pytest.raises(SystemExit) as exit_info:
        runpy.run_module("data_transformer", run_name="__main__", alter_sys=True)
    assert exit_info.value.code == 0
    assert json.loads(capsys.readouterr().out)["is_valid"]
