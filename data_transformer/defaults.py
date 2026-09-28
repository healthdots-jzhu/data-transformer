"""Engine defaults. Every key of DEFAULT_SETTINGS can be overridden in a config file's "settings" block,
or per call with transform(settings={...}). Unknown setting names raise ConfigError (typo protection).
Also holds the built-in language packs (English and French)."""

DEFAULT_SETTINGS = {
    "number_locale": "auto",          # number format of amounts/quantities: "auto" (decided per document
                                      # from the languages of its keys) | "en" | "fr" | "mixed".
                                      # Set "mixed"/"fr" if labels are English but values are French-formatted.
    "ambiguous_number_policy": "error",   # in a "mixed" document, "1,234" could be 1234 or 1.234:
                                          # "error" | "english" | "french"

    # ---- key -> field matching ----------------------------------------------------------
    "fuzzy_threshold": 0.80,          # minimum similarity for a key to match a descriptive alias
    "weak_alias_confidence": 0.85,    # score given to a generic ("amount", "montant") match
    "leaf_only_match_penalty": 0.95,  # multiplier when only the last segment of a nested key matched
    "abbreviation_confidence_without_warning": 0.90,  # expansions below this add a warning and
                                                      # can never establish a generic match
    # ---- type / vocabulary recognition --------------------------------------------------
    "discriminator_threshold": 0.75,  # similarity of an explicit type VALUE ("Dental claim") to the schema
    "vocabulary_threshold": 0.75,     # similarity for the "vocabulary" validator
    # ---- value validation ----------------------------------------------------------------
    "max_age_days": 730,              # "date" validator: older dates are rejected (null = unlimited;
                                      # can be overridden per field via params.max_age_days)
    "allow_future_dates": False,
    "day_first": True,                # how to read 03/04/2024 (True = 3 April)
    # ---- duplicate / conflicting data ----------------------------------------------------
    "conflicting_values_policy": "error",   # two keys map to one field with different values:
                                            # "error" | "warning" (keep the strongest key)
    "total_mismatch_severity": "error",     # total field != sum of line amounts: "error" | "warning"
    # ---- languages -------------------------------------------------------------------------
    "preferred_language": None,       # which language's value wins for "translatable" fields present
                                      # in several languages (null = first of the enabled languages)
    # ---- line items ------------------------------------------------------------------------
    "inherit_shared_fields": False,         # let a top-level value fill lines lacking their own
                                            # (only fields flagged "inherit": true)
    "interpret_root_amount_as_total": False,  # a top-level amount next to a line list = the total
    "single_line_total_as_amount": False,     # no line list: use the total as the single line's amount
    # ---- schema selection ---------------------------------------------------------------------
    "min_schema_score": 0.50,         # a schema must reach this similarity to be selected
    "min_schema_margin": 0.05,        # ...and beat the runner-up by this much, else "ambiguous"
    "similarity_weights": {"signature": 0.5, "required": 0.2, "coverage": 0.3},
        # signature = share of the schema's "signature" fields found in the data
        # required  = share of the schema's required fields found in the data
        # coverage  = share of the data's keys the schema can explain
}

# Names of the keys in the normalised output JSON; override per schema with the "output" block.
DEFAULT_OUTPUT = {"type_key": "schema", "fields_key": "fields", "lines_key": "lines"}

# ---- languages -----------------------------------------------------------------------------------
DEFAULT_LANGUAGE = "en"          # language of the plain (non-i18n) lists in a config file
DEFAULT_LANGUAGES = ["en"]       # languages a schema accepts unless its config says otherwise

# Built-in language packs: lists a config gets for free in each language. A config's own lists
# (base or "i18n") are ADDED to these; for the default language an explicit base list REPLACES the
# pack entry. Keys are the same names used in a config's top-level "i18n" blocks.
BUILTIN_PACKS = {
    "en": {
        "stopwords": ["of", "the", "a", "an", "for", "and", "s"],   # "s" is left over from "dentist's"
        "generic_aliases": ["amount", "cost", "fee", "charge", "price", "date", "code", "type"],
        "generic_line_containers": ["lines", "line items", "items", "details", "services",
                                    "claim lines", "service lines", "entries", "rows"],
    },
    "fr": {
        # NOTE: "en" is deliberately NOT a stop-word: it doubles as the English language tag.
        "stopwords": ["de", "du", "des", "d", "la", "le", "les", "l", "un", "une", "et", "au", "aux",
                      "a", "pour", "s"],
        "generic_aliases": ["montant", "cout", "frais", "prix", "tarif", "date", "code", "type"],
        "generic_line_containers": ["lignes", "lignes de service", "articles", "details", "services",
                                    "entrées", "éléments", "rangées"],
    },
}

# Tokens that tag a key with a language: "description_en", "nom_fr", {"description": {"en": ...}}.
DEFAULT_LANGUAGE_TAGS = {
    "en": ["en", "eng", "english", "anglais"],
    "fr": ["fr", "fra", "fre", "french", "francais", "français"],
}

# Languages that have a number format the parser knows (see value_parsers): used to choose the locale.
NUMBER_FORMAT_LANGUAGES = frozenset({"en", "fr"})
