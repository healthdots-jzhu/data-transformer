"""Reading JSON, XML and CSV sources into a list of records (dicts). The readers are strict about
information loss (CSV that would drop or mis-assign values raises SourceError) and tolerant about
encodings: UTF-8 first, then UTF-16 (BOM), Windows-1252 and ISO-8859-1 (common for French Excel
exports), with a note returned to the caller."""
from __future__ import annotations

import csv
import io
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple, Union

try:  # hardened parser when available (entity-expansion / external-entity attacks)
    from defusedxml import ElementTree as ET
except ImportError:  # pragma: no cover
    import xml.etree.ElementTree as ET

from .errors import SourceError
from .normalization import fold_accents


def squash(name: str) -> str:
    """Lower-case, strip accents and non-alphanumerics ('Lignes de Service' == 'lignes_de_service')."""
    return re.sub(r"[^a-z0-9]", "", fold_accents(name).lower())


# ---------------------------------------------------------------------------
# encodings
# ---------------------------------------------------------------------------
def decode_bytes(data: bytes) -> Tuple[str, Optional[str]]:
    """Decode source bytes -> (text, note). Order: UTF-16 if a BOM is present, UTF-8 (with or without
    BOM), Windows-1252, ISO-8859-1. `note` is a warning text when a fallback was used."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "source is UTF-16 encoded"
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252"), "source is not UTF-8; decoded as Windows-1252 (cp1252)"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "source is not UTF-8; decoded as ISO-8859-1"


# ---------------------------------------------------------------------------
# XML
# ---------------------------------------------------------------------------
def _local(tag: str) -> str:
    """Tag without namespace."""
    return tag.rsplit("}", 1)[-1]


def _xml_value(el, hints: Set[str]) -> Any:
    """Convert an element to str | dict | list. Attributes become keys; repeated child tags become a
    list; a wrapper whose children all share one tag becomes a list (even for ONE child if the
    wrapper's name is a known line-container name such as <procedures> or <lignes>)."""
    children = list(el)
    attrs = {_local(k): v for k, v in el.attrib.items()}
    text = (el.text or "").strip()
    if not children:
        if not attrs:
            return text
        return {**attrs, **({"#text": text} if text else {})}
    tags = [_local(c.tag) for c in children]
    if not attrs and len(set(tags)) == 1 and (len(tags) > 1 or squash(_local(el.tag)) in hints):
        return [_xml_value(c, hints) for c in children]
    obj: Dict[str, Any] = dict(attrs)
    repeated: Set[str] = set()
    for child, tag in zip(children, tags):
        value = _xml_value(child, hints)
        if tag in obj:
            obj[tag] = (obj[tag] + [value]) if tag in repeated else [obj[tag], value]
            repeated.add(tag)
        else:
            obj[tag] = value
    return obj


def _parse_xml(data: Union[str, bytes], hints: Set[str]) -> List[Any]:
    """Parse XML into records. Bytes are passed to the parser untouched so a declared encoding
    (e.g. ISO-8859-1) is honoured. The root element is an envelope and is dropped; a root that is itself
    a list of identical children is a batch of records (unless its name is a line container, in which
    case it is kept as one document)."""
    try:
        root = ET.fromstring(data)
    except Exception as error:  # ParseError / defusedxml exceptions
        raise SourceError(f"source is not valid XML: {error}") from error
    value = _xml_value(root, hints)
    root_tag = _local(root.tag)
    if isinstance(value, list):
        return [{root_tag: value}] if squash(root_tag) in hints else value
    return [value] if isinstance(value, dict) else [{root_tag: value}]


# ---------------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------------
def _detect_delimiter(text: str) -> str:
    """Choose the delimiter (, ; tab |) that splits the header row into the most columns; ties prefer
    the comma. Works for French exports, which use ';' because ',' is the decimal mark."""
    lines = text.splitlines()
    first_line = lines[0] if lines else ""
    best, best_columns = ",", 1
    for delimiter in (",", ";", "\t", "|"):
        try:
            columns = len(next(csv.reader([first_line], delimiter=delimiter)))
        except (csv.Error, StopIteration):
            columns = 1
        if columns > best_columns:
            best, best_columns = delimiter, columns
    return best


def _parse_csv(text: str, delimiter: Optional[str]) -> List[Dict[str, Any]]:
    """Parse CSV with csv.reader (not DictReader) so nothing is lost silently. Raises SourceError for:
      * duplicate column names            (a dict would keep only the last value)
      * values beyond the header columns  (DictReader would discard them)
      * non-empty values under a blank header
      * rows with fewer values than the header (ragged/truncated rows)
    Blank lines are skipped; trailing empty cells beyond the header are tolerated."""
    delimiter = delimiter or _detect_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        raise SourceError("CSV is empty")
    names = [h.strip() for h in header]
    if not any(names):
        raise SourceError("CSV header row has no column names")

    problems: List[str] = []
    positions: Dict[str, List[int]] = {}
    for position, name in enumerate(names, start=1):
        if name:
            positions.setdefault(name, []).append(position)
    for name, where in positions.items():
        if len(where) > 1:
            problems.append(f"duplicate column '{name}' (columns {', '.join(map(str, where))})")

    named = [i for i, name in enumerate(names) if name]
    rows: List[Dict[str, Any]] = []
    for raw in reader:
        if not any(cell.strip() for cell in raw):
            continue
        line = reader.line_num
        if len(raw) > len(names) and any(c.strip() for c in raw[len(names):]):
            problems.append(f"line {line}: {len(raw) - len(names)} value(s) beyond the "
                            f"{len(names)} header column(s)")
            continue
        if len(raw) < len(names):
            problems.append(f"line {line}: {len(raw)} value(s) but the header has {len(names)} columns")
            continue
        orphan = next((i for i in range(len(names)) if not names[i] and raw[i].strip()), None)
        if orphan is not None:
            problems.append(f"line {line}: value in column {orphan + 1}, which has no header")
            continue
        rows.append({names[i]: raw[i].strip() for i in named})

    if problems:
        shown = "; ".join(problems[:10]) + (f"; ... and {len(problems) - 10} more" if len(problems) > 10 else "")
        raise SourceError(f"CSV would lose or mis-assign data: {shown}")
    if not rows:
        raise SourceError("CSV contains no data rows")
    return rows


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------
def _is_file(text: str) -> bool:
    """True if `text` looks like, and is, an existing file path (never raises)."""
    try:
        return "\n" not in text and len(text) < 1024 and Path(text).is_file()
    except OSError:
        return False


def load_source(source: Any, source_format: Optional[str], delimiter: Optional[str],
                hints: Set[str]) -> Tuple[str, List[Any], List[str]]:
    """Resolve `source` (path, raw text, bytes, dict or list) and return (format, records, notes).
    Format comes from `source_format`, else the file extension, else content sniffing
    ('<' = XML, '{' or '[' = JSON, anything else = CSV). `hints` = squashed names of line containers
    (all languages), used to keep single-child XML wrappers as lists. `notes` are warnings such as a
    non-UTF-8 decoding. Raises SourceError on unreadable data."""
    notes: List[str] = []
    fmt = source_format.lower() if source_format else None
    if isinstance(source, (dict, list)):
        if fmt not in (None, "json"):
            raise SourceError(f"a Python object can only be used as json, not {fmt}")
        return "json", source if isinstance(source, list) else [source], notes

    raw: Union[bytes, str]
    if isinstance(source, Path) or (isinstance(source, str)
                                    and not source.lstrip().startswith(("<", "{", "["))
                                    and _is_file(source)):
        path = Path(source)
        fmt = fmt or {".xml": "xml", ".json": "json", ".csv": "csv"}.get(path.suffix.lower())
        raw = path.read_bytes()
    elif isinstance(source, (bytes, bytearray)):
        raw = bytes(source)
    elif isinstance(source, str):
        raw = source
    else:
        raise SourceError(f"unsupported source type {type(source).__name__}")

    probe = raw if isinstance(raw, str) else raw[:4096].decode("utf-8", errors="ignore")
    probe = probe.lstrip("\ufeff").lstrip()
    if not probe and not (isinstance(raw, bytes) and raw.startswith((b"\xff\xfe", b"\xfe\xff"))):
        raise SourceError("source is empty")
    if fmt is None:
        fmt = "xml" if probe.startswith("<") else "json" if probe[:1] in ("{", "[") else "csv"

    if fmt == "xml":
        data = raw.lstrip() if isinstance(raw, str) else raw
        return "xml", _parse_xml(data, hints), notes

    if isinstance(raw, bytes):
        text, note = decode_bytes(raw)
        if note:
            notes.append(note)
    else:
        text = raw
    stripped = text.lstrip("\ufeff").lstrip()
    if not stripped:
        raise SourceError("source is empty")
    if fmt == "json":
        try:
            data = json.loads(stripped)
        except ValueError as error:
            raise SourceError(f"source is not valid JSON: {error}") from error
        return "json", data if isinstance(data, list) else [data], notes
    if fmt == "csv":
        return "csv", _parse_csv(stripped, delimiter), notes
    raise SourceError(f"unsupported source format '{fmt}'")
