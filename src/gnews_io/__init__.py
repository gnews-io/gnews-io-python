"""Official Python client for the GNews API (https://gnews.io)."""

from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Iterable, Iterator, Literal, Union

__version__ = "1.0.0"

__all__ = [
    "GNews",
    "Article",
    "Source",
    "ArticlesResponse",
    "GNewsError",
    "BadRequestError",
    "AuthenticationError",
    "QuotaExceededError",
    "RateLimitError",
    "ServerError",
]

BASE_URL = "https://gnews.io/api/v4"
RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_ARTICLES = 1_000

Category = Literal[
    "general", "world", "nation", "business", "technology",
    "entertainment", "sports", "science", "health",
]
SortBy = Literal["publishedAt", "relevance"]
DateLike = Union[str, date, datetime]
Fields = Union[str, Iterable[str]]


class GNewsError(Exception):
    def __init__(self, message: str, status: int | None = None, errors: Any = None):
        super().__init__(message)
        self.status = status
        self.errors = errors


class BadRequestError(GNewsError):
    """400: missing or invalid parameter, or query syntax error."""


class AuthenticationError(GNewsError):
    """401: missing or invalid API key."""


class QuotaExceededError(GNewsError):
    """403: daily quota reached (reset at 00:00 UTC) or subscription expired."""


class RateLimitError(GNewsError):
    """429: too many requests per second."""


class ServerError(GNewsError):
    """5xx: GNews server error or maintenance."""


_ERRORS_BY_STATUS = {
    400: BadRequestError,
    401: AuthenticationError,
    403: QuotaExceededError,
    429: RateLimitError,
}


@dataclass(frozen=True)
class Source:
    id: str | None
    name: str
    url: str | None
    country: str | None = None


@dataclass(frozen=True)
class Article:
    id: str | None
    title: str
    description: str | None
    content: str | None
    url: str
    image: str | None
    published_at: datetime
    lang: str | None
    source: Source

    @classmethod
    def _from_json(cls, data: dict[str, Any]) -> Article:
        src = data.get("source") or {}
        if not isinstance(src, dict):
            raise TypeError("source must be an object")
        # fromisoformat only accepts "Z" from Python 3.11
        published_at = datetime.fromisoformat(_required_str(data, "publishedAt").replace("Z", "+00:00"))
        if published_at.tzinfo is None:
            raise ValueError("publishedAt must include a timezone")
        return cls(
            id=_optional_str(data, "id"),
            title=_required_str(data, "title"),
            description=_optional_str(data, "description"),
            content=_optional_str(data, "content"),
            url=_required_str(data, "url"),
            image=_optional_str(data, "image"),
            published_at=published_at.astimezone(timezone.utc),
            lang=_optional_str(data, "lang"),
            source=Source(
                id=_optional_str(src, "id"),
                name=_required_str(src, "name"),
                url=_optional_str(src, "url"),
                country=_optional_str(src, "country"),
            ),
        )


@dataclass(frozen=True)
class ArticlesResponse:
    total_articles: int
    articles: list[Article]
    raw: dict[str, Any] = field(repr=False)

    @classmethod
    def _from_json(cls, data: dict[str, Any]) -> ArticlesResponse:
        total_articles = data.get("totalArticles", 0)
        articles = data.get("articles", [])
        if isinstance(total_articles, bool) or not isinstance(total_articles, int):
            raise TypeError("totalArticles must be an integer")
        if not isinstance(articles, list) or not all(isinstance(a, dict) for a in articles):
            raise TypeError("articles must be a list of objects")
        return cls(
            total_articles=total_articles,
            articles=[Article._from_json(a) for a in articles],
            raw=data,
        )


