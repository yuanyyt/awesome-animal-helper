"""MediaWiki API client with rate limiting and optional proxy rotation."""

import random
import re
import time
from collections.abc import Iterable
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Final
from urllib.parse import quote

import httpx

from .models import WikipediaPage

DEFAULT_USER_AGENTS: Final[tuple[str, ...]] = (
    "awesome-animal-helper/0.1 (educational animal data crawler; zh)",
    "awesome-animal-helper/0.1 (educational animal data crawler; en)",
    "awesome-animal-helper/0.1 (educational animal data crawler; wikidata)",
)

# The source workbook contains a few OCR variants and zoo-facing common names.
# Keep aliases explicit so that a broad Wikipedia search cannot silently select
# an unrelated language, character-list, or literary page.
TITLE_ALIASES: Final[dict[str, dict[str, tuple[str, ...]]]] = {
    "褐美狐猴": {"zh": ("褐狐猴",), "en": ("Common brown lemur",)},
    "黄颊长臂猿": {"en": ("Yellow-cheeked gibbon",)},
    "普通绒": {"zh": ("普通狨",), "en": ("Common marmoset",)},
    "棉顶楈": {"zh": ("棉顶狨", "绒顶柽柳猴"), "en": ("Cotton-top tamarin",)},
    "赤掌縃": {"zh": ("赤掌狨", "赤掌柽柳猴"), "en": ("Red-handed tamarin",)},
    "红背须僧面猴": {"en": ("Red-backed bearded saki",)},
}


class WikimediaError(RuntimeError):
    """Raised for expected Wikimedia lookup or API failures."""


# Backward-compatible name used by callers and tests.
WikipediaError = WikimediaError


