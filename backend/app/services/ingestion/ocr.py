"""OCR providers for images and scanned PDFs."""

from __future__ import annotations

import base64
import logging
from typing import Protocol

import httpx
from sdk.errors import ServiceUnavailableError

logger = logging.getLogger("app.ingestion.ocr")

OCR_PROMPT = (
    "Extract all text from this document exactly as written, preserving reading order, "
    "tables (as rows separated by ' | ') and headings. If it is a photo or diagram with "
    "little text, briefly describe its content. Return only the extracted text."
)


class OcrError(ServiceUnavailableError):
    public_message = "Document text extraction service is temporarily unavailable"


class OcrProvider(Protocol):
    def extract_text(self, data: bytes, mime_type: str) -> str: ...


class VertexGeminiOcr:
    """Image / scanned-PDF text extraction using Gemini multimodal (Vertex AI REST)."""

    MAX_BYTES = 18 * 1024 * 1024  # stay under the inline-data request limit

    def __init__(
        self,
        *,
        project_id: str,
        region: str,
        model: str = "gemini-2.0-flash",
        credentials_getter=None,  # noqa: ANN001
        timeout_seconds: float = 90.0,
        http_client: httpx.Client | None = None,
    ) -> None:
        self._url = (
            f"https://{region}-aiplatform.googleapis.com/v1/projects/{project_id}"
            f"/locations/{region}/publishers/google/models/{model}:generateContent"
        )
        self._credentials_getter = credentials_getter
        self._timeout = timeout_seconds
        self._client = http_client

    def _token(self) -> str:
        if self._credentials_getter is None:
            raise OcrError("No GCP credentials configured for Gemini OCR")
        from google.auth.transport.requests import Request  # lazy import

        creds = self._credentials_getter()
        if not creds.valid:
            creds.refresh(Request())
        return creds.token

    def extract_text(self, data: bytes, mime_type: str) -> str:
        if len(data) > self.MAX_BYTES:
            raise OcrError("Document too large for inline OCR")
        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"inline_data": {"mime_type": mime_type, "data": base64.b64encode(data).decode()}},
                        {"text": OCR_PROMPT},
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.0},
        }
        headers = {"Authorization": f"Bearer {self._token()}"}
        try:
            client = self._client or httpx.Client(timeout=self._timeout)
            try:
                resp = client.post(self._url, json=body, headers=headers)
            finally:
                if self._client is None:
                    client.close()
        except httpx.HTTPError as exc:
            logger.error("Gemini OCR request failed: %s", exc)
            raise OcrError(f"Gemini OCR request failed: {exc}") from exc
        if resp.status_code != 200:
            logger.error("Gemini OCR HTTP %s: %s", resp.status_code, resp.text[:300])
            raise OcrError(f"Gemini OCR failed with HTTP {resp.status_code}")
        parts = (
            (resp.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
        )
        return "\n".join(p.get("text", "") for p in parts).strip()
