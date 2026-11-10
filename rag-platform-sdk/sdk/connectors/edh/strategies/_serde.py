"""Core-library record serialization and deserialization (JSONL, CSV, Gzip)."""

from __future__ import annotations

import csv
import gzip
import io
import json
from typing import Any, Iterator, Mapping, Sequence

from sdk.connectors.edh.errors import EdhConfigError
from sdk.models.edh_models import RecordFormat


def serialize_records(
    records: Sequence[Mapping[str, Any]],
    record_format: RecordFormat = RecordFormat.JSONL,
    *,
    compress_gzip: bool = False,
) -> tuple[bytes, str]:
    """Serialize ``records`` into bytes and return ``(payload_bytes, content_type)``."""
    if record_format == RecordFormat.JSONL:
        lines = [json.dumps(dict(row), ensure_ascii=False, sort_keys=True) for row in records]
        raw = ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
        content_type = "application/x-ndjson"
    elif record_format == RecordFormat.CSV:
        if not records:
            raw = b""
        else:
            fieldnames = list(records[0].keys())
            buf = io.StringIO()
            writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
            writer.writeheader()
            for row in records:
                writer.writerow({k: row.get(k, "") for k in fieldnames})
            raw = buf.getvalue().encode("utf-8")
        content_type = "text/csv"
    else:  # pragma: no cover
        raise EdhConfigError(f"Unsupported record format: {record_format!r}")

    if compress_gzip:
        raw = gzip.compress(raw)
        content_type = f"{content_type}+gzip"
    return raw, content_type


def deserialize_records(
    payload: bytes,
    record_format: RecordFormat = RecordFormat.JSONL,
    *,
    compressed_gzip: bool | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield dictionaries from ``payload`` in JSONL or CSV format."""
    if not payload:
        return

    is_gzip = (
        compressed_gzip
        if compressed_gzip is not None
        else payload[:2] == b"\x1f\x8b"
    )
    raw = gzip.decompress(payload) if is_gzip else payload
    text = raw.decode("utf-8")

    if record_format == RecordFormat.JSONL:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                yield json.loads(stripped)
    elif record_format == RecordFormat.CSV:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            yield dict(row)
    else:  # pragma: no cover
        raise EdhConfigError(f"Unsupported record format: {record_format!r}")