class GNews:
    """GNews API client.

    The API key defaults to the ``GNEWS_API_KEY`` environment variable.
    Rate-limited (429), server (5xx) and network errors are retried ``max_retries`` times.
    """

    def __init__(
        self,
        api_key: str | None = None,
        *,
        timeout: float = 10.0,
        max_retries: int = 2,
        base_url: str = BASE_URL,
    ):
        api_key = (api_key or os.environ.get("GNEWS_API_KEY") or "").strip()
        if not api_key:
            raise ValueError(
                "No API key: pass api_key or set GNEWS_API_KEY. "
                "Get a free key at https://gnews.io/register"
            )
        if not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be an integer >= 0")
        if timeout <= 0:
            raise ValueError("timeout must be > 0")
        if not base_url.startswith(("https://", "http://")):
            raise ValueError("base_url must start with https:// or http://")
        self._api_key = api_key
        self.timeout = timeout
        self.max_retries = max_retries
        self.base_url = base_url.rstrip("/")

    def search(
        self,
        q: str,
        *,
        lang: str | None = None,
        country: str | None = None,
        max: int | None = None,
        in_: Fields | None = None,
        nullable: Fields | None = None,
        from_: DateLike | None = None,
        to: DateLike | None = None,
        sortby: SortBy | None = None,
        page: int | None = None,
        truncate: bool = False,
    ) -> ArticlesResponse:
        """Search articles by keywords. See https://docs.gnews.io/endpoints/search-endpoint"""
        if not q:
            raise ValueError("q is required")
        return self._get("search", {
            "q": q,
            "lang": lang,
            "country": country,
            "max": max,
            "in": _csv(in_),
            "nullable": _csv(nullable),
            "from": _iso(from_),
            "to": _iso(to),
            "sortby": sortby,
            "page": page,
            "truncate": "content" if truncate else None,
        })

    def top_headlines(
        self,
        category: Category | None = None,
        *,
        lang: str | None = None,
        country: str | None = None,
        max: int | None = None,
        q: str | None = None,
        nullable: Fields | None = None,
        from_: DateLike | None = None,
        to: DateLike | None = None,
        page: int | None = None,
        truncate: bool = False,
    ) -> ArticlesResponse:
        """Trending articles by category. See https://docs.gnews.io/endpoints/top-headlines-endpoint"""
        return self._get("top-headlines", {
            "category": category,
            "lang": lang,
            "country": country,
            "max": max,
            "q": q,
            "nullable": _csv(nullable),
            "from": _iso(from_),
            "to": _iso(to),
            "page": page,
            "truncate": "content" if truncate else None,
        })

    def iter_search(self, q: str, *, limit: int | None = None, **params: Any) -> Iterator[Article]:
        """Yield articles page by page, up to ``limit`` or the API's 1,000-article cap.

        Takes the same parameters as ``search``.
        """
        return _paginate(lambda **p: self.search(q, **p), limit, params)

    def iter_top_headlines(
        self, category: Category | None = None, *, limit: int | None = None, **params: Any
    ) -> Iterator[Article]:
        """Yield articles page by page, up to ``limit`` or the API's 1,000-article cap.

        Takes the same parameters as ``top_headlines``.
        """
        return _paginate(lambda **p: self.top_headlines(category, **p), limit, params)

    def _get(self, endpoint: str, params: dict[str, Any]) -> ArticlesResponse:
        query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        request = urllib.request.Request(
            f"{self.base_url}/{endpoint}?{query}",
            headers={
                "X-Api-Key": self._api_key,
                "Accept": "application/json",
                "User-Agent": f"gnews-io-python/{__version__}",
            },
        )
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = response.read()
                break
            except urllib.error.HTTPError as e:
                with e:
                    if e.code not in RETRY_STATUSES or attempt == self.max_retries:
                        raise _api_error(e) from None
            except OSError as e:
                if attempt == self.max_retries:
                    raise GNewsError(f"Network error: {e}") from e
            # jitter so concurrent clients hitting the per-second limit don't retry in sync
            time.sleep(2.0 ** attempt + random.random())
            attempt += 1
        try:
            data = json.loads(body)
            if not isinstance(data, dict):
                raise TypeError("response must be a JSON object")
            return ArticlesResponse._from_json(data)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise GNewsError(f"Invalid response from the API: {e!r}") from e


def _paginate(
    fetch: Callable[..., ArticlesResponse], limit: int | None, params: dict[str, Any]
) -> Iterator[Article]:
    page = params.pop("page", None) or 1
    size = params.setdefault("max", None) or 10
    yielded = 0
    while True:
        result = fetch(page=page, **params)
        for article in result.articles[: None if limit is None else limit - yielded]:
            yield article
            yielded += 1
        if (
            yielded == limit
            or len(result.articles) < size
            or page * size >= min(result.total_articles, MAX_ARTICLES)
        ):
            return
        page += 1


def _api_error(e: urllib.error.HTTPError) -> GNewsError:
    try:
        errors = json.load(e).get("errors")
    except (ValueError, AttributeError):
        errors = None
    if isinstance(errors, dict):
        message = "; ".join(f"{k}: {v}" for k, v in errors.items())
    elif isinstance(errors, list) and errors:
        message = "; ".join(map(str, errors))
    else:
        message = f"HTTP {e.code}"
    cls = _ERRORS_BY_STATUS.get(e.code, ServerError if e.code >= 500 else GNewsError)
    # The API answers 400, not 401, when the key is missing or malformed
    if e.code == 400 and "api key" in message.lower():
        cls = AuthenticationError
    return cls(message, status=e.code, errors=errors)


def _csv(value: Fields | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    return ",".join(value)


def _iso(value: DateLike | None) -> str | None:
    if value is None or isinstance(value, str):
        return value
    if not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _required_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _optional_str(data: dict[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise TypeError(f"{key} must be a string or null")
    return value
