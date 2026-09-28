# data-transformer

Detects which predefined target schema a JSON / XML / CSV dataset belongs to, validates it and converts
it into the schema's normalised JSON. Every rule (aliases, abbreviations, limits, validators, target
JSON Schema) lives in a per-data-type config file; the engine has no domain knowledge.

    python -m venv .venv
    .venv\Scripts\Activate.ps1                         # PowerShell on Windows
    # macOS/Linux: source .venv/bin/activate
    pip install -r requirements.txt      # editable install of data_transformer + dependencies
    # or:  pip install -e ".[xml,test]"

    pytest                               # run from the project root
    python -m data_transformer claim.xml --config configs/
    data-transformer claim.xml --config configs/           # console script installed by pip


```python
from data_transformer import transform
report = transform("claim.xml", "configs/")              # directory, file, dict or list of those
for r in report.results:
    print(r.is_valid, r.errors, r.warnings, r.normalized)
```

## Bundled example configs

The JSON files in `configs/` are included in both the wheel and source distribution as
`data_transformer.example_configs`. Setuptools maps that package to the existing `configs/`
directory, so checkout paths remain the same and there is only one copy to maintain.

After installing the package, copy the examples into your own project before editing them.
This example uses Python 3.9 or newer:

```python
from importlib.resources import files
from pathlib import Path

destination = Path("example-configs")
destination.mkdir()  # Choose a new directory to avoid overwriting your configs.
for resource in files("data_transformer.example_configs").iterdir():
    if resource.name.endswith(".json"):
        (destination / resource.name).write_bytes(resource.read_bytes())
```

Copy all JSON files together: schemas use relative `extends` references to shared fragments
and French overlays. You can then run:

```shell
data-transformer claim.xml --config example-configs/
```

These are examples to adapt to your own requirements. Package upgrades do not change your
copied files. When adding Python subpackages, also add them to `tool.setuptools.packages`.

## Pipeline

1. Load configs (strict, `extends` chains) -> 2. read source (CSV refuses duplicate headers, ragged rows and
orphan values) -> 3. flatten (`patient > name`) -> 4. drop schemas not accepting the format ->
5. classify every record by explicit type value -> 6. choose ONE schema for the dataset (explicit type, else
similarity; mixed/unknown types are errors; untyped records must individually fit) ->
7. map keys to fields -> 8. validate document fields, then line items (only if the schema has `line_fields`) ->
9. totals and rules -> 10. enforce `target_schema` -> 11. report.

## Creating a new target schema (step by step)

1. Copy `configs/person.json` (document only) or `configs/dental.json` (document + line items).
2. Set `schema_name` and `source_formats`.
3. List `document_fields` (and `line_fields` if the data has repeating items). For each: a `validator`,
   `required`, strong `aliases`, optionally `weak_aliases`, and `signature: true` on 3-5 distinctive fields.
4. Add `limits` for money fields (`params.limit`) and `vocabularies` for controlled terms.
5. Optionally share rules with `"extends": ["_common.json"]` or your own `_fragment.json`.
6. Add a `discriminator` if the data carries an explicit type; without it similarity decides.
7. Describe the OUTPUT in `target_schema` (JSON Schema; requires `jsonschema`). Output shape is
   `{"<type_key>": name, "<fields_key>": {...}, "<lines_key>": [...]}` (lines only with `line_fields`);
   rename the keys with `output`.
8. Run `python -m data_transformer sample.json --config configs/your_schema.json` and read the warnings:
   they show generic matches, low-confidence abbreviations and ignored keys.

## Languages (English, French, bilingual)

    transform("reclamation.xml", "configs/")                     # every language the schema enables
    transform("reclamation.xml", "configs/", languages=["fr"])   # French-only data: English aliases off

How it works
* A schema enables languages with `"languages": ["en", "fr"]` (order = preference). Plain lists in a
  config are the `default_language` (en). Other languages are added with `i18n` blocks:
  - field specs, `discriminator` and vocabulary terms carry their own `"i18n": {"fr": {"aliases": [...]}}`;
  - schema-wide lists (stopwords, abbreviation_rules, owner_role_keywords, ...) go in top-level `"i18n"`.
* The shipped configs keep French in overlay fragments (`_common.fr.json`, `_dental.fr.json`, ...) pulled
  in through `extends`; remove the overlay from `extends` to make a schema English-only.
* Accents are ignored when matching (`téléphone` = `telephone`), and French stop-words (`de`, `du`, `l'`)
  are built in.
* Bilingual labels are split (`Full name / Nom complet`, `Price / Prix`). Every variant must match
  the same candidate field; an unrecognized or disqualified variant refuses the whole key.
  See the remaining agreement limitation in `INTEGRATION_NOTES.md`.
  Language tags are understood: `name_fr`, `description_en`, `{"description": {"en": ..., "fr": ...}}`.
* A field flagged `"translatable": true` may be present in several languages: the preferred language's value
  goes to the output and every language's value to `result.translations`. For other fields, the same value
  in two languages is silent and different values are a conflict.
* French dates include `14 août 2024`, `1er mai 2025`, and `14 mai 2025 à 23 h 59`.
  French-formatted amounts and quantities use the number-format rules below.
* Output field names and vocabulary terms stay canonical (English) whatever the source language.
  `result.languages_detected` lists the languages the keys were in.
* Non-UTF-8 files (Windows-1252 / ISO-8859-1 / UTF-16) are decoded with a warning; the CSV delimiter is
  chosen from the header (`;` for French Excel exports).

