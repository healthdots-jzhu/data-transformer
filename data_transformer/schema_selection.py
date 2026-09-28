"""Pre-fetching the dataset's shape and choosing the closest configured schema.

Decision order
  1. Every record is CLASSIFIED by its explicit type value, if it has one.
       resolved      the value names exactly one candidate schema
       ambiguous     the value fits several candidates equally
       unrecognised  a descriptive type key exists but its value fits no candidate
       none          no (usable) type information
     Type keys and values may be in any enabled language ("Type de réclamation": "Dentaire") or
     bilingual ("Dental / Dentaire").
  2. Dataset errors: any unrecognised/ambiguous record, or records resolved to DIFFERENT schemas
     (e.g. vision + wellness). A dataset is never processed partly under the wrong schema.
  3. If records agree on one explicit schema, that is the schema.
  4. Otherwise candidates are scored structurally; the best must reach min_schema_score and lead the
     runner-up by min_schema_margin.
  5. (pipeline) records without a type must each fit the chosen schema on their own.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .localization import active_words
from .matching import FieldMatch, map_request_keys_to_fields
from .models import Context, FieldSpec
from .normalization import text_similarity, text_variants
from .paths import PATH_SEP, flatten
from .results import TransformReport

Status = Tuple[str, Any, Optional[FieldMatch]]   # (status, schema name(s), matched type field)


def build_profile(flats: List[Dict[str, Any]]) -> Dict[str, None]:
    """The dataset's 'schema': every key path that occurs, including keys of objects inside arrays
    (line items), so line-level fields take part in matching. Values are irrelevant (None)."""
    probe: Dict[str, None] = {}
    for flat in flats:
        for key, value in flat.items():
            probe.setdefault(key, None)
            if isinstance(value, list):
                parent = key.rpartition(PATH_SEP)[0]
                for item in value:
                    if isinstance(item, dict):
                        try:
                            for k in flatten(item, parent):
                                probe.setdefault(k, None)
                        except ValueError:
                            continue
    return probe


def discriminator_spec(ctx: Context) -> FieldSpec:
    """Pseudo-field describing the schema's type key, built from the config's "discriminator"."""
    d = ctx.cfg.discriminator
    return FieldSpec("schema_type", "text", required=False, aliases=d["aliases"], weak_aliases=d["weak_aliases"])


def discriminator_score(value: Any, ctx: Context) -> float:
    """Similarity (0..1) of a type VALUE ('Dental Claim - Basic', 'Dentaire', 'Dental / Dentaire') to
    this schema's names in the active languages. Bilingual values score as their best part."""
    names = active_words(ctx.cfg.discriminator["values"], ctx.languages) + [ctx.cfg.name]
    best = 0.0
    for variant in text_variants(str(value), include_whole=True):
        text = ctx.norm_plain(variant).text
        for alias in names:
            alias_text = ctx.norm_plain(alias).text
            score = text_similarity(text, alias_text)
            if len(alias_text) >= 4 and alias_text in text:
                score = max(score, 0.9)
            best = max(best, score)
    return best


def classify_record(flat: Dict[str, Any], ctxs: List[Context]) -> Status:
    """Classify ONE flattened record against all candidates (see module docstring). A generic
    ('weak') type key whose value is unrecognised is ignored, because a bare "type" may mean
    something else (e.g. a practitioner type); a descriptive key with an unrecognised value is an error."""
    explicit: List[Tuple[float, Context, FieldMatch]] = []
    rejected: List[FieldMatch] = []
    for ctx in ctxs:
        spec = discriminator_spec(ctx)
        if not (any(spec.aliases.values()) or any(spec.weak_aliases.values())):
            continue
        match = map_request_keys_to_fields(flat, [spec], ctx, True).matches.get("schema_type")
        if not match:
            continue
        score = discriminator_score(match.value, ctx)
        if score >= ctx.settings["discriminator_threshold"]:
            explicit.append((score, ctx, match))
        elif not match.matched_via_weak_alias:
            rejected.append(match)
    if explicit:
        best = max(s for s, _, _ in explicit)
        winners = [(c, m) for s, c, m in explicit if s == best]
        if len(winners) > 1:
            return "ambiguous", [c.cfg.name for c, _ in winners], winners[0][1]
        return "resolved", winners[0][0].cfg.name, winners[0][1]
    if rejected:
        return "unrecognised", None, rejected[0]
    return "none", None, None


