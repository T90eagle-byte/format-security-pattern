from __future__ import annotations

import codecs
import csv
import io
import json
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException


VALID = "VALID"
INVALID = "INVALID"
UNSUPPORTED = "UNSUPPORTED"

EMPTY_FILE = "EMPTY_FILE"
INVALID_JSON = "INVALID_JSON"
INVALID_XML = "INVALID_XML"
INVALID_CSV = "INVALID_CSV"
UNSUPPORTED_FORMAT = "UNSUPPORTED_FORMAT"

CSV_DELIMITERS = ",;\t"


ValidationResult = tuple[str, str | None]


def _is_empty(data: bytes) -> bool:
    return not data or not data.strip()


def validate_json(data: bytes) -> ValidationResult:
    if _is_empty(data):
        return INVALID, EMPTY_FILE

    try:
        json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return INVALID, INVALID_JSON

    return VALID, None


def validate_xml(data: bytes) -> ValidationResult:
    if _is_empty(data):
        return INVALID, EMPTY_FILE

    try:
        ElementTree.fromstring(data)
    except (
        DefusedXmlException,
        ParseError,
        UnicodeDecodeError,
        ValueError,
        LookupError,
    ):
        return INVALID, INVALID_XML

    return VALID, None


def _decode_csv(data: bytes) -> str | None:
    if data.startswith(codecs.BOM_UTF8):
        encoding = "utf-8-sig"
    elif data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        encoding = "utf-32"
    elif data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        encoding = "utf-16"
    else:
        encoding = "utf-8"

    try:
        return data.decode(encoding)
    except UnicodeDecodeError:
        return None


def validate_csv(data: bytes) -> ValidationResult:
    if _is_empty(data):
        return INVALID, EMPTY_FILE

    text = _decode_csv(data)
    if text is None or "\x00" in text:
        return INVALID, INVALID_CSV

    meaningful_lines = [line for line in text.splitlines() if line.strip()]
    if not meaningful_lines:
        return INVALID, EMPTY_FILE

    sample = "\n".join(meaningful_lines[:50])
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=CSV_DELIMITERS)
        rows = list(csv.reader(io.StringIO(text), dialect=dialect, strict=True))
    except csv.Error:
        return INVALID, INVALID_CSV

    meaningful_rows = [
        row
        for row in rows
        if row and any(cell.strip() for cell in row)
    ]
    if not meaningful_rows:
        return INVALID, EMPTY_FILE

    column_counts = [len(row) for row in meaningful_rows]
    if column_counts[0] < 2 or len(set(column_counts)) != 1:
        return INVALID, INVALID_CSV

    return VALID, None


def validate_structure(data: bytes, detected_format: str) -> ValidationResult:
    if _is_empty(data):
        return INVALID, EMPTY_FILE

    validators = {
        "JSON": validate_json,
        "XML": validate_xml,
        "CSV": validate_csv,
    }
    validator = validators.get(detected_format.strip().upper())
    if validator is None:
        return UNSUPPORTED, UNSUPPORTED_FORMAT

    return validator(data)
