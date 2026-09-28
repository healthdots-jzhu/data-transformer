from datetime import date
from pathlib import Path

import pytest

from data_transformer import ConfigError, build_schema_config, transform

CONFIGS = Path(__file__).resolve().parent.parent / "configs"
TODAY = date(2025, 6, 1)


def _vision(kind):
    return {"claim type": kind, "patient name": "Jane Roe", "provider address": "12 Main St, Toronto",
            "provider phone": "416-555-0100", "store name": "Optix",
            "products": [{"product name": "Frame", "price": 200, "date of purchase": "2025-05-01"}]}


# ---- earlier behaviour ------------------------------------------------------------------------------
def test_document_only_schema_needs_no_line_items():
    report = transform({"Full Name": "Ada Lovelace", "DOB": "1990-12-10", "Email": "ada@example.com"},
                       CONFIGS / "person.json", today=TODAY)
    assert report.is_valid, report.to_dict()
    normalized = report.results[0].normalized
    assert "lines" not in normalized
    assert normalized["fields"]["email"] == "ada@example.com"


def test_csv_duplicate_columns_are_rejected():
    report = transform("full name,email,email\nAda Lovelace,a@x.com,b@x.com\n",
                       CONFIGS / "person.json", source_format="csv", csv_delimiter=",")
    assert not report.is_valid and "duplicate column 'email'" in report.errors[0]


def test_csv_values_without_header_are_rejected():
    report = transform("full name,email\nAda Lovelace,a@x.com,EXTRA\n",
                       CONFIGS / "person.json", source_format="csv", csv_delimiter=",")
    assert not report.is_valid and "beyond the 2 header column(s)" in report.errors[0]


def test_valid_vision_claim_passes():
    report = transform(_vision("vision"), CONFIGS / "vision.json", today=TODAY)
    assert report.is_valid, report.to_dict()
    assert report.results[0].normalized["lines"][0]["price"] == 200.0


def test_vision_and_wellness_mix_is_an_error_with_only_vision_config():
    report = transform([_vision("vision"), _vision("wellness")], CONFIGS / "vision.json", today=TODAY)
    assert not report.is_valid and not report.results
    assert "wellness" in report.errors[0]


def test_vision_and_wellness_mix_is_an_error_with_both_configs():
    report = transform([_vision("vision"), _vision("wellness")],
                       [CONFIGS / "vision.json", CONFIGS / "wellness.json"], today=TODAY)
    assert not report.results and "mixes records of different schemas" in report.errors[0]


def test_target_schema_without_jsonschema_fails_loudly(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "jsonschema":
            raise ImportError("blocked for test")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ConfigError, match="jsonschema"):
        build_schema_config({"schema_name": "t", "document_fields": {"a": {"validator": "text"}},
                             "target_schema": {"type": "object"}})


def test_sums_to_total_without_total_field_is_allowed():
    cfg = build_schema_config({
        "schema_name": "t", "total_field": None,
        "document_fields": {"a": {"validator": "text"}},
        "line_fields": {"amt": {"validator": "money", "sums_to_total": true_value()}}})
    assert cfg.has_lines


def true_value():
    return True


# ---- French / bilingual -----------------------------------------------------------------------------
def test_french_only_person_record():
    report = transform({"Nom complet": "Élodie Tremblay", "Date de naissance": "12 mars 1990",
                        "Courriel": "elodie@exemple.ca"}, CONFIGS / "person.json", today=TODAY)
    assert report.is_valid, report.to_dict()
    fields = report.results[0].normalized["fields"]
    assert fields["full_name"] == "Élodie Tremblay" and fields["date_of_birth"] == "1990-03-12"
    assert report.results[0].languages_detected == ["fr"]


def test_bilingual_combined_headers():
    report = transform({"Full name / Nom complet": "Ada Lovelace",
                        "Date of birth / Date de naissance": "1990-12-10",
                        "Email | Courriel": "ada@example.com"}, CONFIGS / "person.json", today=TODAY)
    assert report.is_valid, report.to_dict()


def test_french_vision_claim_with_decimal_comma_and_french_date():
    claim = {"type de réclamation": "Vision", "nom du patient": "Marie Gagnon",
             "adresse du fournisseur": "123, rue Principale, Montréal",
             "téléphone du fournisseur": "514 555-0100", "nom du magasin": "Optique Plus",
             "produits": [{"nom du produit": "Monture", "prix": "249,99 $", "date d'achat": "14 mai 2025"}]}
    report = transform(claim, CONFIGS / "vision.json", today=TODAY)
    assert report.is_valid, report.to_dict()
    line = report.results[0].normalized["lines"][0]
    assert line["price"] == 249.99 and line["product_name"] == "Monture"
    assert line["date_of_purchase"] == "2025-05-14"
    assert "fr" in report.results[0].languages_detected


def test_translatable_field_in_two_languages_is_not_a_conflict():
    claim = _vision("vision")
    claim["products"][0]["nom du produit"] = "Monture"
    report = transform(claim, CONFIGS / "vision.json", today=TODAY)
    assert report.is_valid, report.to_dict()
    result = report.results[0]
    assert result.normalized["lines"][0]["product_name"] == "Frame"        # preferred language: en
    assert result.translations["line 1 > product_name"] == {"en": "Frame", "fr": "Monture"}


def test_non_translatable_field_in_two_languages_with_different_values_conflicts():
    claim = _vision("vision")
    claim["nom du patient"] = "Jeanne Roe"
    report = transform(claim, CONFIGS / "vision.json", today=TODAY)
    assert not report.is_valid and any("conflicting values for 'patient_name'" in e
                                       for e in report.results[0].errors)


