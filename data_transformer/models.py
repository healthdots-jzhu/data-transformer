"""Core data structures shared by every module: the parsed configuration (SchemaConfig/FieldSpec)
and the per-run Context, which resolves all language-dependent lists for the ACTIVE languages."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, FrozenSet, Iterable, List, Optional, Set, Tuple

from .defaults import NUMBER_FORMAT_LANGUAGES
import copy
from .localization import WILDCARD, Localized, active_items, active_words
from .normalization import AbbreviationRule, NormalizedKey, fold_text, normalize_key


@dataclass
class FieldSpec:
    """One canonical output field (an entry of "document_fields" or "line_fields")."""
    name: str                               # canonical name in the output JSON
    validator: str                          # key in registry.FIELD_VALIDATORS
    required: bool = True
    params: Dict[str, Any] = field(default_factory=dict)   # validator parameters
    owner: Optional[str] = None             # party this value belongs to ("patient", "provider", ...)
    explicit_owner: bool = False            # True: a bare "phone" is NOT enough; key must name the owner
    inherit: bool = False                   # line field that may take a shared document-level value
    warn_if_missing: bool = False           # optional, but worth a warning when absent
    aliases: Localized = field(default_factory=dict)       # strong, descriptive names per language
    weak_aliases: Localized = field(default_factory=dict)  # generic names per language (exact + warning)
    signature: bool = False                 # distinctive field used for schema detection
    sums_to_total: bool = False             # line field that adds up to the total field (when both exist)
    total_equivalent: bool = False          # line field equal to the total for 1-line documents
    translatable: bool = False              # free text that may legitimately appear in several languages


@dataclass
class SchemaConfig:
    """A fully parsed, validated schema configuration (see config_loader for the file format).
    Language-dependent lists are stored as Localized dicts {language: [words]}."""
    name: str
    source_formats: FrozenSet[str]
    settings: Dict[str, Any]
    languages: Tuple[str, ...]                       # languages this schema accepts (order = preference)
    default_language: str                            # language of the plain lists in the config file
    language_tags: Dict[str, List[str]]              # tokens that tag a key with a language
    stopwords: Localized
    abbreviation_rules: Dict[str, Tuple[AbbreviationRule, ...]]   # by language
    disqualifying_words: Localized
    owner_roles: Dict[str, Localized]                # role -> words per language
    neutral_words: Localized
    generic_aliases: Localized
    generic_line_containers: Localized
    limits: Dict[str, Tuple[float, float]]
    vocabularies: Dict[str, Dict[str, Dict[str, Any]]]   # name -> term -> {aliases: Localized, ...attrs}
    discriminator: Dict[str, Localized]              # keys: aliases, weak_aliases, values
    line_containers: Localized
    document_fields: List[FieldSpec]
    line_fields: List[FieldSpec]
    total_field: Optional[str]
    rules: List[Dict[str, Any]]
    output: Dict[str, str]
    target_schema: Optional[Dict[str, Any]]
    csv: Dict[str, Any]
    source_path: Optional[str] = None
    target_validator: Any = None            # compiled jsonschema validator, set at load time

    @property
    def has_lines(self) -> bool:
        """True only if this schema defines line-item fields. Line-item detection, validation and
        output are skipped entirely for schemas where this is False (e.g. a simple person record)."""
        return bool(self.line_fields)

    @property
    def all_specs(self) -> List[FieldSpec]:
        """Document-level fields followed by line-level fields."""
        return self.document_fields + self.line_fields

    def document_spec(self, name: Optional[str]) -> Optional[FieldSpec]:
        """The document-level FieldSpec called `name`, or None."""
        return next((s for s in self.document_fields if s.name == name), None)


def _fold_rule(rule: AbbreviationRule) -> AbbreviationRule:
    """Accent-fold and lower-case every word of a rule so it compares with normalised tokens."""
    return AbbreviationRule(
        fold_text(rule.abbreviation), fold_text(rule.expansion), rule.confidence,
        frozenset(fold_text(w) for w in rule.required_previous_tokens),
        frozenset(fold_text(w) for w in rule.required_next_tokens))


class Context:
    """Everything a validator/matcher needs for ONE schema in ONE run. Resolves the language-dependent
    parts of the config for the active languages once, so matching code works with plain lists."""

    def __init__(self, cfg: SchemaConfig, today: date, languages: Optional[Iterable[str]] = None):
        self.cfg = cfg
        self.today = today
        ordered = tuple(languages) if languages else cfg.languages
        self.languages: FrozenSet[str] = frozenset(ordered)
        self.preferred_language: str = cfg.settings.get("preferred_language") or ordered[0]
        self.document_languages: FrozenSet[str] = frozenset()

        self.stopwords: Tuple[str, ...] = tuple(dict.fromkeys(
            fold_text(w) for w in active_words(cfg.stopwords, self.languages)))
        self.abbreviation_rules: Tuple[AbbreviationRule, ...] = tuple(
            _fold_rule(r) for lang, rules in cfg.abbreviation_rules.items()
            if lang in self.languages or lang == WILDCARD for r in rules)
        minimum = cfg.settings["abbreviation_confidence_without_warning"]
        self.safe_rules: Tuple[AbbreviationRule, ...] = tuple(
            r for r in self.abbreviation_rules if r.confidence >= minimum)
        self.disqualifying_token_sets: List[FrozenSet[str]] = [
            frozenset(normalize_key(p, (), self.stopwords).tokens)
            for p in active_words(cfg.disqualifying_words, self.languages)]
        self.owner_roles: Dict[str, Set[str]] = {
            role: {fold_text(w) for w in active_words(loc, self.languages)}
            for role, loc in cfg.owner_roles.items()}
        self.neutral_words: Set[str] = {fold_text(w) for w in active_words(cfg.neutral_words, self.languages)}
        self.generic_aliases: Set[str] = {
            fold_text(w) for w in active_words(cfg.generic_aliases, self.languages)}
        self.line_container_names: List[str] = (active_words(cfg.line_containers, self.languages)
                                                + active_words(cfg.generic_line_containers, self.languages))
        self.language_tag_map: Dict[str, str] = {
            fold_text(tag): lang for lang in self.languages for tag in cfg.language_tags.get(lang, [])}
        self._norm: Dict[str, NormalizedKey] = {}
        self._norm_plain: Dict[str, NormalizedKey] = {}
        self._norm_safe: Dict[str, NormalizedKey] = {}
        self._vocab: Dict[str, Dict[str, List[str]]] = {}

    @property
    def settings(self) -> Dict[str, Any]:
        return self.cfg.settings

    def norm(self, text: str) -> NormalizedKey:
        """Normalise a KEY with this run's abbreviation rules and stop-words (memoised)."""
        if text not in self._norm:
            self._norm[text] = normalize_key(text, self.abbreviation_rules, self.stopwords)
        return self._norm[text]

    def norm_plain(self, text: str) -> NormalizedKey:
        """Normalise WITHOUT abbreviation expansion (used for free-text VALUES, not keys)."""
        if text not in self._norm_plain:
            self._norm_plain[text] = normalize_key(text, (), self.stopwords)
        return self._norm_plain[text]

    def norm_safe(self, text: str) -> NormalizedKey:
        """Normalise using only high-confidence abbreviation rules (for generic-alias comparisons)."""
        if text not in self._norm_safe:
            self._norm_safe[text] = normalize_key(text, self.safe_rules, self.stopwords)
        return self._norm_safe[text]

    def aliases(self, spec: FieldSpec) -> List[Tuple[str, str]]:
        """The spec's strong aliases as [(alias, language)] for the active languages."""
        return active_items(spec.aliases, self.languages)

    def weak_aliases(self, spec: FieldSpec) -> List[Tuple[str, str]]:
        """The spec's weak (generic) aliases as [(alias, language)] for the active languages."""
        return active_items(spec.weak_aliases, self.languages)

    def vocabulary(self, name: str) -> Dict[str, List[str]]:
        """Vocabulary `name` as {canonical term: [canonical term, aliases in the active languages]}."""
        if name not in self._vocab:
            self._vocab[name] = {
                term: [term] + active_words(meta.get("aliases", {}), self.languages)
                for term, meta in self.cfg.vocabularies[name].items()}
        return self._vocab[name]

    @property
    def primary_container_name(self) -> str:
        """Name used when the engine must create a line-item array itself (CSV layout 'lines')."""
        names = active_words(self.cfg.line_containers, self.languages)
        return names[0] if names else "lines"

    def for_document(self, languages: Iterable[str]) -> "Context":
        """A view of this context for ONE document, carrying the languages evidenced by that document's
        keys. Shares all caches with the original; the original is not modified."""
        clone = copy.copy(self)
        clone.document_languages = frozenset(languages)
        return clone

    @property
    def number_locale(self) -> str:
        """Number format for amounts and numbers in the current document:
           settings.number_locale if not "auto"; otherwise from the languages the document's keys
           use (limited to the active languages): only French -> "fr"; only English -> "en";
           both, or no language evidence at all while both are active -> "mixed".
        "fr" means decimal comma and no comma-thousands; "mixed" disambiguates per value."""
        override = self.settings.get("number_locale", "auto")
        if override != "auto":
            return override
        relevant = (self.document_languages & self.languages) or self.languages
        formats = relevant & NUMBER_FORMAT_LANGUAGES
        if formats == frozenset({"fr"}):
            return "fr"
        if len(formats) == 2:
            return "mixed"
        return "en"
