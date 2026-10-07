import json
import os
import threading
import unittest
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from gnews_io import (
    AuthenticationError,
    BadRequestError,
    GNews,
    GNewsError,
    QuotaExceededError,
    RateLimitError,
    ServerError,
)

ARTICLE = {
    "id": "a1",
    "title": "Gold edges lower",
    "description": "Gold prices slipped.",
    "content": "Oct 7 (Reuters) - Gold prices slipped... [1862 chars]",
    "url": "https://www.reuters.com/a1",
    "image": "https://www.reuters.com/a1.jpg",
    "publishedAt": "2026-10-07T04:46:07Z",
    "lang": "en",
    "source": {"id": "s1", "name": "Reuters", "url": "https://www.reuters.com", "country": "us"},
}
OK = (200, {"totalArticles": 54453, "articles": [ARTICLE]})


class FakeAPI(BaseHTTPRequestHandler):
    api_responses: list = []
    requests: list = []

    def log_message(self, *args):
        pass

    def do_GET(self):
        url = urllib.parse.urlsplit(self.path)
        FakeAPI.requests.append({
            "path": url.path,
            "query": dict(urllib.parse.parse_qsl(url.query)),
            "headers": dict(self.headers),
        })
        response = FakeAPI.api_responses.pop(0)
        status, body = response[:2]
        headers = response[2] if len(response) == 3 else {}
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else json.dumps(body).encode())


class ClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), FakeAPI)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base_url = f"http://127.0.0.1:{cls.server.server_port}/api/v4"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        FakeAPI.requests = []
        sleep = mock.patch("gnews_io.time.sleep")
        self.sleep = sleep.start()
        self.addCleanup(sleep.stop)

    def client(self, responses, **kwargs):
        FakeAPI.api_responses = list(responses)
        return GNews("test-key", base_url=self.base_url, **kwargs)

    def test_search_builds_request(self):
        self.client([OK]).search(
            '"Federal Reserve"',
            lang="en",
            max=10,
            in_=["title", "description"],
            from_=datetime(2026, 10, 1, 2, 0, tzinfo=timezone(timedelta(hours=2))),
            to=date(2026, 10, 7),
            sortby="relevance",
            truncate=True,
        )
        [req] = FakeAPI.requests
        self.assertEqual(req["path"], "/api/v4/search")
        self.assertEqual(req["query"], {
            "q": '"Federal Reserve"',
            "lang": "en",
            "max": "10",
            "in": "title,description",
            "from": "2026-10-01T00:00:00Z",
            "to": "2026-10-07T00:00:00Z",
            "sortby": "relevance",
            "truncate": "content",
        })
        self.assertEqual(req["headers"]["X-Api-Key"], "test-key")
        self.assertTrue(req["headers"]["User-Agent"].startswith("gnews-io-python/"))

    def test_top_headlines_builds_request(self):
        self.client([OK]).top_headlines("business", country="us", from_=datetime(2026, 10, 7, 12))
        [req] = FakeAPI.requests
        self.assertEqual(req["path"], "/api/v4/top-headlines")
        self.assertEqual(req["query"], {
            "category": "business",
            "country": "us",
            "from": "2026-10-07T12:00:00Z",
        })

    def test_parses_response(self):
        result = self.client([OK]).search("gold")
        self.assertEqual(result.total_articles, 54453)
        self.assertEqual(result.raw, OK[1])
        [article] = result.articles
        self.assertEqual(article.title, "Gold edges lower")
        self.assertEqual(article.published_at, datetime(2026, 10, 7, 4, 46, 7, tzinfo=timezone.utc))
        self.assertEqual(article.source.name, "Reuters")
        self.assertEqual(article.source.country, "us")

    def test_parses_nullable_fields(self):
        minimal = {"title": "T", "url": "u", "publishedAt": "2026-10-07T04:46:07Z", "source": {"name": "S"}}
        [article] = self.client([(200, {"totalArticles": 1, "articles": [minimal]})]).top_headlines().articles
        self.assertIsNone(article.image)
        self.assertIsNone(article.source.country)

    def test_normalizes_published_at_to_utc(self):
        article = dict(ARTICLE, publishedAt="2026-10-07T06:46:07+02:00")
        [parsed] = self.client([(200, {"totalArticles": 1, "articles": [article]})]).search("gold").articles
        self.assertEqual(parsed.published_at, datetime(2026, 10, 7, 4, 46, 7, tzinfo=timezone.utc))

    def test_maps_http_errors(self):
        cases = [
            (400, {"errors": {"q": "The query has a syntax error."}}, BadRequestError, "q: The query has a syntax error."),
            (400, {"errors": ["You did not provide an API key."]}, AuthenticationError, "You did not provide an API key."),
            (401, {"errors": ["Invalid API Key provided."]}, AuthenticationError, "Invalid API Key provided."),
            (403, {"errors": ["You have reached your request limit for today."]}, QuotaExceededError, "You have reached your request limit for today."),
            (418, b"teapot", GNewsError, "HTTP 418"),
        ]
        for status, body, error, message in cases:
            with self.subTest(status=status, error=error.__name__):
                FakeAPI.requests = []
                with self.assertRaises(error) as ctx:
                    self.client([(status, body)]).search("x")
                self.assertEqual(str(ctx.exception), message)
                self.assertEqual(ctx.exception.status, status)
                self.assertEqual(len(FakeAPI.requests), 1)

    def test_retries_rate_limit_then_succeeds(self):
        result = self.client([(429, {"errors": ["Too many requests."]}), OK]).search("x")
        self.assertEqual(result.total_articles, 54453)
        self.assertEqual(len(FakeAPI.requests), 2)
        [(delay,), _] = self.sleep.call_args
        self.assertTrue(1 <= delay < 2)

    def test_gives_up_after_max_retries(self):
        unavailable = (503, {"errors": ["Maintenance."]})
        with self.assertRaises(ServerError):
            self.client([unavailable] * 3).search("x")
        self.assertEqual(len(FakeAPI.requests), 3)

        FakeAPI.requests = []
        with self.assertRaises(RateLimitError):
            self.client([(429, {"errors": ["Too many requests."]})], max_retries=0).search("x")
        self.assertEqual(len(FakeAPI.requests), 1)

    def test_invalid_responses_raise_gnews_error(self):
        bodies = [
            b"<html>maintenance</html>",
            {"totalArticles": 1, "articles": [{"url": "u", "publishedAt": "2026-10-07T04:46:07Z"}]},
            {"totalArticles": 1, "articles": [{"title": "T", "url": "u", "publishedAt": "yesterday"}]},
            {"totalArticles": "1", "articles": []},
            {"totalArticles": 1, "articles": ["not an object"]},
            [],
        ]
        for body in bodies:
            with self.subTest(body=body):
                with self.assertRaises(GNewsError):
                    self.client([(200, body)]).search("x")

    def test_network_error_raises_gnews_error(self):
        with self.assertRaises(GNewsError):
            GNews("k", base_url="http://127.0.0.1:9", max_retries=0).search("x")

    def test_constructor_validation(self):
        with mock.patch.dict(os.environ, {"GNEWS_API_KEY": "  env-key\n"}):
            self.assertEqual(GNews()._api_key, "env-key")
        with mock.patch.dict(os.environ, clear=True):
            with self.assertRaises(ValueError):
                GNews()
        for kwargs in [{"max_retries": -1}, {"timeout": 0}, {"base_url": "gnews.io/api/v4"}]:
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                GNews("k", **kwargs)
        with self.assertRaises(ValueError):
            GNews("k").search("")

    def test_iter_search_paginates_lazily_and_honors_limit(self):
        first = (200, {"totalArticles": 5, "articles": [ARTICLE, dict(ARTICLE, id="a2")]})
        second = (200, {"totalArticles": 5, "articles": [dict(ARTICLE, id="a3"), dict(ARTICLE, id="a4")]})
        articles = list(self.client([first, second]).iter_search("gold", max=2, limit=3))

        self.assertEqual([article.id for article in articles], ["a1", "a2", "a3"])
        self.assertEqual([request["query"]["page"] for request in FakeAPI.requests], ["1", "2"])

    def test_iter_top_headlines_stops_on_last_page(self):
        first = (200, {"totalArticles": 3, "articles": [ARTICLE, dict(ARTICLE, id="a2")]})
        last = (200, {"totalArticles": 3, "articles": [dict(ARTICLE, id="a3")]})
        articles = list(self.client([first, last]).iter_top_headlines("business", max=2))

        self.assertEqual([article.id for article in articles], ["a1", "a2", "a3"])
        self.assertEqual([request["query"]["page"] for request in FakeAPI.requests], ["1", "2"])
        self.assertEqual(FakeAPI.requests[0]["query"]["category"], "business")


if __name__ == "__main__":
    unittest.main()