def test_windows_1252_csv_with_semicolon_delimiter():
    data = "nom complet;date de naissance;courriel\nÉlodie Tremblay;12 mars 1990;e@x.ca\n".encode("cp1252")
    report = transform(data, CONFIGS / "person.json", source_format="csv", today=TODAY)
    assert report.is_valid, report.to_dict()
    assert any("Windows-1252" in w for w in report.warnings)


def test_languages_parameter_restricts_matching():
    # English keys are ignored when only French is accepted
    report = transform({"Full Name": "Ada Lovelace", "DOB": "1990-12-10", "Email": "ada@example.com"},
                       CONFIGS / "person.json", languages=["fr"], today=TODAY)
    assert not report.is_valid


from decimal import Decimal
from data_transformer.value_parsers import parse_date, parse_money, parse_number

def _french_vision(price="249,99 $"):
    return {"type de réclamation": "Vision", "nom du patient": "Marie Gagnon",
            "adresse du fournisseur": "123, rue Principale, Montréal",
            "téléphone du fournisseur": "514 555-0100", "nom du magasin": "Optique Plus",
            "produits": [{"nom du produit": "Monture", "prix": price, "date d'achat": "14 mai 2025"}]}


# ---- 1. bilingual labels must agree ------------------------------------------------------------------------------
def test_bilingual_label_with_a_disqualifying_english_part_is_not_a_price():
    claim = _vision("vision")
    claim["products"][0].pop("price")
    claim["products"][0]["min amount / montant"] = 200
    report = transform(claim, CONFIGS / "vision.json", today=TODAY)
    result = report.results[0]
    assert not result.is_valid
    assert any("required field 'price' is missing" in e for e in result.errors)
    assert any("'min amount / montant' was not used" in w and "do not all mean 'price'" in w
               for w in result.warnings)
    assert "price" not in result.normalized["lines"][0] or result.normalized["lines"][0]["price"] is None


def test_bilingual_label_with_consistent_parts_maps_and_detects_both_languages():
    claim = _vision("vision")
    claim["products"][0].pop("price")
    claim["products"][0]["Price / Prix"] = 200
    report = transform(claim, CONFIGS / "vision.json", today=TODAY)
    assert report.is_valid, report.to_dict()
    assert report.results[0].normalized["lines"][0]["price"] == 200.0
    assert {"en", "fr"} <= set(report.results[0].languages_detected)


# ---- 2. number locale ---------------------------------------------------------------------------------------------------
def test_number_parsers_follow_the_locale():
    assert parse_money("1,234", "en").value == Decimal("1234")
    assert parse_money("1,234", "fr").value == Decimal("1.234")          # decimal comma first
    assert parse_money("1,234.56", "fr").value is None                   # comma-thousands does not exist in fr
    assert parse_money("1 234,56 $", "fr").value == Decimal("1234.56")
    assert parse_money("1 234,56", "en").value is None                   # spaces are not English thousands
    assert parse_money("1,234.56", "mixed").value == Decimal("1234.56")
    assert parse_money("12,5", "mixed").value == Decimal("12.5")
    assert parse_money("1,234", "mixed").error                           # genuinely ambiguous
    english = parse_money("1,234", "mixed", "english")
    assert english.value == Decimal("1234") and english.note
    assert parse_money("1,234", "mixed", "french").value == Decimal("1.234")


def test_numbers_and_amounts_share_the_same_rules():
    for text, locale in [("1,234", "en"), ("1,234", "fr"), ("12,5", "fr"), ("1 000", "fr"), ("1 000", "en")]:
        assert parse_number(text, locale).value == parse_money(text, locale).value


def test_document_language_selects_the_number_format():
    english = _vision("vision")
    english["products"][0]["price"] = "1,234"
    r = transform(english, CONFIGS / "vision.json", today=TODAY).results[0]
    assert r.is_valid and r.number_locale == "en" and r.normalized["lines"][0]["price"] == 1234.0

    r = transform(_french_vision("1,234"), CONFIGS / "vision.json", today=TODAY).results[0]
    assert r.is_valid and r.number_locale == "fr" and r.normalized["lines"][0]["price"] == 1.23


def test_bilingual_document_with_an_ambiguous_amount_is_an_error_unless_a_policy_is_set():
    claim = _vision("vision")
    claim["products"][0]["price"] = "1,234"
    claim["adresse du patient"] = "45 rue Laval, Montréal"        # one French key -> both languages present
    r = transform(claim, CONFIGS / "vision.json", today=TODAY).results[0]
    assert r.number_locale == "mixed" and not r.is_valid
    assert any("ambiguous" in e for e in r.errors)

    r = transform(claim, CONFIGS / "vision.json", today=TODAY,
                  settings={"ambiguous_number_policy": "english"}).results[0]
    assert r.is_valid and r.normalized["lines"][0]["price"] == 1234.0


# ---- 3. French times 20h-23h ---------------------------------------------------------------------------------------------
@pytest.mark.parametrize("suffix", ["à 20 h", "à 21h30", "à 22 h 05", "à 23 h 59", "à 23h", "a 00 h 00",
                                    "à 7 h 05", "à 14 h 30.", "à 19 h"])
def test_french_time_of_day_is_ignored_for_every_hour(suffix):
    parsed = parse_date(f"14 mai 2025 {suffix}", True, {"fr"})
    assert parsed is not None and parsed.value == date(2025, 5, 14)
