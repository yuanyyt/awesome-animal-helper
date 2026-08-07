"""Wikipedia MediaWiki API client with proxy and User-Agent rotation."""

import random
import time
from collections.abc import Iterable
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Final

import httpx

DEFAULT_USER_AGENTS: Final[tuple[str, ...]] = (
    "awesome-animal-helper/0.1 (research bot; contact: local-project)",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 Version/18.1 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
)


class WikipediaError(RuntimeError):
    """Raised for expected Wikipedia lookup or API failures."""


class RateLimitError(WikipediaError):
    """Raised after Wikipedia responds with a rate limit."""

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
        self.delay = delay
        self.jitter = max(0.0, jitter)
        self.retries = max(0, retries)
        self._last_request = 0.0
        self.proxies = tuple(dict.fromkeys(proxy.strip() for proxy in proxies if proxy.strip()))
        self.user_agents = tuple(dict.fromkeys(agent.strip() for agent in user_agents if agent.strip())) or DEFAULT_USER_AGENTS
        self._proxy_cooldowns: dict[str, float] = {}
        self._proxy_clients: dict[str, httpx.Client] = {}
        self.http = httpx.Client(
            timeout=timeout,
            headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self.http.close()
        for client in self._proxy_clients.values():
            client.close()

    @staticmethod
    def load_proxies(path: Path | None) -> tuple[str, ...]:
        """Load one HTTP(S) proxy URL per line; blank lines and # comments are ignored."""
        if path is None:
            return ()
        return tuple(
            line.split("#", 1)[0].strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.split("#", 1)[0].strip()
        )

    def fetch_animal(self, animal: str, languages: tuple[str, ...] = ("zh", "en")) -> tuple[str, str, str]:
        errors: list[str] = []
        for language in languages:
            try:
                title = self._search(animal, language)
                if title:
                    return title, language, self._parse(title, language)
                errors.append(f"{language}: 页面不存在")
            except (httpx.HTTPError, WikipediaError) as exc:
                errors.append(f"{language}: {exc}")
        raise WikipediaError("；".join(errors))

    def _search(self, animal: str, language: str) -> str:
        data = self._request(language, {"action": "query", "list": "search", "srsearch": animal, "srlimit": 5})
        results = data.get("query", {}).get("search", [])
        exact = next((item["title"] for item in results if item["title"].casefold() == animal.casefold()), None)
        return exact or (results[0]["title"] if len(results) == 1 else "")

    def _parse(self, title: str, language: str) -> str:
        data = self._request(language, {"action": "parse", "page": title, "prop": "text", "formatversion": "2"})
        try:
            return data["parse"]["text"]
        except KeyError as exc:
            raise WikipediaError(f"无法解析页面 {title}") from exc

    def _request(self, language: str, params: dict[str, object]) -> dict:
        wait = self.delay + (random.uniform(0, self.jitter) if self.delay > 0 else 0) - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        endpoint = f"https://{language}.wikipedia.org/w/api.php"
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            proxy = self._select_proxy()
            client = self._client_for_proxy(proxy)
            try:
                response = client.get(
                    endpoint,
                    params={**params, "format": "json"},
                    headers={"User-Agent": random.choice(self.user_agents)},
                )
                self._last_request = time.monotonic()
                if response.status_code == 429:
                    retry_after = _retry_after_seconds(response)
                    self._cool_down(proxy, retry_after or 2**attempt)
                    raise RateLimitError("Wikipedia 返回 429 Too Many Requests", retry_after)
                response.raise_for_status()
                data = response.json()
                if "error" in data:
                    raise WikipediaError(data["error"].get("info", "Wikipedia API 错误"))
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
        raise WikipediaError(f"请求失败：{last_error}") from last_error

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
            self._proxy_clients[proxy] = httpx.Client(
                proxy=proxy,
                timeout=self.timeout,
                headers={"Accept-Language": "zh-CN,zh;q=0.9,en;q=0.5"},
                follow_redirects=True,
            )
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