def structural_score(probe: Dict[str, None], ctx: Context) -> float:
    """Similarity of a key profile to a schema: weighted mix (settings.similarity_weights) of the
    share of signature fields found, the share of required fields found, and the share of keys the
    schema explains. Generic (weak) aliases are NOT used here: they say too little. Components that do
    not exist for the schema (e.g. no signature fields) are left out and the weights renormalised."""
    specs: Dict[str, FieldSpec] = {}
    for spec in ctx.cfg.all_specs:
        specs.setdefault(spec.name, spec)
    outcome = map_request_keys_to_fields(probe, list(specs.values()), ctx, use_weak=False)
    signature = [n for n, s in specs.items() if s.signature]
    required = [n for n, s in specs.items() if s.required]
    parts = {
        "signature": (sum(n in outcome.matches for n in signature) / len(signature)) if signature else None,
        "required": (sum(n in outcome.matches for n in required) / len(required)) if required else None,
        "coverage": len(outcome.consumed_keys) / len(probe) if probe else 0.0,
    }
    weights = ctx.settings["similarity_weights"]
    used = {k: v for k, v in parts.items() if v is not None}
    total = sum(weights[k] for k in used)
    return sum(weights[k] * v for k, v in used.items()) / total if total else 0.0


def select_schema(ctxs: List[Context], statuses: List[Status], flats: List[Dict[str, Any]],
                  forced: bool, report: TransformReport) -> Optional[Context]:
    """Dataset-level decision (see module docstring). Fills report.schema_scores and report.errors;
    returns the chosen Context or None. Looks at EVERY record, not just the first."""
    settings = ctxs[0].settings
    probe = build_profile(flats)
    for ctx in ctxs:
        report.schema_scores[ctx.cfg.name] = round(structural_score(probe, ctx), 4)
    candidates = ", ".join(c.cfg.name for c in ctxs)

    unrecognised = [(n, m) for n, (st, _, m) in enumerate(statuses, 1) if st == "unrecognised"]
    if unrecognised:
        report.errors.append(
            "record(s) declare a type that matches no candidate schema ("
            + "; ".join(f"record {n}: '{m.value}'" for n, m in unrecognised[:10])
            + f"); candidate schemas: {candidates}")
    ambiguous = [(n, names) for n, (st, names, _) in enumerate(statuses, 1) if st == "ambiguous"]
    if ambiguous:
        report.errors.append("record type matches several schemas ("
                             + "; ".join(f"record {n}: {', '.join(names)}" for n, names in ambiguous[:10]) + ")")
    named = sorted({name for st, name, _ in statuses if st == "resolved"})
    if len(named) > 1:
        report.errors.append(f"the dataset mixes records of different schemas ({', '.join(named)}); "
                             f"split it by type and transform each part separately")
    if report.errors:
        return None

    if named:
        return next(c for c in ctxs if c.cfg.name == named[0])
    if forced:
        return ctxs[0]

    ranked = sorted(ctxs, key=lambda c: -report.schema_scores[c.cfg.name])
    best, best_score = ranked[0], report.schema_scores[ranked[0].cfg.name]
    if best_score < settings["min_schema_score"]:
        report.errors.append(f"no configured schema is similar enough to the data "
                             f"(best: '{best.cfg.name}' at {best_score:.2f}, minimum "
                             f"{settings['min_schema_score']:.2f})")
        return None
    if len(ranked) > 1 and best_score - report.schema_scores[ranked[1].cfg.name] < settings["min_schema_margin"]:
        tied = [c.cfg.name for c in ranked
                if best_score - report.schema_scores[c.cfg.name] < settings["min_schema_margin"]]
        report.errors.append(f"schema could not be determined; ambiguous between {', '.join(tied)}")
        return None
    report.warnings.append(f"schema type was not supplied; inferred '{best.cfg.name}' "
                           f"from the fields present (similarity {best_score:.2f})")
    return best


def record_fit_error(flat: Dict[str, Any], ctx: Context) -> Optional[str]:
    """For a record WITHOUT an explicit type: an error message if it does not fit the chosen schema
    on its own (score below min_schema_score), else None. Stops an outlier being absorbed by a
    dataset-level decision."""
    score = structural_score(build_profile([flat]), ctx)
    minimum = ctx.settings["min_schema_score"]
    if score < minimum:
        return (f"record does not fit schema '{ctx.cfg.name}' "
                f"(similarity {score:.2f}, minimum {minimum:.2f})")
    return None


def rows_to_document(flat_rows: List[Dict[str, Any]], ctx: Context,
                     discriminator_key: Optional[str]) -> Dict[str, Any]:
    """CSV layout "lines": fold all rows into ONE document. Columns that map to document-level fields
    and hold the same value on every row are hoisted to the document (as is the type column); the
    remaining columns of each row become one line item under the schema's first container name."""
    outcome = map_request_keys_to_fields(flat_rows[0], ctx.cfg.document_fields, ctx, True)
    hoisted: Dict[str, Any] = {}
    for match in outcome.matches.values():
        if len({row.get(match.request_key) for row in flat_rows}) == 1:
            hoisted[match.request_key] = flat_rows[0][match.request_key]
    if discriminator_key and discriminator_key in flat_rows[0]:
        hoisted[discriminator_key] = flat_rows[0][discriminator_key]
    document = dict(hoisted)
    document[ctx.primary_container_name] = [{k: v for k, v in row.items() if k not in hoisted}
                                            for row in flat_rows]
    return document
