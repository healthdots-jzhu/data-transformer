"""Result objects returned to the caller."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TransformResult:
    """Outcome for ONE document/record."""
    is_valid: bool = False
    schema: Optional[str] = None
    record_index: int = 0                                  # 1-based position in the source
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    normalized: Dict[str, Any] = field(default_factory=dict)   # converted output JSON
    field_mapping: Dict[str, str] = field(default_factory=dict)  # source key -> canonical field
    unmapped_fields: List[str] = field(default_factory=list)     # source keys that were ignored
    languages_detected: List[str] = field(default_factory=list)  # languages the source keys were in
    translations: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    number_locale: Optional[str] = None   # number format used for this document: "en" | "fr" | "mixed"
    # translations: field -> {language: raw value} for "translatable" fields given in several languages
    # (e.g. {"line 1 > product_name": {"en": "Frame", "fr": "Monture"}}). The output holds the
    # preferred language's value.


@dataclass
class TransformReport:
    """Outcome for a whole dataset. `errors` are dataset-level (unreadable source, mixed types, no
    matching schema); per-record problems live in `results[i].errors`."""
    source_format: Optional[str] = None
    schema: Optional[str] = None
    schema_scores: Dict[str, float] = field(default_factory=dict)   # similarity per candidate schema
    languages_detected: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    results: List[TransformResult] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """True only if there are no dataset errors and every record is valid."""
        return not self.errors and bool(self.results) and all(r.is_valid for r in self.results)

    @property
    def output(self) -> List[Dict[str, Any]]:
        """Normalised JSON of the valid records only."""
        return [r.normalized for r in self.results if r.is_valid]

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["is_valid"] = self.is_valid
        return data
