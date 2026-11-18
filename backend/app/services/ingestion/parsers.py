"""Document parsers: a registry of per-format parsers.

Built-in (standard library only): txt/md/log/rst/ini/yaml, csv/tsv, json/jsonl, xml,
html, docx, pptx, xlsx, eml, rtf.
PDF uses ``pypdf`` and falls back to OCR for scanned files; images require an OCR
provider (see ``app.services.ingestion.ocr``).
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from email import policy
from email.parser import BytesParser
from html.parser import HTMLParser
from typing import Callable

from sdk.errors import BadRequestError

from app.services.ingestion.ocr import OcrProvider

MAX_CSV_FIELD = 1_000_000


class UnsupportedDocumentError(BadRequestError):
    public_message = "Unsupported or unreadable document type"


# ----------------------------------------------------------------------- helpers
def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    return data.decode("utf-8", errors="replace")


def normalise_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _open_zip(data: bytes, kind: str) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnsupportedDocumentError(f"Invalid {kind} file") from exc


def _xml_root(raw: bytes) -> ET.Element:
    return ET.fromstring(raw)  # noqa: S314 - bounded local document parts


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# ----------------------------------------------------------------------- parsers
def parse_text(data: bytes) -> str:
    return _decode(data)


def parse_csv(data: bytes, delimiter: str | None = None) -> str:
    text = _decode(data)
    csv.field_size_limit(MAX_CSV_FIELD)
    if delimiter is None:
        try:
            delimiter = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|").delimiter
        except csv.Error:
            delimiter = ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delimiter) if any(c.strip() for c in r)]
    if not rows:
        return ""
    header, body = rows[0], rows[1:]
    if not body:
        return " | ".join(header)
    lines = []
    for row in body:
        lines.append(" | ".join(f"{h.strip()}: {v.strip()}" for h, v in zip(header, row) if v.strip()))
    return "\n".join(lines)


def parse_json(data: bytes) -> str:
    try:
        return json.dumps(json.loads(_decode(data)), indent=2, ensure_ascii=False)
    except ValueError as exc:
        raise UnsupportedDocumentError("Invalid JSON file") from exc


def parse_jsonl(data: bytes) -> str:
    out = []
    for line in _decode(data).splitlines():
        if line.strip():
            try:
                out.append(json.dumps(json.loads(line), ensure_ascii=False))
            except ValueError as exc:
                raise UnsupportedDocumentError("Invalid JSONL file") from exc
    return "\n".join(out)


def parse_xml(data: bytes) -> str:
    try:
        root = _xml_root(data)
    except ET.ParseError as exc:
        raise UnsupportedDocumentError("Invalid XML file") from exc
    return "\n".join(t.strip() for t in root.itertext() if t.strip())


class _HtmlText(HTMLParser):
    _SKIP = {"script", "style", "noscript"}
    _BREAK = {"p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "table", "section"}

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag in self._SKIP:
            self._skip += 1
        elif tag in self._BREAK:
            self._parts.append("\n")
        elif tag in {"td", "th"}:
            self._parts.append(" | ")

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in self._SKIP and self._skip:
            self._skip -= 1

    def handle_data(self, data):  # noqa: ANN001
        if not self._skip:
            self._parts.append(data)


def parse_html(data: bytes) -> str:
    p = _HtmlText()
    p.feed(_decode(data))
    return "".join(p._parts)


def parse_docx(data: bytes) -> str:
    with _open_zip(data, "DOCX") as zf:
        try:
            root = _xml_root(zf.read("word/document.xml"))
        except KeyError as exc:
            raise UnsupportedDocumentError("Invalid DOCX file") from exc
    paras = []
    for para in root.iter():
        if _local(para.tag) == "p":
            paras.append("".join(t.text or "" for t in para.iter() if _local(t.tag) == "t"))
    return "\n".join(paras)


def parse_pptx(data: bytes) -> str:
    with _open_zip(data, "PPTX") as zf:
        slides = sorted(
            (n for n in zf.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
            key=lambda n: int(re.findall(r"\d+", n)[0]),
        )
        out = []
        for i, name in enumerate(slides, 1):
            root = _xml_root(zf.read(name))
            texts = [t.text for t in root.iter() if _local(t.tag) == "t" and t.text]
            out.append(f"Slide {i}:\n" + "\n".join(texts))
    return "\n\n".join(out)


def parse_xlsx(data: bytes) -> str:
    with _open_zip(data, "XLSX") as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            for si in _xml_root(zf.read("xl/sharedStrings.xml")):
                shared.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
        sheet_names: list[str] = []
        if "xl/workbook.xml" in zf.namelist():
            sheet_names = [
                s.get("name", "") for s in _xml_root(zf.read("xl/workbook.xml")).iter() if _local(s.tag) == "sheet"
            ]
        sheets = sorted(
            (n for n in zf.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
            key=lambda n: int(re.findall(r"\d+", n)[0]),
        )
        out = []
        for idx, name in enumerate(sheets):
            title = sheet_names[idx] if idx < len(sheet_names) else f"Sheet{idx + 1}"
            rows = []
            for row in _xml_root(zf.read(name)).iter():
                if _local(row.tag) != "row":
                    continue
                cells = []
                for c in row:
                    v = next((x for x in c if _local(x.tag) == "v"), None)
                    inline = "".join(t.text or "" for t in c.iter() if _local(t.tag) == "t")
                    if c.get("t") == "s" and v is not None and v.text:
                        cells.append(shared[int(v.text)] if int(v.text) < len(shared) else "")
                    elif inline:
                        cells.append(inline)
                    elif v is not None and v.text:
                        cells.append(v.text)
                if any(cells):
                    rows.append(" | ".join(cells))
            out.append(f"Sheet: {title}\n" + "\n".join(rows))
    return "\n\n".join(out)


def parse_eml(data: bytes) -> str:
    msg = BytesParser(policy=policy.default).parsebytes(data)
    head = [f"{h}: {msg[h]}" for h in ("From", "To", "Cc", "Date", "Subject") if msg[h]]
    body = msg.get_body(preferencelist=("plain", "html"))
    content = ""
    if body is not None:
        content = body.get_content()
        if body.get_content_type() == "text/html":
            content = parse_html(content.encode("utf-8"))
    return "\n".join(head) + "\n\n" + content


def parse_rtf(data: bytes) -> str:
    text = _decode(data)
    text = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1252", "replace"), text)
    text = re.sub(r"\\(par|line|tab)\b ?", "\n", text)
    text = re.sub(r"\\[a-zA-Z]+-?\d* ?|[{}]|\\\S", "", text)
    return text


def parse_pdf(data: bytes, ocr: OcrProvider | None = None) -> str:
    text = ""
    try:
        from pypdf import PdfReader  # type: ignore[import-not-found]

        text = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)
    except ImportError:
        if ocr is None:
            raise UnsupportedDocumentError("PDF parsing requires the 'pypdf' package") from None
    except Exception as exc:
        if ocr is None:
            raise UnsupportedDocumentError("Invalid PDF file") from exc
    if len(text.strip()) < 20 and ocr is not None:  # scanned / image-only PDF
        text = ocr.extract_text(data, "application/pdf")
    return text


IMAGE_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".heic": "image/heic",
}

_SIMPLE: dict[str, Callable[[bytes], str]] = {
    **dict.fromkeys(
        (".txt", ".md", ".markdown", ".log", ".rst", ".ini", ".cfg", ".yaml", ".yml", ".toml", ".tex"),
        parse_text,
    ),
    ".csv": parse_csv,
    ".tsv": lambda d: parse_csv(d, "\t"),
    ".json": parse_json,
    ".jsonl": parse_jsonl,
    ".ndjson": parse_jsonl,
    ".xml": parse_xml,
    ".html": parse_html,
    ".htm": parse_html,
    ".docx": parse_docx,
    ".pptx": parse_pptx,
    ".xlsx": parse_xlsx,
    ".eml": parse_eml,
    ".rtf": parse_rtf,
}


def supported_extensions() -> list[str]:
    return sorted({*_SIMPLE, ".pdf", *IMAGE_MIME})


def parse_document(data: bytes, filename: str, ocr: OcrProvider | None = None) -> str:
    """Return plain text extracted from ``data`` based on the file extension."""
    name = filename.lower()
    ext = "." + name.rsplit(".", 1)[-1] if "." in name else ""
    if ext in _SIMPLE:
        text = _SIMPLE[ext](data)
    elif ext == ".pdf":
        text = parse_pdf(data, ocr)
    elif ext in IMAGE_MIME:
        if ocr is None:
            raise UnsupportedDocumentError(
                "Image OCR is not configured (set OCR_PROVIDER=vertex)"
            )
        text = ocr.extract_text(data, IMAGE_MIME[ext])
    elif not ext:
        text = _decode(data)
    else:
        raise UnsupportedDocumentError(f"Unsupported extension {ext!r}")
    text = normalise_whitespace(text)
    if not text:
        raise UnsupportedDocumentError("Document contains no extractable text")
    return text
