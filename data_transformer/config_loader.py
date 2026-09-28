"""Loading and validating schema configuration files.

A CONFIG FILE DESCRIBES ONE TARGET SCHEMA (one data type). It may be JSON, a Python file defining a
dict named CONFIG, or YAML (needs PyYAML). Comments: any key starting with "_" is ignored
(use "_comment"). Unknown keys raise ConfigError so typos are never silent.

Segments of a config file
-------------------------
"extends": ["_common.json", "_common.fr.json"]
    Parent files (relative to this file) merged first. Dicts merge key by key; lists and scalars
    are replaced; `null` removes an inherited entry (e.g. "patient_phone": null). Files whose name
    starts with "_" are fragments: a directory scan does not treat them as schemas. LANGUAGE OVERLAYS
    are fragments too (e.g. "_dental.fr.json") that only add "i18n" blocks to fields defined elsewhere.

"schema_name": "dental"                                   REQUIRED
    Unique name. Appears in the output ("schema": "dental") and in similarity reports.

"source_formats": ["json", "xml", "csv"]
    Formats this schema accepts. Other formats exclude the schema from selection.

"csv": {"layout": "records" | "lines"}
    records (default): each CSV row is its own document.
    lines: all rows form ONE document; each row is a line item; columns that are constant across
           rows and map to document-level fields are hoisted (needs "line_fields").

"settings": {...}
    Overrides of defaults.DEFAULT_SETTINGS (thresholds, date policy, conflict policy, preferred_language).

LANGUAGE SEGMENTS
"languages": ["en", "fr"]
    Languages this schema accepts; order = preference (the first one wins for "translatable" fields
    unless settings.preferred_language says otherwise). Default ["en"]. A per-call
    transform(languages=[...]) may restrict this (e.g. ["fr"] = French-only data; English aliases
    are then ignored).
"default_language": "en"
    The language of every PLAIN list in the file (aliases, stopwords, ...). Default "en".
"language_tags": {"fr": ["fr", "fra"]}
    Extra tokens that tag a key with a language ("name_fr"). en/fr tags are built in.
"i18n": {"fr": {...}}
    Schema-wide lists for one language. Allowed keys, each a list of the same shape as the base key:
    stopwords, abbreviation_rules (each may also carry "language"), disqualifying_key_words,
    owner_role_keywords ({role: [words]}), neutral_container_words, line_containers,
    generic_aliases, generic_line_containers. Language "*" = active in every language.
    Field specs, the discriminator and vocabulary terms carry their OWN "i18n" block (see below).
    Built-in packs already supply French stop-words, generic words and language tags.
"generic_aliases", "generic_line_containers"
    Base lists (default language) of generic one-word names ("amount", "date") and of generic names
    for a line-item array ("lines", "items"). Defaults come from the built-in packs.

"stopwords": ["of","the",...]
    Words ignored when comparing keys (accents are folded, so "à" == "a").

"abbreviation_rules": [{"abbreviation","expansion","confidence", "previous":[], "following":[], "language"?}]
    Your abbreviation dictionary. Without previous/following the rule applies anywhere; with them
    only next to those words. Confidence below settings.abbreviation_confidence_without_warning
    (0.90) still expands but adds a warning and can never create a generic-alias match. Rules of all
    active languages apply together, so one abbreviation should not expand differently per language.

"disqualifying_key_words": ["max","tax","approved",...]
    A key containing one of these is a DIFFERENT quantity ("max amount" is not "amount") unless the
    alias itself contains the word.

"owner_role_keywords": {"patient": [...], "provider": [...]}
    Words that say whose value a key is. Fields declare their "owner"; a key naming the other party
    is refused.

"neutral_container_words": ["claim","request","data",...]
    Parent names that carry no meaning ("claim > patient > name"). Any other parent is unrecognised:
    its leaf is not trusted.

"discriminator": {"aliases": [...], "weak_aliases": [...], "values": [...], "i18n": {"fr": {...same keys}}}
    How an EXPLICIT type is recognised. aliases/weak_aliases = names of the type field
    ("claim type" / "type"); values = the ways this schema is named as a VALUE
    ("dental", "dental claim", "Dentaire"...). Bilingual values ("Dental / Dentaire") are understood.
    Optional: without it, only similarity selection is used.

"line_containers": ["procedures", "treatments"]
    Names of the array holding line items (in addition to generic_line_containers).
    Only allowed if "line_fields" is non-empty.

"limits": {"dentist_fee": [0.01, 5000]}
    Named [min, max] ranges referenced by money fields via params.limit. Sanity bounds, not
    benefit-plan rules.

"vocabularies": {"practitioner_types": {"massage therapist": {"aliases": [...], "max_amount": 300,
                                                              "i18n": {"fr": {"aliases": [...]}}}}}
    Controlled term lists for the "vocabulary" validator. The term itself is the canonical output
    value in every language; extra attributes (max_amount) can be read by rules.

"total_field": "total_amount"
    Name of the document-level field holding the document total. The line-sum cross-check runs only
    when this field exists, some line field has "sums_to_total", and the document states a total.

"document_fields": {name: FieldSpec}   and   "line_fields": {name: FieldSpec}
    The canonical fields. "document_fields" occur once per document; "line_fields" once per line item
    (omit the section entirely for schemas without line items). FieldSpec keys:
      validator        REQUIRED. Built-in (text, person_name, identifier, address, phone, date, money,
                       number, regex, vocabulary) or registered with @register_validator.
      params           Validator parameters (see validators.py).
      required         default true.
      aliases          Strong, descriptive source names (default language; fuzzy matched).
      weak_aliases     Generic names accepted only on exact match, with a warning.
      i18n             {"fr": {"aliases": [...], "weak_aliases": [...]}} names in other languages.
      translatable     true = free text that may be given in several languages at once; the preferred
                       language's value is output, the others are reported in result.translations.
      owner            "patient" / "provider" / any role in owner_role_keywords.
      explicit_owner   true = a bare key like "phone" is not enough; it must name the owner.
      signature        true = distinctive field used for schema similarity.
      warn_if_missing  optional field that deserves a warning when absent.
      inherit          (line field) may take a shared document-level value (settings.inherit_shared_fields).
      sums_to_total    (line field) adds up to the total_field (optional, see total_field).
      total_equivalent (line field) equals the total for one-line documents.
    Document fields that are not patient-owned and not the total may ALSO appear per line.

"rules": [ ... ]
    Declarative cross-field checks run after validation:
      {"kind": "max_by_vocabulary", "amount_field", "type_field", "vocabulary", "attribute",
       "default", "severity"?}   amount must not exceed vocabulary[type][attribute]
      {"kind": "rate_limit", "amount_field", "unit_field", "max_rate", "unit_label"?}   warning
      {"kind": "custom", "name": "<registered with @register_rule>"}

"output": {"type_key": "schema", "fields_key": "fields", "lines_key": "lines"}
    Names of the keys in the output JSON. (lines_key is omitted from output for line-less schemas.)

"target_schema": { ...JSON Schema... }
    OPTIONAL but enforced: the final output JSON (as shaped by "output") must validate against it.
    Requires the `jsonschema` package: if it is missing, or the schema itself is invalid, loading
    raises ConfigError. It runs only for records without earlier errors.
"""
from __future__ import annotations

