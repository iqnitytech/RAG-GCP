from __future__ import annotations
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from core.exceptions import DomainValidationError

CUSTOMER_ID_RE = re.compile(r"^[A-Za-z0-9\-]{1,36}$")
MACHINE_ID_RE = re.compile(r"^MCH-[A-Z0-9]{4}-[A-Z0-9]{4}$")
SITE_ID_RE = re.compile(r"^SITE-[A-Z0-9]{4}$")

class CustomerId(str):
    def __new__(cls, value: str) -> "CustomerId":
        if not CUSTOMER_ID_RE.match(value):
            raise ValueError(f"Invalid CustomerId format: {value!r}")
        return super().__new__(cls, value)

class MachineId(str):
    def __new__(cls, value: str) -> "MachineId":
        if not MACHINE_ID_RE.match(value):
            raise ValueError(f"Invalid MachineId format: {value!r}. Expected MCH-XXXX-XXXX")
        return super().__new__(cls, value)

class SiteId(str):
    def __new__(cls, value: str) -> "SiteId":
        if not SITE_ID_RE.match(value):
            raise ValueError(f"Invalid SiteId format: {value!r}. Expected SITE-XXXX")
        return super().__new__(cls, value)

class CorrelationId(str):
    def __new__(cls, value: str | None = None) -> "CorrelationId":
        return super().__new__(cls, value or str(uuid.uuid4()))

@dataclass(frozen=True)
class PaginationParams:
    """Standardized pagination parameter value object (ARCH-04).
    Enforces boundary validation and provides bidirectional translation between
    page/page_size and limit/offset paradigms.
    """
    page: int = 1
    page_size: int = 20
    offset: int = 0
    limit: int = 20

    def __post_init__(self) -> None:
        if self.page < 1:
            raise DomainValidationError("page must be >= 1")
        if not (1 <= self.page_size <= 100):
            raise DomainValidationError("page_size must be between 1 and 100")
        if self.limit < 1 or self.limit > 100:
            raise DomainValidationError("limit must be between 1 and 100")
        if self.offset < 0:
            raise DomainValidationError("offset must be >= 0")

    @classmethod
    def from_page(cls, page: int = 1, page_size: int = 20) -> "PaginationParams":
        """Construct PaginationParams from 1-based page and page_size."""
        if page < 1:
            raise DomainValidationError("page must be >= 1")
        if not (1 <= page_size <= 100):
            raise DomainValidationError("page_size must be between 1 and 100")
        offset = (page - 1) * page_size
        return cls(page=page, page_size=page_size, offset=offset, limit=page_size)

    @classmethod
    def from_offset_limit(cls, offset: int = 0, limit: int = 10) -> "PaginationParams":
        """Construct PaginationParams from zero-based offset and limit."""
        if offset < 0:
            raise DomainValidationError("offset must be >= 0")
        if not (1 <= limit <= 100):
            raise DomainValidationError("limit must be between 1 and 100")
        page = (offset // limit) + 1 if limit > 0 else 1
        return cls(page=page, page_size=limit, offset=offset, limit=limit)
