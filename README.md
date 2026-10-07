# GNews API Python Client

Official Python client for the [GNews API](https://gnews.io): search news articles and top headlines from 80,000+ sources in 41 languages.

- No dependencies, Python 3.10+
- Typed responses (`Article`, `Source`) with editor autocompletion
- One exception per API error (invalid key, quota reached, rate limit...)
- Automatic retry on rate limit (429), server (5xx) and network errors
- Lazy pagination helpers that stop at a requested limit

## Installation

```bash
pip install gnews-io-python
```

Note: `gnews` on PyPI is an unrelated Google News scraper and `gnewsio` is an unofficial client. The official client is `gnews-io-python`, imported as `gnews_io`.

## Quick start

Get a free API key at [gnews.io/register](https://gnews.io/register).

```python
from gnews_io import GNews

client = GNews("YOUR_API_KEY")  # or set the GNEWS_API_KEY environment variable and call GNews()

result = client.search("bitcoin", lang="en", max=10)

print(result.total_articles)
for article in result.articles:
    print(article.published_at, article.source.name, article.title)
```

## Search

```python
from datetime import datetime, timezone

result = client.search(
    '"Federal Reserve" AND rates',
    lang="en",
    country="us",
    max=10,
    in_=["title", "description"],
    from_=datetime(2026, 10, 1, tzinfo=timezone.utc),
    sortby="relevance",
)
```

The query supports quotes, `AND`, `OR`, `NOT` and parentheses: see the [query syntax](https://docs.gnews.io/endpoints/search-endpoint#query-syntax).

## Top headlines

```python
result = client.top_headlines("business", lang="en", country="us", max=10)
```

Categories: `general` (default), `world`, `nation`, `business`, `technology`, `entertainment`, `sports`, `science`, `health`.

## Parameters

| Parameter | `search` | `top_headlines` | Notes |
|---|---|---|---|
| `q` | required | optional | Keywords, max 200 characters |
| `category` | | first argument | One of the categories above |
| `lang` | yes | yes | 2-letter language code, e.g. `"en"` |
| `country` | yes | yes | 2-letter country code, e.g. `"us"` |
| `max` | yes | yes | Articles per request, 1 to 100 depending on your plan (default 10) |
| `in_` | yes | | Fields to search: `"title"`, `"description"`, `"content"` (string or list) |
| `nullable` | yes | yes | Fields allowed to be null: `"description"`, `"content"`, `"image"` |
| `from_`, `to` | yes | yes | `datetime`, `date` or ISO 8601 string. Naive datetimes are treated as UTC |
| `sortby` | yes | | `"publishedAt"` (default) or `"relevance"` |
| `page` | yes | yes | Page number, starts at 1 (up to 1,000 articles in total) |
| `truncate` | yes | yes | `True` to truncate `content` |

`in_` and `from_` end with an underscore because `in` and `from` are Python keywords.

Full reference: [docs.gnews.io](https://docs.gnews.io).

## Response

`search` and `top_headlines` return an `ArticlesResponse`:

| Attribute | Type |
|---|---|
| `total_articles` | `int` |
| `articles` | `list[Article]` |
| `raw` | `dict`, the JSON returned by the API |

Each `Article` has `id`, `title`, `description`, `content`, `url`, `image`, `published_at` (timezone-aware `datetime`, UTC), `lang` and `source` (`id`, `name`, `url`, `country`). `source.country` is only returned by `search`.

On the Free plan, `content` is truncated. Full content is available on [paid plans](https://gnews.io/pricing).

## Pagination

`iter_search` and `iter_top_headlines` take the same parameters as `search` and `top_headlines`, fetch the next page only when you reach it, and stop at `limit`, at the last page or at the API's 1,000-article cap:

```python
for article in client.iter_search("climate", lang="en", max=10, limit=50):
    print(article.title)
```

Each page is one API request. You can also pass `page` yourself to `search` and `top_headlines`.

## Error handling

All errors inherit from `GNewsError`, which exposes `status` (HTTP code) and `errors` (the API error payload).

```python
from gnews_io import GNews, GNewsError, QuotaExceededError

try:
    result = client.search("bitcoin")
except QuotaExceededError:
    print("Daily quota reached, it resets at 00:00 UTC")
except GNewsError as e:
    print(e.status, e)
```

| Exception | HTTP | Cause |
|---|---|---|
| `BadRequestError` | 400 | Missing or invalid parameter, query syntax error |
| `AuthenticationError` | 401 (or 400) | Missing or invalid API key |
| `QuotaExceededError` | 403 | Daily quota reached or subscription expired |
| `RateLimitError` | 429 | Too many requests per second (1/s on Free, 10/s on paid plans) |
| `ServerError` | 5xx | Server error or maintenance |
| `GNewsError` | | Base class, also raised on network errors and invalid responses |

Rate limit, server and network errors are retried twice with a short randomized backoff (about 1 s, then 2 s) before raising. Change it with `GNews(max_retries=0)`.

## Options

```python
client = GNews(
    "YOUR_API_KEY",
    timeout=10.0,     # seconds per request
    max_retries=2,    # retries on 429, 5xx and network errors
)
```

## Development

```bash
pip install -e ".[dev]"
python -m unittest discover -v
mypy
```

## License

MIT