import json
import re
import runpy
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from . import validators  # noqa: F401  (importing registers the built-in validators)
from .defaults import (BUILTIN_PACKS, DEFAULT_LANGUAGE, DEFAULT_LANGUAGE_TAGS, DEFAULT_LANGUAGES,
                       DEFAULT_OUTPUT, DEFAULT_SETTINGS)
from .errors import ConfigError
from .localization import Localized, has_entries
from .models import FieldSpec, SchemaConfig
from .normalization import AbbreviationRule
from .registry import FIELD_VALIDATORS, POST_RULES

_TOP_LEVEL_KEYS = {
    "schema_name", "source_formats", "csv", "settings", "stopwords", "abbreviation_rules",
    "disqualifying_key_words", "owner_role_keywords", "neutral_container_words", "limits",
    "vocabularies", "discriminator", "line_containers", "document_fields", "line_fields",
    "total_field", "rules", "output", "target_schema",
    "languages", "default_language", "language_tags", "i18n", "generic_aliases", "generic_line_containers",
}
_I18N_TOP_KEYS = {"stopwords", "abbreviation_rules", "disqualifying_key_words", "owner_role_keywords",
                  "neutral_container_words", "line_containers", "generic_aliases",
                  "generic_line_containers"}
_FIELD_KEYS = {"validator", "required", "params", "owner", "explicit_owner", "inherit",
               "warn_if_missing", "aliases", "weak_aliases", "signature", "sums_to_total",
               "total_equivalent", "translatable", "i18n"}