Adding a language: add it to `languages`, supply `i18n.<lang>` entries (built-in packs exist for en and fr
only), optionally `language_tags`. Month names for the date parser are in `value_parsers.py`.

Limits: error/warning messages stay in English; the weekday abbreviation "mar" is not recognised in
French dates (it means March in English). Individual hours 20–23 are supported, but French time ranges
such as `20h–23h` are not handled by this revision.

### Number formats

Amounts and quantities share one number format per document, reported in `result.number_locale`.
Mapping happens before validation and duplicate-value comparison so those operations use the same
format. Recognized English and French field labels supply the language evidence. With no evidence,
the enabled languages determine the fallback; enabling both gives `mixed`.

| Document keys | Locale | `1,234` | `1 234,56` | `1,234.56` | `12,5` |
|---|---|---|---|---|---|
| English only | `en` | 1234 | Rejected | 1234.56 | Rejected |
| French only | `fr` | 1.234 | 1234.56 | Rejected | 12.5 |
| Both languages | `mixed` | Ambiguous; follows policy | 1234.56 | 1234.56 | 12.5 |

Money is subsequently rounded to cents, with a warning if rounding changes the value; quantities
retain their decimal precision subject to their configured constraints.

Configure these in the schema's `settings` or pass them through `transform(settings={...})`:

- `number_locale`: `auto` (default), `en`, `fr`, or `mixed`. For English labels with French-formatted
  values, select `fr` or `mixed` explicitly.
- `ambiguous_number_policy`: `error` (default), `english`, or `french`. In mixed mode, choosing a
  reading of an ambiguous value produces a warning.

Date parsing continues to use the enabled languages independently of the number locale.

## Config reference

The complete, authoritative description of every config segment is the docstring at the top of
`data_transformer/config_loader.py`; defaults for `settings` are documented in `defaults.py`;
validator parameters in `validators.py`. Summary:

| Segment | Purpose |
|---|---|
| `extends` | parent files merged first (dicts merge, lists replace, `null` removes) |
| `schema_name` | unique name, used in output and reports |
| `source_formats` | formats this schema accepts (`json`, `xml`, `csv`) |
| `csv.layout` | `records` (row = document) or `lines` (rows = line items of one document) |
| `settings` | thresholds and policies (dates, conflicts, selection) |
| `stopwords`, `abbreviation_rules` | key normalisation dictionary with confidence and context |
| `disqualifying_key_words` | words that make a key a different quantity ("max amount") |
| `owner_role_keywords`, `neutral_container_words` | who owns a nested key; which parents are meaningless |
| `discriminator` | how an explicit type key/value is recognised |
| `line_containers` | names of the line-item array (only with `line_fields`) |
| `limits` | named [min, max] ranges for money fields |
| `vocabularies` | controlled term lists for the `vocabulary` validator |
| `total_field` | document field compared with the sum of `sums_to_total` line fields |
| `document_fields` / `line_fields` | the canonical fields (validator, params, aliases, flags) |
| `rules` | `max_by_vocabulary`, `rate_limit`, `custom` cross-field checks |
| `output` | names of the output keys |
| `target_schema` | JSON Schema enforced on the output; loading fails without `jsonschema` |

Keys starting with `_` are comments. Unknown keys raise `ConfigError`.

## Behaviour guarantees

* Missing `jsonschema` with a `target_schema` -> `ConfigError` at load time, never a skipped check.
* Schemas without `line_fields` never look for, require or output line items.
* CSV never silently loses data (duplicate headers, extra values, ragged rows are errors).
* A dataset is never processed partly under the wrong schema: unknown or mixed explicit types are
  dataset-level errors; untyped records that do not fit are record-level errors.

## Known limits

* A type carried only by a generic key (`"type": "wellness"`) cannot be told from other meanings of
  "type" and is ignored; use a descriptive key (`claim type`) or pass all candidate configs.
* XML: wrap each document in one root element; a wrapper with a single child becomes a list only if its
  name is a configured line container.
* Configs are not cached: load once with `load_schema_configs()` and pass the objects to `transform()`
  when processing many files.

## Extending

```python
from data_transformer import register_validator, register_rule, ValidationOutcome

@register_validator("provincial_licence")      # then  "validator": "provincial_licence"
def _licence(raw, spec, ctx):
    ok = str(raw).startswith("ON-")
    return ValidationOutcome(str(raw), None if ok else "licence must start with ON-")

@register_rule("no_weekend_visits")            # then  {"kind": "custom", "name": "no_weekend_visits"}
def _weekend(doc_values, lines, ctx, result, rule):
    ...
```
Register before loading the configs that refer to them.

## Known limitations

See [the integration notes](INTEGRATION_NOTES.md) for known limitations and proposed fixes.

## Test coverage

Run all active tests and enforce a 96% floor for both statement and branch coverage
of the transformer package independently:

```powershell
.\.venv\Scripts\python.exe scripts/run_coverage.py
```

The runner measures the entire `data_transformer` package, including its CLI.
No production lines or branches are excluded, including lines
marked by pre-existing exclusion comments in the supplied implementation.

Reports: `htmlcov_integration/index.html`, `coverage_integration.json`, and
`test_results_integration.xml`. See [the validation results](TEST_RESULTS_transformer.md).
Plain `pytest` runs the same active suites without coverage measurement. Temporary
test files are isolated in `.pytest_tmp_transformer` beneath the working directory.
