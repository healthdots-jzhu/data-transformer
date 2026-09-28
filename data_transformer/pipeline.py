"""The public entry point: transform(). See the package docstring (data_transformer/__init__.py)
for the high-level pipeline; the numbered comments below mark each stage."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .config_loader import load_schema_configs
from .document_processor import process_document
from .errors import ConfigError, SourceError
from .localization import all_words
from .models import Context
from .paths import flatten
from .results import TransformReport, TransformResult
from .schema_selection import (classify_record, record_fit_error, rows_to_document, select_schema)
from .source_readers import load_source, squash

Flat = Tuple[Optional[Dict[str, Any]], Optional[str]]    # (flattened record, error message)


def _flatten_records(records: List[Any]) -> List[Flat]:
    """Flatten each record; a non-object, empty or path-colliding record becomes an error entry so
    that record numbering stays aligned with the source."""
    flats: List[Flat] = []
    for record in records:
        if not isinstance(record, dict):
            flats.append((None, "record must be an object"))
            continue
        try:
            flat = flatten(record)
            flats.append((flat, None) if flat else (None, "record is empty"))
        except ValueError as error:
            flats.append((None, str(error)))
    return flats


def transform(source: Any, configs: Any, *, source_format: Optional[str] = None,
              schema: Optional[str] = None, languages: Optional[Sequence[str]] = None,
              csv_delimiter: Optional[str] = None, today: Optional[date] = None,
              settings: Optional[Dict[str, Any]] = None) -> TransformReport:
    """Detect the target schema of `source`, validate and convert it.

    source         file path, raw JSON/XML/CSV text or bytes, or a dict/list.
    configs        a config file, a directory of config files, a dict, a SchemaConfig, or a list of those.
    source_format  "json" | "xml" | "csv"; otherwise detected from extension / content.
    schema         force one schema by name (skips similarity; an explicit conflicting type is still an error).
    languages      restrict matching to these languages, e.g. ["fr"] for French-only data. Each must be
                   enabled in every candidate schema's "languages". Default: all languages the schema enables.
    csv_delimiter  CSV delimiter; auto-detected if omitted.
    today          reference date for date rules (default: today).
    settings       per-call overrides of any setting, applied to every schema.

    Raises ConfigError for configuration problems. Data problems are returned in the report:
    report.errors (dataset level) and report.results[i].errors (record level)."""
    report = TransformReport()

    # 1. LOAD CONFIGS (strict; raises ConfigError)
    cfgs = load_schema_configs(configs)
    if settings:
        cfgs = [replace(c, settings={**c.settings, **settings}) for c in cfgs]
    if languages:
        for c in cfgs:
            missing = [l for l in languages if l not in c.languages]
            if missing:
                raise ConfigError(f"schema '{c.name}' does not enable language(s) {', '.join(missing)} "
                                  f"(enabled: {', '.join(c.languages)})")

    # 2. READ SOURCE -> records (container names of ALL languages help XML single-child wrappers)
    hints = {squash(n) for c in cfgs for n in all_words(c.line_containers) + all_words(c.generic_line_containers)}
    try:
        fmt, records, notes = load_source(source, source_format, csv_delimiter, hints)
    except SourceError as error:
        report.errors.append(str(error))
        return report
    report.source_format = fmt
    report.warnings.extend(notes)

    # 4. FORMAT FILTER: only schemas that accept this format remain candidates
    eligible = [c for c in cfgs if fmt in c.source_formats]
    if schema:
        if schema not in {c.name for c in cfgs}:
            raise ConfigError(f"unknown schema '{schema}'")
        eligible = [c for c in eligible if c.name == schema]
    if not eligible:
        report.errors.append(f"no configured schema accepts the source format '{fmt}'")
        return report
    ctxs = [Context(c, today or date.today(), languages) for c in eligible]

    # 3. FLATTEN records ("patient > name")
    flats = _flatten_records(records)
    valid_idx = [i for i, (f, _) in enumerate(flats) if f is not None]
    if not valid_idx:
        report.errors.append("; ".join(e for _, e in flats if e) or "no usable records")
        return report
    valid = [flats[i][0] for i in valid_idx]

    # 5-6. CLASSIFY every record, then SELECT the schema for the whole dataset
    statuses = [classify_record(f, ctxs) for f in valid]
    ctx = select_schema(ctxs, statuses, valid, bool(schema), report)
    if ctx is None:
        return report
    report.schema = ctx.cfg.name
    disc_keys = {i: (st[2].request_key if st[0] == "resolved" else None)
                 for i, st in zip(valid_idx, statuses)}
    disc_langs = {i: (st[2].languages if st[0] == "resolved" else ())
                  for i, st in zip(valid_idx, statuses)}

    # records without an explicit type must each fit the schema individually
    if not schema:
        for i, st in zip(valid_idx, statuses):
            if st[0] == "none":
                problem = record_fit_error(flats[i][0], ctx)
                if problem:
                    flats[i] = (None, problem)

    # CSV layout "lines": all rows form one document (any bad row invalidates the combination)
    if fmt == "csv" and ctx.cfg.has_lines and ctx.cfg.csv.get("layout", "records") == "lines":
        bad = [(n, e) for n, (_, e) in enumerate(flats, 1) if e]
        if bad:
            report.errors.append("CSV rows cannot be combined into one document: "
                                 + "; ".join(f"row {n}: {e}" for n, e in bad))
            return report
        rows = [f for f, _ in flats]
        first_key = next((k for k in disc_keys.values() if k), None)
        first_langs = next((l for l in disc_langs.values() if l), ())
        flats = [(flatten(rows_to_document(rows, ctx, first_key)), None)]
        disc_keys, disc_langs = {0: first_key}, {0: first_langs}

    # 7-11. MAP, VALIDATE, CROSS-CHECK, ENFORCE TARGET SCHEMA, REPORT - per record
    for index, (flat, error) in enumerate(flats, start=1):
        result = TransformResult(schema=ctx.cfg.name, record_index=index)
        if error:
            result.errors.append(error)
        else:
            process_document(flat, ctx, disc_keys.get(index - 1), result, disc_langs.get(index - 1, ()))
        report.results.append(result)
    report.languages_detected = sorted({l for r in report.results for l in r.languages_detected})
    return report