class RateLimitError(WikimediaError):
    """Raised after a Wikimedia endpoint responds with a rate limit."""

    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class WikipediaClient:
    def __init__(
        self,
        timeout: float = 20.0,
        delay: float = 1.0,
        jitter: float = 0.5,
        retries: int = 2,
        proxies: Iterable[str] = (),
        user_agents: Iterable[str] = DEFAULT_USER_AGENTS,
    ):
        self.timeout = timeout
        self.delay = max(0.0, delay)
        self.jitter = max(0.0, jitter)
        self.retries = max(0, retries)
        self._last_request = 0.0
        self.proxies = tuple(dict.fromkeys(proxy.strip() for proxy in proxies if proxy.strip()))
        agents = tuple(dict.fromkeys(agent.strip() for agent in user_agents if agent.strip()))
        self.user_agents = agents or DEFAULT_USER_AGENTS
        self._proxy_cooldowns: dict[str, float] = {}
        self._proxy_clients: dict[str, httpx.Client] = {}
        self.http = self._new_http_client()

    def close(self) -> None:
        self.http.close()
        for client in self._proxy_clients.values():
            client.close()

    @staticmethod
    def load_lines(path: Path | None) -> tuple[str, ...]:
        """Load non-empty lines while allowing whole-line and trailing comments."""
        if path is None:
            return ()
        values: list[str] = []
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            values.append(line.split(" #", 1)[0].strip())
        return tuple(values)

    load_proxies = load_lines

    def fetch_animal(
        self,
        animal: str,
        languages: tuple[str, ...] = ("zh", "en"),
    ) -> WikipediaPage:
        errors: list[str] = []
        for language in languages:
            try:
                page = self._resolve_page(animal, language)
                if page is not None:
                    return page
                errors.append(f"{language}: 页面不存在")
            except (httpx.HTTPError, WikimediaError) as exc:
                errors.append(f"{language}: {exc}")
        raise WikimediaError("；".join(errors))

    def request_json(self, endpoint: str, params: dict[str, object]) -> dict:
        """Send one throttled JSON request to a Wikimedia endpoint."""
        wait = self.delay + (random.uniform(0, self.jitter) if self.delay else 0)
        wait -= time.monotonic() - self._last_request
        if wait > 0:
            time.sleep(wait)

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            proxy = self._select_proxy()
            try:
                user_agent = random.choice(self.user_agents)
                response = self._client_for_proxy(proxy).get(
                    endpoint,
                    params={**params, "format": "json", "maxlag": "5"},
                    headers={"User-Agent": user_agent, "Api-User-Agent": user_agent},
                )
                self._last_request = time.monotonic()
                if response.status_code == 429:
                    retry_after = _retry_after_seconds(response)
                    self._cool_down(proxy, retry_after or 2**attempt)
                    raise RateLimitError("Wikimedia 返回 429 Too Many Requests", retry_after)
                response.raise_for_status()
                data = response.json()
                if "error" in data:
                    raise WikimediaError(data["error"].get("info", "Wikimedia API 错误"))
                return data
            except RateLimitError as exc:
                last_error = exc
                if attempt < self.retries:
                    time.sleep(min(exc.retry_after or 2**attempt, 60))
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                self._cool_down(proxy, 2**attempt)
                if attempt < self.retries:
                    time.sleep(min(2**attempt, 60))
        raise WikimediaError(f"请求失败：{last_error}") from last_error

    def _resolve_page(self, animal: str, language: str) -> WikipediaPage | None:
        lookup_titles = (animal, *TITLE_ALIASES.get(animal, {}).get(language, ()))
        page = None
        for title in lookup_titles:
            page = self._query_page(title, language)
            if page is not None:
                break
        if page is None:
            for lookup_title in lookup_titles:
                for title in self._search_titles(lookup_title, language):
                    if not _is_relevant_title(lookup_title, title):
                        continue
                    page = self._query_page(title, language)
                    if page is not None:
                        break
                if page is not None:
                    break
        if page is None:
            return None

        parsed = self.request_json(
            self._endpoint(language),
            {
                "action": "parse",
                "pageid": page["pageid"],
                "prop": "text|tocdata|revid",
                "redirects": "1",
                "disableeditsection": "1",
                "formatversion": "2",
            },
        ).get("parse", {})
        title = parsed.get("title", page["title"])
        return WikipediaPage(
            title=title,
            url=f"https://{language}.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
            language=language,
            page_id=page["pageid"],
            revision_id=parsed.get("revid"),
            wikidata_id=page.get("pageprops", {}).get("wikibase_item", ""),
            extract=page.get("extract", ""),
            html=parsed.get("text", ""),
        )

    def _query_page(self, title: str, language: str) -> dict | None:
        data = self.request_json(
            self._endpoint(language),
            {
                "action": "query",
                "titles": title,
                "redirects": "1",
                "converttitles": "1",
                "prop": "pageprops|extracts",
                "ppprop": "wikibase_item|disambiguation",
                "explaintext": "1",
                "exintro": "1",
                "formatversion": "2",
            },
        )
        pages = data.get("query", {}).get("pages", [])
        if not pages or pages[0].get("missing"):
            return None
        page = pages[0]
        if "disambiguation" in page.get("pageprops", {}):
            return None
        return page

    def _search_titles(self, animal: str, language: str) -> tuple[str, ...]:
        for query in (f'intitle:"{animal}"', animal):
            data = self.request_json(
                self._endpoint(language),
                {"action": "query", "list": "search", "srsearch": query, "srlimit": 5, "formatversion": "2"},
            )
            results = data.get("query", {}).get("search", [])
            if results:
                return tuple(result["title"] for result in results)
        return ()

    @staticmethod
    def _endpoint(language: str) -> str:
        return f"https://{language}.wikipedia.org/w/api.php"

    def _new_http_client(self, proxy: str | None = None) -> httpx.Client:
        return httpx.Client(
            proxy=proxy,
            timeout=self.timeout,
            headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"},
            follow_redirects=True,
        )

    def _select_proxy(self) -> str | None:
        if not self.proxies:
            return None
        now = time.monotonic()
        available = [proxy for proxy in self.proxies if self._proxy_cooldowns.get(proxy, 0) <= now]
        if available:
            return random.choice(available)
        proxy = min(self.proxies, key=lambda item: self._proxy_cooldowns.get(item, 0))
        time.sleep(max(0.0, self._proxy_cooldowns[proxy] - now))
        return proxy

    def _cool_down(self, proxy: str | None, seconds: float) -> None:
        if proxy:
            self._proxy_cooldowns[proxy] = time.monotonic() + max(1.0, seconds)

    def _client_for_proxy(self, proxy: str | None) -> httpx.Client:
        if proxy is None:
            return self.http
        if proxy not in self._proxy_clients:
            self._proxy_clients[proxy] = self._new_http_client(proxy)
        return self._proxy_clients[proxy]


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def _is_relevant_title(query: str, candidate: str) -> bool:
    """Reject low-similarity search hits before downloading their articles."""
    normalized_query = re.sub(r"[^\w]", "", query).casefold()
    normalized_candidate = re.sub(r"[^\w]", "", candidate).casefold()
    if not normalized_query or not normalized_candidate:
        return False
    if normalized_candidate in normalized_query and normalized_candidate != normalized_query:
        return False
    if normalized_candidate.endswith(("科", "属", "屬")) and not normalized_query.endswith(
        ("科", "属", "屬")
    ):
        return False
    ratio = SequenceMatcher(None, normalized_query, normalized_candidate).ratio()
    cjk_query = set(re.findall(r"[\u3400-\u9fff]", normalized_query))
    if cjk_query:
        shared = cjk_query & set(re.findall(r"[\u3400-\u9fff]", normalized_candidate))
        return len(shared) >= min(2, len(cjk_query)) and ratio >= 0.5
    return ratio >= 0.65
