"""Mapping source keys to canonical fields.

Matching principles (all configurable per schema):
  * STRONG aliases are descriptive ("date of service" / "date de service"): fuzzy-matched, but the word
    count must be identical and every word must be close - "paid expense amount" is not "expense amount".
  * WEAK aliases are generic ("amount", "montant", "date"): the key must EQUAL the alias after safe
    normalisation; they never outrank strong matches and always produce a warning.
  * DISQUALIFYING words ("max", "tax", "approved", "taxe"...) turn a key into a different quantity.
  * OWNERSHIP: "provider > phone" can never fill a patient-owned field, and vice versa.
  * A key whose parent segment is unknown ("customer > address") is refused unless it matches a
    descriptive alias exactly.
  * LANGUAGES: aliases exist per language; accents are ignored; language tags ("name_fr",
    {"name": {"fr": ...}}) are read and removed. Every match remembers the language(s) it was made in.
  * BILINGUAL LABELS ("Price / Prix") are split into language variants and EVERY variant must map to
    the same field. "Min amount / Montant" is therefore NOT a price: "min amount" is a different
    quantity, so the key is refused as a whole (a warning names the disagreeing variant).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Iterable, List, NamedTuple, Optional, Sequence, Set, Tuple

from .models import Context, FieldSpec
from .normalization import AbbreviationExpansion, NormalizedKey, fold_plurals, text_similarity, text_variants
from .paths import PATH_SEP, split_path


# ---------------------------------------------------------------------------
# key variants and language tags
# ---------------------------------------------------------------------------
def key_variants(request_key: str) -> List[str]:
    """Bilingual key -> one key per language variant of its LAST path segment:
    'patient > Name / Nom' -> ['patient > Name', 'patient > Nom']. Other keys -> [key]."""
    segments = split_path(request_key)
    if not segments:
        return [request_key]
    parts = text_variants(segments[-1])
    if len(parts) == 1:
        return [request_key]
    return [PATH_SEP.join(segments[:-1] + [p]) for p in parts]


def strip_language_tag(key: NormalizedKey, tag_map: Dict[str, str]) -> Tuple[NormalizedKey, Optional[str]]:
    """Remove a leading/trailing language tag token ('description fr' -> 'description', 'fr').
    Only for keys with at least two tokens; returns the key and the language (or None)."""
    tokens = list(key.tokens)
    if len(tokens) >= 2:
        if tokens[-1] in tag_map:
            language, tokens = tag_map[tokens[-1]], tokens[:-1]
        elif tokens[0] in tag_map:
            language, tokens = tag_map[tokens[0]], tokens[1:]
        else:
            return key, None
        return NormalizedKey(" ".join(tokens), tuple(tokens), key.expansions), language
    return key, None


# ---------------------------------------------------------------------------
# key path analysis
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class KeyPathAnalysis:
    """What a (possibly nested) key says about itself."""
    request_key: str
    full_key: NormalizedKey                 # the whole path: "provider > phone" -> "provider phone"
    leaf_key: Optional[NormalizedKey]       # last segment only; None for a top-level key
    all_owner_roles: FrozenSet[str]         # parties named anywhere in the path
    parent_owner_roles: FrozenSet[str]      # parties named by parent segments
    unrecognised_parent_segments: Tuple[str, ...]  # parents that are neither an owner nor neutral
    language: Optional[str] = None          # language tag found in the key, if any


def owner_roles_in_tokens(tokens: Tuple[str, ...], ctx: Context) -> FrozenSet[str]:
    """Owner roles (e.g. 'patient') whose keyword list (active languages) contains any of the tokens."""
    return frozenset(role for role, words in ctx.owner_roles.items() if any(t in words for t in tokens))


def analyze_key_path(request_key: str, ctx: Context) -> KeyPathAnalysis:
    """Split the key into segments and determine owners, unrecognised parents and language tags.
    A last segment that is only a language tag ('description > fr') is dropped and becomes the key's
    language. A parent is 'understood' if, apart from owner words, it only uses neutral container words."""
    segments = split_path(request_key)
    language: Optional[str] = None
    if len(segments) > 1:
        last = ctx.norm(segments[-1]).tokens
        if len(last) == 1 and last[0] in ctx.language_tag_map:
            language, segments = ctx.language_tag_map[last[0]], segments[:-1]
    full_key, tag_language = strip_language_tag(ctx.norm(PATH_SEP.join(segments)), ctx.language_tag_map)
    language = language or tag_language
    leaf_key, parent_roles, unrecognised = None, set(), []
    if len(segments) > 1:
        leaf_key, leaf_language = strip_language_tag(ctx.norm(segments[-1]), ctx.language_tag_map)
        language = language or leaf_language
        role_words = set().union(*ctx.owner_roles.values()) if ctx.owner_roles else set()
        for parent in segments[:-1]:
            tokens = ctx.norm(parent).tokens
            parent_roles |= owner_roles_in_tokens(tokens, ctx)
            rest = [t for t in tokens if t not in role_words]
            if not all(t in ctx.neutral_words for t in rest):
                unrecognised.append(parent)
    return KeyPathAnalysis(request_key, full_key, leaf_key, owner_roles_in_tokens(full_key.tokens, ctx),
                           frozenset(parent_roles), tuple(unrecognised), language)


def path_refusal_reason(analysis: KeyPathAnalysis, spec: FieldSpec, mode: str) -> Optional[str]:
    """A hard, explainable rejection: ownership disagreement, a required-but-missing owner word,
    or a leaf-only reading that would discard an unknown parent. None = acceptable."""
    roles = analysis.all_owner_roles
    if len(roles) > 1:
        return "the key names several parties; ownership is ambiguous"
    if roles and spec.owner and roles != {spec.owner}:
        return f"the key belongs to the {next(iter(roles))}, not the {spec.owner}"
    if spec.explicit_owner and roles != {spec.owner}:
        return f"the key does not identify the {spec.owner}"
    if mode == "leaf" and analysis.unrecognised_parent_segments:
        return f"unrecognised parent '{analysis.unrecognised_parent_segments[0]}' cannot be discarded"
    return None


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def alias_similarity(key_text: str, alias_text: str, ctx: Context) -> float:
    """Similarity of a normalised key to ONE alias: 0 unless the word counts are equal, no
    disqualifying word was added, and every word is close to a distinct alias word (order is free).
    Generic (one-word) aliases must match exactly."""
    key_tokens, alias_tokens = key_text.split(), alias_text.split()
    if not key_tokens or len(key_tokens) != len(alias_tokens):
        return 0.0
    for phrase_tokens in ctx.disqualifying_token_sets:
        if phrase_tokens <= set(key_tokens) and not phrase_tokens <= set(alias_tokens):
            return 0.0
    if fold_plurals(alias_text) in ctx.generic_aliases:
        return float(fold_plurals(key_text) == fold_plurals(alias_text))
    remaining, scores = list(alias_tokens), []
    for token in key_tokens:
        best = max(remaining, key=lambda a: text_similarity(token, a))
        score = text_similarity(token, best)
        if score < ctx.settings["fuzzy_threshold"]:
            return 0.0
        scores.append(score)
        remaining.remove(best)
    return sum(scores) / len(scores)


def generic_alias_match(key: NormalizedKey, aliases: Iterable[Tuple[str, str]], ctx: Context) -> Optional[str]:
    """Exact (plural-folded) equality of `key` with a generic alias. Returns the alias's language
    ("*" = language-neutral) or None if nothing matches. A key that relied on a low-confidence
    abbreviation expansion can never establish a generic match."""
    minimum = ctx.settings["abbreviation_confidence_without_warning"]
    if any(e.confidence < minimum for e in key.expansions):
        return None
    folded_key = fold_plurals(key.text)
    for alias, language in aliases:
        if folded_key == fold_plurals(ctx.norm_safe(alias).text):
            return language
    return None


class FieldScore(NamedTuple):
    score: float = 0.0                           # 0 = no acceptable match
    matched_via_weak_alias: bool = False
    refusal_reasons: Tuple[str, ...] = ()        # filled only when a match was found but refused
    language: Optional[str] = None               # language of the match (key tag or alias language)


def score_key_against_field(analysis: KeyPathAnalysis, spec: FieldSpec, ctx: Context,
                            use_weak: bool) -> FieldScore:
    """Score one key (ONE language variant) against one field, reading it as a full path and (for nested
    keys) as its last segment, against the aliases of every active language. Strong matches always
    outrank weak ones regardless of numeric score. `use_weak=False` is used during schema selection,
    where generic matches must not count."""
    s = ctx.settings
    strong = ctx.aliases(spec) + [(spec.name, "*")]
    weak = ctx.weak_aliases(spec) if use_weak else []
    readings = [("full", analysis.full_key, 1.0)]
    if analysis.leaf_key is not None:
        readings.append(("leaf", analysis.leaf_key, s["leaf_only_match_penalty"]))
    best, refusals = FieldScore(), []
    for mode, key, multiplier in readings:
        reason = path_refusal_reason(analysis, spec, mode)
        if reason:
            refusals.append(reason)
            continue
        similarity, alias_language = 0.0, "*"
        for alias, language in strong:
            alias_text = ctx.norm(alias).text
            if fold_plurals(alias_text) in ctx.generic_aliases:
                value = 1.0 if generic_alias_match(key, [(alias, language)], ctx) is not None else 0.0
            else:
                value = alias_similarity(key.text, alias_text, ctx)
            if value > similarity:
                similarity, alias_language = value, language
        score, via_weak = similarity * multiplier, False
        if score < s["fuzzy_threshold"]:
            weak_language = generic_alias_match(key, weak, ctx)
            if weak_language is None:
                continue
            score, via_weak, alias_language = s["weak_alias_confidence"] * multiplier, True, weak_language
        if mode == "full" and analysis.unrecognised_parent_segments and (via_weak or similarity != 1.0):
            refusals.append("unknown parent requires an exact descriptive alias")
            continue
        language = analysis.language or (alias_language if alias_language != "*" else None)
        if (best.score == 0 or (best.matched_via_weak_alias and not via_weak)
                or (best.matched_via_weak_alias == via_weak and score > best.score)):
            best = FieldScore(score, via_weak, (), language)
    return best if best.score else FieldScore(refusal_reasons=tuple(dict.fromkeys(refusals)))


# ---------------------------------------------------------------------------
# mapping a whole set of keys
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class FieldMatch:
    """The key that won a canonical field."""
    canonical_name: str
    request_key: str
    value: Any
    score: float
    matched_via_weak_alias: bool = False
    abbreviation_expansions: Tuple[AbbreviationExpansion, ...] = ()
    language: Optional[str] = None            # the single language of the key; None if none/several
    languages: Tuple[str, ...] = ()           # every language the key was matched in (evidence)


@dataclass(frozen=True)
class CompetingMatch:
    """Another key that also matched an already-won field (a duplicate or a conflict)."""
    canonical_name: str
    winning_key: str
    winning_value: Any
    losing_key: str
    losing_value: Any
    winning_language: Optional[str] = None
    losing_language: Optional[str] = None


@dataclass
class FieldMappingOutcome:
    matches: Dict[str, FieldMatch] = field(default_factory=dict)      # canonical name -> winner
    competing_matches: List[CompetingMatch] = field(default_factory=list)
    ambiguous_keys: List[str] = field(default_factory=list)           # keys with tied meanings
    refused_matches: Dict[str, List[str]] = field(default_factory=dict)  # key -> refusal reasons
    translations: Dict[str, Dict[str, Any]] = field(default_factory=dict)  # field -> {language: value}
    translated_keys: Set[str] = field(default_factory=set)            # keys used as a translation

    @property
    def consumed_keys(self) -> Set[str]:
        """Keys that were used, or explicitly reported as duplicate/conflict/translation."""
        return ({m.request_key for m in self.matches.values()}
                | {c.losing_key for c in self.competing_matches} | self.translated_keys)

    def languages(self) -> Set[str]:
        """Languages evidenced by the keys that were mapped (used to choose the document's number format)."""
        found: Set[str] = set()
        for match in self.matches.values():
            found.update(match.languages)
        for by_language in self.translations.values():
            found.update(by_language)
        for c in self.competing_matches:
            found.update(l for l in (c.winning_language, c.losing_language) if l)
        return found


class _Candidate(NamedTuple):
    score: float
    request_key: str
    canonical_name: str
    weak: bool
    expansions: Tuple[AbbreviationExpansion, ...]
    language: Optional[str]
    languages: Tuple[str, ...]


def map_request_keys_to_fields(request_fields: Dict[str, Any], specs: Sequence[FieldSpec],
                               ctx: Context, use_weak: bool,
                               excluded_keys: Iterable[str] = ()) -> FieldMappingOutcome:
    """Map keys to fields in three steps:
      1. score every (key, field) pair. A BILINGUAL key ('Price / Prix') is scored per language variant
         and matches a field only if EVERY variant matches it (score = the weakest variant, weak if any
         variant is weak); otherwise it is refused with an explanation. Refusals are remembered so
         unused keys can be explained;
      2. resolve each key's single best meaning (ties between fields -> ambiguous key);
      3. rank the keys competing for each field (strong before weak; for TRANSLATABLE fields the
         preferred language before others; then score). The winner is used. A loser in a DIFFERENT
         language on a translatable field is recorded as a translation; any other loser is reported as
         duplicate/conflict - never silently reassigned elsewhere.
    Keys whose value is a list/dict are skipped (they are containers, not values)."""
    excluded = set(excluded_keys)
    outcome = FieldMappingOutcome()
    spec_by_name = {s.name: s for s in specs}
    candidates: List[_Candidate] = []
    for request_key, value in request_fields.items():
        if request_key in excluded or isinstance(value, (list, dict)):
            continue
        analyses = [analyze_key_path(v, ctx) for v in key_variants(str(request_key))]
        for spec in specs:
            scored: List[Tuple[KeyPathAnalysis, FieldScore]] = []
            for analysis in analyses:
                fs = score_key_against_field(analysis, spec, ctx, use_weak)
                for reason in fs.refusal_reasons:
                    reasons = outcome.refused_matches.setdefault(request_key, [])
                    if reason not in reasons:
                        reasons.append(reason)
                scored.append((analysis, fs))
            matched = [(a, fs) for a, fs in scored if fs.score > 0]
            if not matched:
                continue
            if len(matched) < len(scored):
                # some language variants mean something else ("min amount / montant" is not a price)
                unmatched = [(split_path(a.request_key) or [a.request_key])[-1]
                             for a, fs in scored if fs.score <= 0]
                reason = (f"its language variants do not all mean '{spec.name}' "
                          f"({', '.join(repr(u) for u in unmatched)} does not)")
                reasons = outcome.refused_matches.setdefault(request_key, [])
                if reason not in reasons:
                    reasons.append(reason)
                continue
            languages = tuple(sorted({fs.language for _, fs in matched if fs.language}))
            candidates.append(_Candidate(
                min(fs.score for _, fs in matched), request_key, spec.name,
                any(fs.matched_via_weak_alias for _, fs in matched),
                tuple(dict.fromkeys(e for a, _ in matched for e in a.full_key.expansions)),
                languages[0] if len(languages) == 1 else None, languages))

    selected: List[_Candidate] = []
    for request_key in request_fields:
        mine = [c for c in candidates if c.request_key == request_key]
        if not mine:
            continue
        rank = min((c.weak, -c.score) for c in mine)
        best = [c for c in mine if (c.weak, -c.score) == rank]
        if len({c.canonical_name for c in best}) > 1:
            outcome.ambiguous_keys.append(request_key)
        else:
            selected.append(best[0])

    preferred = ctx.preferred_language

    def order(c: _Candidate):
        spec = spec_by_name[c.canonical_name]
        language_rank = 1 if (spec.translatable and c.language not in (None, preferred)) else 0
        return (c.weak, language_rank, -c.score, c.request_key)

    selected.sort(key=order)
    for c in selected:
        value = request_fields[c.request_key]
        winner = outcome.matches.get(c.canonical_name)
        if winner is None:
            outcome.matches[c.canonical_name] = FieldMatch(
                c.canonical_name, c.request_key, value, c.score, c.weak, c.expansions,
                c.language, c.languages)
            continue
        spec = spec_by_name[c.canonical_name]
        known = outcome.translations.get(c.canonical_name, {winner.language: winner.value})
        if (spec.translatable and winner.language and c.language and winner.language != c.language
                and c.language not in known):
            entry = outcome.translations.setdefault(c.canonical_name, {winner.language: winner.value})
            entry[c.language] = value
            outcome.translated_keys.add(c.request_key)
        else:
            outcome.competing_matches.append(CompetingMatch(
                c.canonical_name, winner.request_key, winner.value, c.request_key, value,
                winner.language, c.language))
    return outcome
