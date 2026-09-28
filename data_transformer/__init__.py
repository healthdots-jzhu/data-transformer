"""
data_transformer - configuration-driven detection, validation and conversion of JSON / XML / CSV data
into predefined target JSON structures. Handles English, French and bilingual (English + French) data.

HIGH-LEVEL PIPELINE  (implemented in pipeline.transform)
=========================================================
  1. LOAD CONFIGS      config_loader   One config file per data type ("schema"). Files may `extends`
                                       shared fragments, including per-language overlays (_x.fr.json).
                                       Loading is strict: typos raise ConfigError, and a target_schema
                                       without the jsonschema package raises too.
  2. READ SOURCE       source_readers  JSON / XML / CSV (path, text, bytes, dict/list) -> records.
                                       Non-UTF-8 files (Windows-1252 / ISO-8859-1 / UTF-16) are decoded
                                       with a warning. CSV refuses duplicate headers, ragged rows and
                                       orphan values instead of silently dropping data.
  3. FLATTEN           paths           Nested objects become "patient > name" style keys so that
                                       ownership ("whose phone is this?") can be judged from the path.
  4. FORMAT FILTER     pipeline        Only schemas whose `source_formats` include the detected format
                                       remain candidates.
  5. CLASSIFY RECORDS  schema_selection Each record is inspected for an explicit type value
                                       ("claim type": "dental" / "Dentaire"). Unrecognised or mixed types
                                       are errors.
  6. SELECT SCHEMA     schema_selection If no explicit type exists, candidates are scored by similarity
                                       and the winner must be clearly ahead. Records that do not fit the
                                       chosen schema individually are rejected.
  7. MAP KEYS          matching        Every source key is mapped to a canonical field using aliases in
                                       every enabled language, accent folding, abbreviation rules, fuzzy
                                       matching, ownership and disqualifying-word safeguards. Bilingual
                                       keys ("Name / Nom") are split; language tags (_en/_fr) are read.
  8. VALIDATE          document_processor, line_items, validators
                                       Document-level fields, then (only if the schema defines line
                                       items) every line item; values are normalised (French dates and
                                       decimal-comma amounts included).
  9. CROSS-CHECK       post_checks     Total vs. sum of lines (when both exist), declarative rules,
                                       custom rules.
 10. ENFORCE TARGET    post_checks     Optional JSON Schema check of the final output.
 11. REPORT            results         errors, warnings, normalised JSON, key-mapping audit trail,
                                       detected languages and translation pairs.

Quick start
-----------
    from data_transformer import transform
    report = transform("incoming/reclamation.xml", "configs/")
    for result in report.results:
        print(result.is_valid, result.languages_detected, result.errors, result.normalized)
"""
from .config_loader import build_schema_config, load_schema_config, load_schema_configs
from .errors import ConfigError, SourceError
from .models import FieldSpec, SchemaConfig
from .pipeline import transform
from .registry import ValidationOutcome, register_rule, register_validator
from .results import TransformReport, TransformResult

__all__ = [
    "transform", "load_schema_config", "load_schema_configs", "build_schema_config",
    "register_validator", "register_rule", "ValidationOutcome",
    "ConfigError", "SourceError", "TransformReport", "TransformResult", "FieldSpec", "SchemaConfig",
]
