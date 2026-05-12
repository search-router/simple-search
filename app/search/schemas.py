"""Pydantic v2 schemas — request, result, response, capabilities, health."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
)

# --- enums ------------------------------------------------------------------

class SafeSearch(StrEnum):
    OFF = "off"
    MODERATE = "moderate"
    STRICT = "strict"


class TimeRange(StrEnum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"
    YEAR = "year"
    ALL = "all"


class ImageSize(StrEnum):
    ANY = "any"
    LARGE = "large"
    MEDIUM = "medium"
    SMALL = "small"


class ImageOrientation(StrEnum):
    ANY = "any"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    SQUARE = "square"


class ImageColor(StrEnum):
    ANY = "any"
    COLOR = "color"
    GRAY = "gray"
    RED = "red"
    GREEN = "green"
    BLUE = "blue"
    YELLOW = "yellow"
    ORANGE = "orange"
    BLACK = "black"
    WHITE = "white"


Direction = Literal["ltr", "rtl", "auto"]
QueryStr = Annotated[str, StringConstraints(min_length=1, max_length=400, strip_whitespace=True)]
# Caps on free-form strings the request carries. Numbers are deliberately
# generous for valid input but tight enough to defang amplification attacks.
LocaleStr = Annotated[
    str, StringConstraints(min_length=2, max_length=20, strip_whitespace=True)
]
RegionStr = Annotated[
    str, StringConstraints(min_length=2, max_length=10, strip_whitespace=True)
]
SiteStr = Annotated[
    str, StringConstraints(min_length=1, max_length=255, strip_whitespace=True)
]
BackendStr = Annotated[
    str, StringConstraints(min_length=1, max_length=64, strip_whitespace=True)
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        str_strip_whitespace=True,
        use_enum_values=True,
    )


class _ResponseModel(BaseModel):
    model_config = ConfigDict(populate_by_name=True, use_enum_values=True)


# --- requests ---------------------------------------------------------------

class ImageFilters(_StrictModel):
    size: ImageSize | None = None
    orientation: ImageOrientation | None = None
    color: ImageColor | None = None
    site: SiteStr | None = None


class _BaseSearchRequest(_StrictModel):
    q: QueryStr
    backend: BackendStr = "auto"
    language: LocaleStr | None = None
    region: RegionStr | None = None
    ui_locale: LocaleStr | None = None
    direction: Direction = "auto"
    # Cap ``page`` so an attacker can't push the upstream into pathological
    # offsets; 1000 covers anything a real UI would ever need.
    page: int = Field(default=0, ge=0, le=1000)
    limit: int = Field(default=10, ge=1, le=100)
    safe_search: SafeSearch = SafeSearch.MODERATE
    time_range: TimeRange = TimeRange.ALL
    site: SiteStr | None = None
    cache: bool = True


class WebSearchRequest(_BaseSearchRequest):
    pass


class ImageSearchRequest(_BaseSearchRequest):
    image_filters: ImageFilters = Field(default_factory=ImageFilters)


# --- result models ----------------------------------------------------------

class _ResultBase(_ResponseModel):
    rank: int
    title: str | None = None
    domain: str | None = None
    snippet: str | None = None
    language: str | None = None
    direction: Direction = "auto"
    provider: str
    raw: dict[str, Any] = Field(default_factory=dict, repr=False, exclude=True)


class WebResult(_ResultBase):
    url: str
    published_at: datetime | None = None


class ImageResult(_ResultBase):
    page_url: str
    image_url: str
    thumbnail_url: str | None = None
    width: int | None = None
    height: int | None = None


# --- responses --------------------------------------------------------------

class _BaseResponse(_ResponseModel):
    request_id: str
    query: str
    backend: str
    language: str | None = None
    direction: Direction = "auto"
    page: int = 0
    limit: int = 10
    total_results: int = 0
    response_time_ms: int = 0
    cache_hit: bool = False


class WebSearchResponse(_BaseResponse):
    type: Literal["web"] = "web"
    results: list[WebResult] = Field(default_factory=list)


class ImageSearchResponse(_BaseResponse):
    type: Literal["images"] = "images"
    results: list[ImageResult] = Field(default_factory=list)


# --- backend introspection --------------------------------------------------

class BackendCapabilities(_ResponseModel):
    web_search: bool = False
    image_search_by_text: bool = False
    safe_search: bool = False
    pagination: bool = False
    regions: bool = False
    languages: bool = False
    max_results: int = 10
    response_formats: list[str] = Field(default_factory=lambda: ["json"])


class BackendHealth(_ResponseModel):
    status: Literal["ok", "degraded", "down"]
    latency_ms: int | None = None
    last_checked: datetime | None = None
    last_error: str | None = None


class BackendDescriptor(_ResponseModel):
    name: str
    enabled: bool
    healthy: bool
    is_mock: bool = False
    circuit_state: Literal["closed", "open", "half_open"] = "closed"
    capabilities: BackendCapabilities


class BackendListResponse(_ResponseModel):
    backends: list[BackendDescriptor]


class HealthStatus(_ResponseModel):
    status: Literal["ok", "degraded", "down"]
    version: str
    backends: dict[str, str]
    redis: str
