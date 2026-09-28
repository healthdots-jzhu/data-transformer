"""Real encoded fixtures exercise file parsing and its data-loss safeguards."""
from pathlib import Path

import pytest

from data_transformer import SourceError
from data_transformer.source_readers import decode_bytes, load_source, _is_file, _parse_csv


@pytest.mark.parametrize("raw,format,expected", [
    ({"a": 1}, None, [{"a": 1}]), ([{"a": 1}], None, [{"a": 1}]),
    ('{"a": 1}', None, [{"a": 1}]), ('[{"a": 1}]', "json", [{"a": 1}]),
    (bytearray(b'{"a":1}'), None, [{"a": 1}]),
    ("a,b\n1,2\n\n,,\n3,4,,\n", "csv", [{"a": "1", "b": "2"}, {"a": "3", "b": "4"}]),
    ("a,,b\n1,,2\n", "csv", [{"a": "1", "b": "2"}]),
    ("a\tb\n1\t2\n", None, [{"a": "1", "b": "2"}]),
    ("<root><a>1</a><b>2</b></root>", None, [{"a": "1", "b": "2"}]),
    ("<root>hello</root>", "xml", [{"root": "hello"}]),
    ('<root id="1">hello</root>', "xml", [{"id": "1", "#text": "hello"}]),
    ('<root id="1"/>', "xml", [{"id": "1"}]),
    ('<root xmlns:n="urn:test"><n:a>1</n:a><b>2</b></root>', "xml", [{"a": "1", "b": "2"}]),
    ('<root><a>1</a><b>2</b><a>3</a><a>4</a></root>', "xml", [{"a": ["1", "3", "4"], "b": "2"}]),
    ('<batch><record><a>1</a></record><record><a>2</a></record></batch>', "xml", [{"a": "1"}, {"a": "2"}]),
    ('<items><item><a>1</a></item></items>', "xml", [{"items": [{"a": "1"}]}]),
])
def test_supported_sources(raw, format, expected):
    _, records, warnings = load_source(raw, format, None, {"items"})
    assert records == expected
    assert warnings == []


@pytest.mark.parametrize("raw,format,message", [
    ({}, "xml", "only be used as json"), (42, None, "unsupported source type"),
    ("", None, "source is empty"), ("\ufeff  ", "json", "source is empty"),
    ("<bad>", "xml", "not valid XML"), ('{"bad":}', "json", "not valid JSON"),
    ("anything", "pdf", "unsupported source format"),
    ("a,b\n1\n", "csv", "header has 2"), ("a,,b\n1,orphan,2\n", "csv", "no header"),
    (",\n1,2\n", "csv", "no column names"), ("a,b\n", "csv", "no data rows"),
    ("a,b\n" + "1\n" * 11, "csv", "and 1 more"),
])
def test_source_errors_are_descriptive(raw, format, message):
    with pytest.raises(SourceError, match=message):
        load_source(raw, format, None, set())


def test_empty_csv_reader_error():
    with pytest.raises(SourceError, match="CSV is empty"):
        _parse_csv("", None)


@pytest.mark.parametrize("encoding,note", [("utf-8-sig", None), ("utf-16", "UTF-16"),
                                         ("cp1252", "Windows-1252")])
def test_decoding_and_file_extension_detection(tmp_path, encoding, note):
    path = tmp_path / "input.json"
    path.write_bytes('{"nom":"Élodie"}'.encode(encoding))
    format, records, warnings = load_source(str(path), None, None, set())
    assert format == "json" and records == [{"nom": "Élodie"}]
    assert (any(note in warning for warning in warnings) if note else not warnings)


def test_latin1_fallback_and_declared_xml_encoding():
    text, note = decode_bytes(b"\x81")
    assert text == "\x81" and "ISO-8859-1" in note
    xml = '<?xml version="1.0" encoding="ISO-8859-1"?><root><nom>Élodie</nom></root>'
    assert load_source(xml.encode("latin-1"), "xml", None, set())[1] == [{"nom": "Élodie"}]


def test_utf16_empty_document_is_rejected():
    with pytest.raises(SourceError, match="source is empty"):
        load_source("".encode("utf-16"), "json", None, set())


def test_path_probe_handles_operating_system_errors(monkeypatch):
    def inaccessible_path(_path):
        raise OSError("inaccessible")
    monkeypatch.setattr(Path, "is_file", inaccessible_path)
    assert not _is_file("unavailable.json")


def test_xml_entities_are_rejected():
    source = '<!DOCTYPE root [<!ENTITY secret "expanded">]><root>&secret;</root>'
    with pytest.raises(SourceError, match="not valid XML"):
        load_source(source, "xml", None, set())
