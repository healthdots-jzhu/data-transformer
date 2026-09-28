"""Configuration contracts: reject malformed schemas and resolve file inheritance."""
import builtins
import json

import pytest

from data_transformer import ConfigError, build_schema_config, load_schema_config, load_schema_configs


def sample_configuration(**overrides):
    return {"schema_name": "sample", "document_fields": {"label": {"validator": "text"}}, **overrides}


@pytest.mark.parametrize("override,message", [
    ({"typo": 1}, "unknown key"), ({"source_formats": []}, "source_formats"),
    ({"source_formats": ["pdf"]}, "source_formats"), ({"csv": {"layout": "wrong"}}, "layout"),
    ({"settings": {"typo": 1}}, "unknown key"), ({"output": {"typo": 1}}, "unknown key"),
    ({"languages": []}, "must not be empty"), ({"languages": ["English"]}, "language code"),
    ({"default_language": "*"}, "language code"), ({"languages": [12]}, "language code"),
    ({"i18n": []}, "must be an object"), ({"i18n": {"fr": []}}, "must be an object"),
    ({"i18n": {"fr": {"typo": 1}}}, "unknown key"),
    ({"stopwords": "the"}, "must be a list"),
    ({"document_fields": {"label": "text"}}, "must be an object"),
    ({"document_fields": {"label": {}}}, "missing 'validator'"),
    ({"document_fields": {"label": {"validator": "absent"}}}, "unknown validator"),
    ({"document_fields": {"label": {"validator": "text", "aliases": "label"}}}, "must be a list"),
    ({"abbreviation_rules": [{"abbreviation": "qty"}]}, "missing"),
    ({"abbreviation_rules": [{"abbreviation": "qty", "expansion": "quantity", "confidence": 2}]}, "confidence"),
    ({"target_schema": {"type": "not-a-type"}}, "not a valid JSON Schema"),
    ({"settings": {"number_locale": "bad"}}, "number_locale"),
    ({"settings": {"ambiguous_number_policy": "bad"}}, "ambiguous_number_policy"),
    ({"line_containers": ["items"]}, "no 'line_fields'"),
    ({"csv": {"layout": "lines"}}, "requires 'line_fields'"),
    ({"document_fields": {}}, "no fields"),
    ({"document_fields": {"code": {"validator": "regex", "params": {"patterns": ["["]}}}}, "bad regex"),
    ({"document_fields": {"type": {"validator": "vocabulary"}}}, "unknown vocabulary"),
    ({"document_fields": {"price": {"validator": "money", "params": {"limit": "absent"}}}}, "not defined"),
    ({"limits": {"price": [1]}}, "expected"),
    ({"rules": [{"kind": "absent"}]}, "unknown rule kind"),
    ({"rules": [{"kind": "rate_limit"}]}, "missing"),
    ({"rules": [{"kind": "custom", "name": "unregistered"}]}, "not registered"),
    ({"rules": [{"kind": "max_by_vocabulary", "amount_field": "price", "type_field": "type",
                  "vocabulary": "absent", "attribute": "max", "default": 10}]}, "unknown vocabulary"),
])
def test_invalid_configuration_fails_before_processing(override, message):
    with pytest.raises(ConfigError, match=message):
        build_schema_config(sample_configuration(**override))


@pytest.mark.parametrize("raw,message", [([], "object/dict"), ({}, "schema_name")])
def test_configuration_requires_named_object(raw, message):
    with pytest.raises(ConfigError, match=message):
        build_schema_config(raw)


def test_inheritance_merges_objects_replaces_lists_and_removes_fields(tmp_path):
    parent = sample_configuration(document_fields={"label": {"validator": "text", "aliases": ["old"]},
                                                    "removed": {"validator": "text"}})
    (tmp_path / "_base.json").write_text(json.dumps(parent), encoding="utf-8")
    (tmp_path / "child.json").write_text(json.dumps({"extends": ["_base.json"],
        "document_fields": {"label": {"aliases": ["new"]}, "removed": None, "_comment": "ignored"}}), encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("ignored", encoding="utf-8")
    loaded = load_schema_configs(tmp_path)
    assert len(loaded) == 1
    assert [field.name for field in loaded[0].document_fields] == ["label"]
    assert loaded[0].document_fields[0].aliases["en"] == ["new"]
    assert loaded[0].source_path == str((tmp_path / "child.json").resolve())
    assert load_schema_configs(loaded[0])[0] is loaded[0]
    with pytest.raises(ConfigError, match="duplicate schema names"):
        load_schema_configs([loaded[0], loaded[0]])
    with pytest.raises(ConfigError, match="no schema configuration"):
        load_schema_configs([])


def test_python_config_and_non_dictionary_rejection(tmp_path):
    path = tmp_path / "schema.py"
    path.write_text("CONFIG = " + repr(sample_configuration()), encoding="utf-8")
    assert load_schema_configs(str(path))[0].name == "sample"
    path.write_text("CONFIG = []", encoding="utf-8")
    with pytest.raises(ConfigError, match="object/dict"):
        load_schema_config(path)


@pytest.mark.parametrize("filename,content,message", [
    ("bad.json", "{", "cannot parse"), ("list.json", "[]", "object/dict"),
    ("bad.txt", "{}", "unsupported config type"),
    ("cycle.json", '{"extends": ["cycle.json"]}', "circular"),
])
def test_config_file_errors(tmp_path, filename, content, message):
    path = tmp_path / filename
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_schema_config(path)


def test_yaml_dependency_is_explicit(tmp_path, monkeypatch):
    path = tmp_path / "schema.yml"
    path.write_text("schema_name: example", encoding="utf-8")
    original_import = builtins.__import__

    def unavailable_yaml(name, *arguments, **keywords):
        if name == "yaml":
            raise ImportError("dependency unavailable")
        return original_import(name, *arguments, **keywords)

    monkeypatch.setattr(builtins, "__import__", unavailable_yaml)
    with pytest.raises(ConfigError, match="PyYAML is required"):
        load_schema_config(path)


def test_language_overlays_tags_comments_and_vocabulary_metadata(context_factory):
    context = context_factory(default_language="fr", languages=["fr", "en"],
        language_tags={"fr": ["francophone"]},
        document_fields={"label": {"validator": "text", "i18n": {"*": {"aliases": ["universal"]}}}},
        i18n={"_comment": "ignored", "en": {"owner_role_keywords": {"provider": ["doctor"]}}},
        vocabularies={"_comment": "ignored", "types": {"_comment": "ignored", "therapy": None}})
    assert context.language_tag_map["francophone"] == "fr"
    assert context.owner_roles["provider"] == {"doctor"}
    assert context.vocabulary("types") == {"therapy": ["therapy"]}
    assert ("universal", "*") in context.aliases(context.cfg.document_fields[0])
    assert context.cfg.document_spec("missing") is None