_FIELD_I18N_KEYS = {"aliases", "weak_aliases"}
_DISCRIMINATOR_I18N_KEYS = {"aliases", "weak_aliases", "values"}
_VOCAB_I18N_KEYS = {"aliases"}
_RULE_REQUIRED = {
    "max_by_vocabulary": ["amount_field", "type_field", "vocabulary", "attribute", "default"],
    "rate_limit": ["amount_field", "unit_field", "max_rate"],
    "custom": ["name"],
}
_LANG_CODE = re.compile(r"^(?:\*|[a-z]{2,3})$")


# ---------------------------------------------------------------------------
# reading files
# ---------------------------------------------------------------------------
def _merge(base: Dict[str, Any], extra: Dict[str, Any]) -> Dict[str, Any]:
    """Recursive merge: dicts merge key by key; lists, scalars and null REPLACE."""
    merged = dict(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merged[key] = _merge(base[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _read_config_file(path: Path) -> Dict[str, Any]:
    """Read one file (.json, .py with CONFIG, .yaml/.yml) into a dict."""
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            data = json.loads(path.read_text(encoding="utf-8"))
        elif suffix == ".py":
            data = runpy.run_path(str(path)).get("CONFIG")
        elif suffix in (".yaml", ".yml"):
            try:
                import yaml  # type: ignore
            except ImportError as error:
                raise ConfigError(f"{path}: PyYAML is required for YAML configs") from error
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        else:
            raise ConfigError(f"{path}: unsupported config type '{suffix}'")
    except ValueError as error:
        if isinstance(error, ConfigError):
            raise
        raise ConfigError(f"{path}: cannot parse: {error}") from error
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: a config must be an object/dict")
    return data


def _load_raw(path: Path, stack: Tuple[Path, ...] = ()) -> Dict[str, Any]:
    """Read a file and resolve its "extends" chain (parents first, child overrides). Detects cycles."""
    path = path.resolve()
    if path in stack:
        raise ConfigError(f"circular 'extends': {' -> '.join(map(str, stack + (path,)))}")
    raw = _read_config_file(path)
    merged: Dict[str, Any] = {}
    for parent in raw.get("extends", []):
        merged = _merge(merged, _load_raw(path.parent / parent, stack + (path,)))
    return _merge(merged, {k: v for k, v in raw.items() if k != "extends"})


# ---------------------------------------------------------------------------
# building the model
# ---------------------------------------------------------------------------
def _check_keys(section: Dict[str, Any], allowed: set, where: str) -> None:
    """Reject unknown keys (typo protection). Keys starting with '_' are comments."""
    unknown = [k for k in section if k not in allowed and not str(k).startswith("_")]
    if unknown:
        raise ConfigError(f"{where}: unknown key(s) {', '.join(map(str, unknown))} "
                          f"(allowed: {', '.join(sorted(allowed))})")


def _check_language_code(code: str, where: str, allow_wildcard: bool = False) -> None:
    """Language codes are 2-3 lower-case letters ('en', 'fr'); '*' (all languages) where allowed."""
    if not isinstance(code, str) or not _LANG_CODE.match(code) or (code == "*" and not allow_wildcard):
        raise ConfigError(f"{where}: '{code}' is not a valid language code (use e.g. 'en', 'fr')")


def _parse_i18n(block: Any, allowed: set, where: str) -> Dict[str, Dict[str, Any]]:
    """Validate an "i18n" block ({language: {key: value}}) and return it without comment keys."""
    if block is None:
        return {}
    if not isinstance(block, dict):
        raise ConfigError(f"{where}: 'i18n' must be an object keyed by language code")
    parsed = {}
    for lang, body in block.items():
        if str(lang).startswith("_"):
            continue
        _check_language_code(lang, f"{where}.{lang}", allow_wildcard=True)
        if not isinstance(body, dict):
            raise ConfigError(f"{where}.{lang}: must be an object")
        _check_keys(body, allowed, f"{where}.{lang}")
        parsed[lang] = body
    return parsed


def _field_localized(obj: Dict[str, Any], key: str, default_language: str, where: str,
                     allowed: set) -> Localized:
    """Localized list for `key` of a field/discriminator/vocabulary term: the plain list is the default
    language; obj["i18n"][lang][key] adds entries for other languages."""
    base = obj.get(key, [])
    if not isinstance(base, list):
        raise ConfigError(f"{where}.{key}: must be a list")
    out: Localized = {default_language: list(base)}
    for lang, block in _parse_i18n(obj.get("i18n"), allowed, f"{where}.i18n").items():
        out.setdefault(lang, []).extend(block.get(key, []))
    return out


def _localized_list(key: str, raw: Dict[str, Any], i18n: Dict[str, Dict[str, Any]],
                    default_language: str, where: str) -> Localized:
    """Localized schema-wide list. Sources, in order: the plain list (default language; when absent the
    built-in pack supplies it), the built-in packs of the other languages, then config i18n blocks."""
    base = raw.get(key)
    if base is not None and not isinstance(base, list):
        raise ConfigError(f"{where}.{key}: must be a list")
    pack_default = BUILTIN_PACKS.get(default_language, {}).get(key, [])
    out: Localized = {default_language: list(base) if base is not None else list(pack_default)}
    for lang, pack in BUILTIN_PACKS.items():
        if lang != default_language:
            out.setdefault(lang, []).extend(pack.get(key, []))
    for lang, block in i18n.items():
        out.setdefault(lang, []).extend(block.get(key, []))
    return out


def _build_specs(section: Optional[Dict[str, Any]], where: str, default_language: str) -> List[FieldSpec]:
    """Turn a {"field": {...}} block into FieldSpec objects, checking validator names. A null entry
    (used with "extends") removes an inherited field."""
    specs = []
    for name, body in (section or {}).items():
        if str(name).startswith("_") or body is None:
            continue
        if not isinstance(body, dict):
            raise ConfigError(f"{where}.{name}: must be an object")
        _check_keys(body, _FIELD_KEYS, f"{where}.{name}")
        if "validator" not in body:
            raise ConfigError(f"{where}.{name}: missing 'validator'")
        if body["validator"] not in FIELD_VALIDATORS:
            raise ConfigError(f"{where}.{name}: unknown validator '{body['validator']}' "
                              f"(known: {', '.join(sorted(FIELD_VALIDATORS))})")
        spec_where = f"{where}.{name}"
        specs.append(FieldSpec(
            name=name, validator=body["validator"], required=body.get("required", True),
            params=body.get("params", {}), owner=body.get("owner"),
            explicit_owner=body.get("explicit_owner", False), inherit=body.get("inherit", False),
            warn_if_missing=body.get("warn_if_missing", False),
            aliases=_field_localized(body, "aliases", default_language, spec_where, _FIELD_I18N_KEYS),
            weak_aliases=_field_localized(body, "weak_aliases", default_language, spec_where,
                                          _FIELD_I18N_KEYS),
            signature=body.get("signature", False), sums_to_total=body.get("sums_to_total", False),
            total_equivalent=body.get("total_equivalent", False),
            translatable=body.get("translatable", False)))
    return specs


def _build_abbreviation_rules(raw_rules: List[Dict[str, Any]], i18n: Dict[str, Dict[str, Any]],
                              default_language: str, name: str) -> Dict[str, Tuple[AbbreviationRule, ...]]:
    """Parse the abbreviation dictionary by language: plain rules belong to the default language,
    i18n.<lang>.abbreviation_rules to <lang>; a rule's own "language" key overrides."""
    by_language: Dict[str, List[AbbreviationRule]] = {}

    def add(rule_dicts: List[Dict[str, Any]], language: str, where: str) -> None:
        for i, r in enumerate(rule_dicts):
            missing = [k for k in ("abbreviation", "expansion", "confidence") if k not in r]
            if missing:
                raise ConfigError(f"{where}[{i}]: missing {', '.join(missing)}")
            _check_keys(r, {"abbreviation", "expansion", "confidence", "previous", "following", "language"},
                        f"{where}[{i}]")
            if not 0 <= float(r["confidence"]) <= 1:
                raise ConfigError(f"{where}[{i}]: confidence must be between 0 and 1")
            lang = r.get("language", language)
            _check_language_code(lang, f"{where}[{i}].language", allow_wildcard=True)
            by_language.setdefault(lang, []).append(AbbreviationRule(
                r["abbreviation"], r["expansion"], float(r["confidence"]),
                frozenset(r.get("previous", [])), frozenset(r.get("following", []))))

    add(raw_rules, default_language, f"{name}.abbreviation_rules")
    for lang, block in i18n.items():
        add(block.get("abbreviation_rules", []), lang, f"{name}.i18n.{lang}.abbreviation_rules")
    return {lang: tuple(rules) for lang, rules in by_language.items()}


def _build_owner_roles(raw: Dict[str, Any], i18n: Dict[str, Dict[str, Any]],
                       default_language: str) -> Dict[str, Localized]:
    """owner_role_keywords -> {role: {language: [words]}}."""
    roles: Dict[str, Localized] = {}
    for role, words in raw.get("owner_role_keywords", {}).items():
        roles[role] = {default_language: list(words)}
    for lang, block in i18n.items():
        for role, words in block.get("owner_role_keywords", {}).items():
            roles.setdefault(role, {}).setdefault(lang, []).extend(words)
    return roles


def _build_vocabularies(raw: Dict[str, Any], default_language: str, name: str) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Vocabulary terms: "aliases" (+ per-term i18n) become a Localized list; other attributes stay."""
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for vocab_name, terms in raw.items():
        if str(vocab_name).startswith("_"):
            continue
        out[vocab_name] = {}
        for term, meta in terms.items():
            if str(term).startswith("_"):
                continue
            where = f"{name}.vocabularies.{vocab_name}.{term}"
            meta = dict(meta or {})
            localized = _field_localized(meta, "aliases", default_language, where, _VOCAB_I18N_KEYS)
            meta.pop("i18n", None)
            meta["aliases"] = localized
            out[vocab_name][term] = meta
    return out


def _compile_target_schema(schema: Dict[str, Any], name: str):
    """Compile the target JSON Schema NOW so that a missing jsonschema package or an invalid schema
    is a loud ConfigError at load time - output validation is never silently skipped."""
    try:
        import jsonschema  # type: ignore
    except ImportError as error:
        raise ConfigError(
            f"{name}: 'target_schema' is defined but the 'jsonschema' package is not installed. "
            f"Install it (pip install jsonschema) or remove target_schema; output validation "
            f"will not be skipped silently.") from error
    validator_class = jsonschema.validators.validator_for(schema)
    try:
        validator_class.check_schema(schema)
    except jsonschema.SchemaError as error:
        raise ConfigError(f"{name}: target_schema is not a valid JSON Schema: {error.message}") from error
    return validator_class(schema)


def _check_consistency(cfg: SchemaConfig) -> None:
    """Cross-section checks that catch mistakes before any data is processed."""
    name = cfg.name
    if cfg.settings["number_locale"] not in ("auto", "en", "fr", "mixed"):
        raise ConfigError(f"{cfg.name}.settings.number_locale must be auto, en, fr or mixed")
    if cfg.settings["ambiguous_number_policy"] not in ("error", "english", "french"):
        raise ConfigError(f"{cfg.name}.settings.ambiguous_number_policy must be error, english or french")
    if not cfg.has_lines:
        if has_entries(cfg.line_containers):
            raise ConfigError(f"{name}: 'line_containers' is set but no 'line_fields' are defined")
        if cfg.csv.get("layout") == "lines":
            raise ConfigError(f"{name}: csv layout 'lines' requires 'line_fields'")
    if not cfg.all_specs:
        raise ConfigError(f"{name}: no fields defined")
    for spec in cfg.all_specs:
        for pattern in spec.params.get("patterns", []):
            try:
                re.compile(pattern)
            except re.error as error:
                raise ConfigError(f"{name}.{spec.name}: bad regex '{pattern}': {error}") from error
        if spec.validator == "vocabulary" and spec.params.get("vocabulary") not in cfg.vocabularies:
            raise ConfigError(f"{name}.{spec.name}: unknown vocabulary '{spec.params.get('vocabulary')}'")
        limit = spec.params.get("limit")
        if spec.validator == "money" and limit and limit not in cfg.limits:
            raise ConfigError(f"{name}.{spec.name}: limit '{limit}' is not defined in 'limits'")
    for rule in cfg.rules:
        kind = rule.get("kind")
        if kind not in _RULE_REQUIRED:
            raise ConfigError(f"{name}: unknown rule kind '{kind}'")
        missing = [k for k in _RULE_REQUIRED[kind] if k not in rule]
        if missing:
            raise ConfigError(f"{name}: rule '{kind}' is missing {', '.join(missing)}")
        if kind == "custom" and rule["name"] not in POST_RULES:
            raise ConfigError(f"{name}: custom rule '{rule['name']}' is not registered")
        if kind == "max_by_vocabulary" and rule["vocabulary"] not in cfg.vocabularies:
            raise ConfigError(f"{name}: rule refers to unknown vocabulary '{rule['vocabulary']}'")


def build_schema_config(raw: Dict[str, Any], source_path: Optional[str] = None) -> SchemaConfig:
    """Validate a raw (already merged) config dict and build the SchemaConfig. Raises ConfigError."""
    where = source_path or "config"
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: a config must be an object/dict")
    _check_keys(raw, _TOP_LEVEL_KEYS | {"extends"}, where)
    if "schema_name" not in raw:
        raise ConfigError(f"{where}: 'schema_name' is required")
    name = raw["schema_name"]
    formats = frozenset(f.lower() for f in raw.get("source_formats", ["json", "xml", "csv"]))
    if not formats or not formats <= {"json", "xml", "csv"}:
        raise ConfigError(f"{name}: source_formats must be a non-empty subset of json, xml, csv")
    csv_cfg = raw.get("csv", {})
    _check_keys(csv_cfg, {"layout"}, f"{name}.csv")
    if csv_cfg.get("layout", "records") not in ("records", "lines"):
        raise ConfigError(f"{name}.csv.layout must be 'records' or 'lines'")
    settings_override = raw.get("settings", {})
    _check_keys(settings_override, set(DEFAUL_SETTINGS_KEYS()), f"{name}.settings")
    output = raw.get("output", {})
    _check_keys(output, set(DEFAULT_OUTPUT), f"{name}.output")

    # ---- languages ----
    default_language = raw.get("default_language", DEFAULT_LANGUAGE)
    _check_language_code(default_language, f"{name}.default_language")
    languages = tuple(raw.get("languages", DEFAULT_LANGUAGES))
    if not languages:
        raise ConfigError(f"{name}.languages must not be empty")
    for code in languages:
        _check_language_code(code, f"{name}.languages")
    i18n = _parse_i18n(raw.get("i18n"), _I18N_TOP_KEYS, f"{name}.i18n")
    language_tags = {lang: list(tags) for lang, tags in DEFAULT_LANGUAGE_TAGS.items()}
    for lang, tags in raw.get("language_tags", {}).items():
        _check_language_code(lang, f"{name}.language_tags")
        language_tags.setdefault(lang, []).extend(tags)

    # ---- discriminator ----
    disc_raw = raw.get("discriminator", {})
    _check_keys(disc_raw, {"aliases", "weak_aliases", "values", "i18n"}, f"{name}.discriminator")
    discriminator = {key: _field_localized(disc_raw, key, default_language, f"{name}.discriminator",
                                           _DISCRIMINATOR_I18N_KEYS)
                     for key in ("aliases", "weak_aliases", "values")}

    limits: Dict[str, Tuple[float, float]] = {}
    for key, bounds in raw.get("limits", {}).items():
        if not (isinstance(bounds, (list, tuple)) and len(bounds) == 2):
            raise ConfigError(f"{name}.limits.{key}: expected [minimum, maximum]")
        limits[key] = (float(bounds[0]), float(bounds[1]))

    cfg = SchemaConfig(
        name=name, source_formats=formats,
        settings=_merge(deepcopy(DEFAULT_SETTINGS), settings_override),
        languages=languages, default_language=default_language, language_tags=language_tags,
        stopwords=_localized_list("stopwords", raw, i18n, default_language, name),
        abbreviation_rules=_build_abbreviation_rules(raw.get("abbreviation_rules", []), i18n,
                                                     default_language, name),
        disqualifying_words=_localized_list("disqualifying_key_words", raw, i18n, default_language, name),
        owner_roles=_build_owner_roles(raw, i18n, default_language),
        neutral_words=_localized_list("neutral_container_words", raw, i18n, default_language, name),
        generic_aliases=_localized_list("generic_aliases", raw, i18n, default_language, name),
        generic_line_containers=_localized_list("generic_line_containers", raw, i18n, default_language, name),
        limits=limits, vocabularies=_build_vocabularies(raw.get("vocabularies", {}), default_language, name),
        discriminator=discriminator,
        line_containers=_localized_list("line_containers", raw, i18n, default_language, name),
        document_fields=_build_specs(raw.get("document_fields"), f"{name}.document_fields", default_language),
        line_fields=_build_specs(raw.get("line_fields"), f"{name}.line_fields", default_language),
        total_field=raw.get("total_field", "total_amount"), rules=raw.get("rules", []),
        output={**DEFAULT_OUTPUT, **output}, target_schema=raw.get("target_schema"),
        csv=csv_cfg, source_path=source_path)
    _check_consistency(cfg)
    if cfg.target_schema:
        cfg.target_validator = _compile_target_schema(cfg.target_schema, name)
    return cfg


def DEFAUL_SETTINGS_KEYS():  # noqa: N802  (kept tiny and private-ish: the set of allowed setting names)
    """Names accepted in a config's "settings" block."""
    return DEFAULT_SETTINGS.keys()


# ---------------------------------------------------------------------------
# public loaders
# ---------------------------------------------------------------------------
def load_schema_config(path: Any) -> SchemaConfig:
    """Load ONE config file (with its "extends" chain). Not cached: call once and reuse the object."""
    path = Path(path)
    return build_schema_config(_load_raw(path), str(path.resolve()))


def load_schema_configs(configs: Any) -> List[SchemaConfig]:
    """Load many schemas. `configs` may be a file path, a directory (every .json/.py/.yaml that does not
    start with "_"), a dict, a SchemaConfig, or a list of those. Schema names must be unique."""
    items = configs if isinstance(configs, (list, tuple)) else [configs]
    loaded: List[SchemaConfig] = []
    for item in items:
        if isinstance(item, SchemaConfig):
            loaded.append(item)
        elif isinstance(item, dict):
            loaded.append(build_schema_config(_merge({}, item)))
        else:
            path = Path(item)
            if path.is_dir():
                files = sorted(p for p in path.iterdir()
                               if p.suffix.lower() in (".json", ".py", ".yaml", ".yml")
                               and not p.name.startswith("_"))
                loaded.extend(load_schema_config(p) for p in files)
            else:
                loaded.append(load_schema_config(path))
    if not loaded:
        raise ConfigError("no schema configuration supplied")
    names = [c.name for c in loaded]
    if len(set(names)) != len(names):
        raise ConfigError(f"duplicate schema names: {names}")
    return loaded
