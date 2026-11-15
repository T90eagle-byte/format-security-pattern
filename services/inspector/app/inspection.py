from __future__ import annotations

import codecs
import csv
import io
import re
from pathlib import PurePath


UNKNOWN = "UNKNOWN"
METADATA_MISMATCH = "METADATA_MISMATCH"
FORMAT_NOT_DETECTED = "FORMAT_NOT_DETECTED"
FORMAT_MISMATCH = "FORMAT_MISMATCH"

EXTENSION_FORMATS = {
    ".json": "JSON",
    ".xml": "XML",
    ".csv": "CSV",
}

MIME_FORMATS = {
    "application/json": "JSON",
    "text/json": "JSON",
    "application/xml": "XML",
    "text/xml": "XML",
    "text/csv": "CSV",
    "application/csv": "CSV",
}

GENERIC_MIME_TYPES = {"", "application/octet-stream"}
CSV_DELIMITERS = (",", ";", "\t")
XML_START_PATTERN = re.compile(
    r"<[A-Za-z_][A-Za-z0-9_.:-]*(?:\s[^<>]*?)?/?>",
    re.DOTALL,
)


def detect_declared_format(
    filename: str | None,
    content_type: str | None,
) -> tuple[str, str | None]:
    """Infer the declared format and report contradictory trusted metadata."""

    suffix = PurePath(filename or "").suffix.lower()
    extension_format = EXTENSION_FORMATS.get(suffix)

    normalized_mime = (content_type or "").partition(";")[0].strip().lower()
    mime_format = MIME_FORMATS.get(normalized_mime)

    if extension_format and mime_format and extension_format != mime_format:
        return extension_format, METADATA_MISMATCH

    if extension_format:
        return extension_format, None

    if mime_format:
        return mime_format, None

    # Missing, generic and unsupported MIME types are all non-authoritative.
    if normalized_mime in GENERIC_MIME_TYPES or normalized_mime:
        return UNKNOWN, None

    return UNKNOWN, None


def _decode_text(data: bytes) -> str | None:
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


def _looks_like_csv(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False

    sample = "\n".join(lines[:50])
    for delimiter in CSV_DELIMITERS:
        try:
            rows = list(csv.reader(io.StringIO(sample), delimiter=delimiter))
        except csv.Error:
            continue

        column_counts = [len(row) for row in rows]
        if (
            len(column_counts) >= 2
            and column_counts[0] >= 2
            and len(set(column_counts)) == 1
        ):
            return True

    return False


def detect_actual_format(data: bytes) -> str:
    """Classify content by signatures without performing full validation."""

    if not data:
        return UNKNOWN

    text = _decode_text(data)
    if text is None:
        return UNKNOWN

    content = text.lstrip("\ufeff \t\r\n")
    if not content:
        return UNKNOWN

    if content[0] in "{[":
        return "JSON"

    if content.lower().startswith("<?xml") or XML_START_PATTERN.match(content):
        return "XML"

    if _looks_like_csv(content):
        return "CSV"

    return UNKNOWN


def compare_formats(
    declared_format: str,
    detected_format: str,
) -> tuple[bool, str | None]:
    if detected_format == UNKNOWN:
        return False, FORMAT_NOT_DETECTED

    if declared_format != detected_format:
        return False, FORMAT_MISMATCH

    return True, None
