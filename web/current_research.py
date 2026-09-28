"""Small, source-aware web retrieval layer for time-sensitive JARVIS answers.

This is retrieval, not model retraining. It fetches a bounded set of public search
results at request time and passes only those results to the selected LLM.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from html import unescape
from html.parser import HTMLParser
import os
import re
from typing import Any, Callable

from core.memory_manager import SecretDetector
from urllib.parse import parse_qs, quote_plus, unquote, urlparse

try:
    import requests
except Exception:  # pragma: no cover - startup remains safe if optional dependency is absent
    requests = None


@dataclass
class WebSource:
    title: str
    url: str
    snippet: str
    domain: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


class _DuckDuckGoLiteParser(HTMLParser):
    """Extract normal organic result links from DuckDuckGo Lite HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[WebSource] = []
        self._current: dict[str, str] | None = None
        self._in_title = False
        self._in_snippet = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = dict(attrs)
        classes = set((attr.get("class") or "").split())
        if tag == "a" and "result-link" in classes:
            self._finish_current()
            self._current = {"title": "", "url": _unwrap_search_url(attr.get("href") or ""), "snippet": ""}
            self._in_title = True
        elif tag == "td" and "result-snippet" in classes and self._current is not None:
            self._in_snippet = True

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._in_title:
            self._in_title = False
        elif tag == "td" and self._in_snippet:
            self._in_snippet = False
            self._finish_current()

    def handle_data(self, data: str) -> None:
        if self._current is None:
            return
        if self._in_title:
            self._current["title"] += data
        elif self._in_snippet:
            self._current["snippet"] += data

    def close(self) -> None:
        super().close()
        self._finish_current()

    def _finish_current(self) -> None:
        if not self._current:
            return
        title = _clean_text(self._current.get("title", ""))
        url = self._current.get("url", "").strip()
        snippet = _clean_text(self._current.get("snippet", ""))
        if title and _safe_url(url):
            self.results.append(WebSource(title=title[:240], url=url[:1000], snippet=snippet[:700], domain=_domain(url)))
        self._current = None
        self._in_title = False
        self._in_snippet = False


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(value or "")).strip()


def _unwrap_search_url(value: str) -> str:
    """Turn a DuckDuckGo click wrapper into the actual source URL."""
    url = unescape(value).strip()
    if url.startswith("//"):
        url = "https:" + url
    try:
        parsed = urlparse(url)
        if parsed.netloc.endswith("duckduckgo.com") and parsed.path == "/l/":
            target = parse_qs(parsed.query).get("uddg", [""])[0]
            if target:
                return unquote(target)
    except Exception:
        pass
    return url


def _domain(url: str) -> str:
    match = re.match(r"https?://(?:www\.)?([^/]+)", url, flags=re.IGNORECASE)
    return match.group(1).lower() if match else ""


def _evidence_tokens(value: str) -> list[str]:
    stop = {
        "the", "and", "for", "are", "this", "that", "with", "from", "what", "when", "where", "which", "who", "how", "why", "is", "am", "be", "to", "of", "in", "on", "at", "a", "an", "my", "your", "current", "currently", "today", "latest", "recent", "please", "about", "tell", "me",
    }
    aliases = {
        "sadar": "president", "rashtrapati": "president", "राष्ट्रपति": "president", "صدر": "president",
        "wazir": "minister", "azam": "prime", "wazir-e-azam": "prime", "وزیراعظم": "prime", "وزیر": "minister",
        "प्रधानमंत्री": "prime", "मंत्री": "minister", "kon": "who", "kaun": "who", "کون": "who",
        "mausam": "weather", "موسم": "weather", "मौसम": "weather",
        "khabar": "news", "خبر": "news", "समाचार": "news",
    }
    tokens = re.findall(r"[\w\u0600-\u06FF\u0900-\u097F]{2,}", (value or "").casefold())
    return [aliases.get(token, token) for token in tokens if token not in stop]


def _safe_url(url: str) -> bool:
    if not url.startswith(("http://", "https://")) or re.search(r"[\r\n\x00]", url):
        return False
    domain = _domain(url)
    return bool(domain) and "duckduckgo.com" not in domain and "bing.com" not in domain


def _preferred_domains(query: str) -> set[str]:
    """Return official domains for roles where an authoritative source exists."""
    value = query.lower()
    if re.search(r"\b(president|sadar|rashtrapati)\b", value) and re.search(r"\b(united states|u\.s\.?|america|amerika|us)\b", value):
        return {"whitehouse.gov", "usa.gov", "govinfo.gov"}
    if re.search(r"\bmayor\b", value) and re.search(r"\bnew york( city)?\b", value):
        return {"nyc.gov"}
    if re.search(r"\b(prime minister|pm|wazir[- ]e[- ]azam|wazir[- ]azam|وزیراعظم|प्रधानमंत्री)\b", value) and re.search(r"\bpakistan\b|پاکستان|पाकिस्तान", value):
        return {"pmo.gov.pk", "pakistan.gov.pk", "pid.gov.pk", "mofa.gov.pk"}
    return set()


def _direct_official_urls(query: str) -> list[tuple[str, str]]:
    """Return stable official pages for common public-office lookups."""
    value = query.lower()
    if re.search(r"\b(president|sadar|rashtrapati)\b", value) and re.search(r"\b(united states|u\.s\.?|america|amerika|us)\b", value):
        return [
            ("https://www.whitehouse.gov/administration/donald-j-trump/", "The White House president page"),
            ("https://www.usa.gov/presidents", "USAGov presidents page"),
        ]
    if re.search(r"\bmayor\b", value) and re.search(r"\bnew york( city)?\b", value):
        return [("https://www.nyc.gov/mayors-office", "NYC Mayor's Office")]
    if re.search(r"\b(prime minister|pm|wazir[- ]e[- ]azam|wazir[- ]azam|وزیراعظم|प्रधानमंत्री)\b", value) and re.search(r"\bpakistan\b|پاکستان|पाकिस्तान", value):
        return [
            ("https://www.pmo.gov.pk/pm_profile.php", "Prime Minister's Office of Pakistan"),
            ("https://www.pakistan.gov.pk/", "Government of Pakistan"),
        ]
    return []


def _page_text(html: str) -> str:
    clean = re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<noscript[\s\S]*?</noscript>", " ", html or "", flags=re.IGNORECASE)
    clean = re.sub(r"<[^>]+>", " ", clean)
    return _clean_text(clean)


def _official_snippet(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"(?:president|mayor|prime minister)[^.]{0,500}", text, flags=re.IGNORECASE)
    if match:
        return text[max(0, match.start() - 80): match.end() + 120][:700]
    return text[:700]


def _current_role_hint(query: str, sources: list[WebSource]) -> str:
    """Extract only an explicit name from an official result, never from model memory."""
    preferred = _preferred_domains(query)
    if not preferred:
        return ""
    text = " ".join(f"{source.title}. {source.snippet}" for source in sources if source.domain in preferred)
    if not text:
        return ""
    text = re.sub(r"\bSkip to content.*", "", text, flags=re.IGNORECASE)
    name = ""
    role = ""
    value = query.lower()
    if re.search(r"\bpresident\b", value) and re.search(r"\b(united states|u\.s\.?|america|us)\b", value):
        match = re.search(r"\bPresident\s+([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})", text)
        role = "President of the United States"
        name = match.group(1).strip() if match else ""
    elif re.search(r"\bmayor\b", value) and re.search(r"\bnew york( city)?\b", value):
        match = re.search(r"\bMayor\s+([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,2})", text)
        role = "Mayor of New York City"
        name = match.group(1).strip() if match else ""
    elif re.search(r"\b(prime minister|pm|wazir[- ]e[- ]azam|wazir[- ]azam|وزیراعظم|प्रधानमंत्री)\b", value) and re.search(r"\bpakistan\b|پاکستان|पाकिस्तान", value):
        patterns = [
            r"\bH\.?E\.?\s+(?:Mr\.?\s+)?([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})(?=,|\.)",
            r"\bPrime Minister of Pakistan\s+(?:Mian\s+)?([A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,3})(?=\s+Prime Minister|,|\.)",
        ]
        role = "Prime Minister of Pakistan"
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                name = match.group(1).strip()
                break
    if not name:
        return ""
    return f"Official current-role extraction: {role} = {name}. This value was extracted from the official source text supplied above."


class WebResearcher:
    """Fetch a few public search results with bounded time and prompt size."""

    endpoint = "https://lite.duckduckgo.com/lite/?q="

    def __init__(self, timeout: float | None = None, http_get: Callable[..., Any] | None = None):
        self.timeout = max(2.0, float(timeout or os.getenv("JARVIS_WEB_TIMEOUT", "8")))
        self.http_get = http_get or (requests.get if requests is not None else None)
        self.secret_detector = SecretDetector()
        self.last_error = ""

    @property
    def available(self) -> bool:
        return self.http_get is not None

    def _direct_sources(self, query: str, top_k: int) -> list[WebSource]:
        results: list[WebSource] = []
        for url, title in _direct_official_urls(query)[:2]:
            try:
                response = self.http_get(
                    url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                    timeout=self.timeout,
                )
                response.raise_for_status()
                snippet = _official_snippet(_page_text(response.text[:2_000_000]))
                if snippet:
                    results.append(WebSource(title=title, url=url, snippet=snippet, domain=_domain(url)))
                    if len(results) >= top_k:
                        break
            except Exception as exc:
                self.last_error = f"official source {type(exc).__name__}: {str(exc)[:140]}"
        return results

    def search(self, query: str, top_k: int = 5) -> list[WebSource]:
        # Redact before any public search request; callers may use this class
        # directly outside the browser server.
        query = self.secret_detector.redact(_clean_text(query))
        if not query or not self.available:
            self.last_error = "HTTP search dependency unavailable"
            return []
        top_k = max(1, min(int(top_k), 8))
        direct = self._direct_sources(query, top_k)
        if direct:
            self.last_error = ""
            return direct
        url = self.endpoint + quote_plus(query)
        try:
            response = self.http_get(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            parser = _DuckDuckGoLiteParser()
            parser.feed(response.text[:2_000_000])
            parser.close()
            candidates = [item for item in parser.results if _safe_url(item.url)]
            preferred = _preferred_domains(query)
            if preferred:
                authoritative = [item for item in candidates if item.domain in preferred]
                # If an official result exists, do not dilute it with generic
                # pages that may contain stale or contradictory names.
                if authoritative:
                    candidates = authoritative
            unique: list[WebSource] = []
            seen: set[str] = set()
            for item in candidates:
                if item.url in seen:
                    continue
                seen.add(item.url)
                unique.append(item)
                if len(unique) >= top_k:
                    break
            if not unique:
                self.last_error = "Search returned no usable source results."
            else:
                self.last_error = ""
            return unique
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {str(exc)[:180]}"
            return []

    def has_relevant_evidence(self, query: str, sources: list[dict[str, str]] | list[WebSource]) -> bool:
        """Require overlap with the question's meaningful subject terms."""
        if not sources:
            return False
        query_terms = set(_evidence_tokens(query))
        if not query_terms:
            return False
        for item in sources:
            if isinstance(item, WebSource):
                text = f"{item.title} {item.snippet} {item.domain}"
            else:
                text = " ".join(str(item.get(key, "")) for key in ("title", "snippet", "domain"))
            source_terms = set(_evidence_tokens(text))
            if query_terms & source_terms:
                return True
        return False

    def context(self, query: str, top_k: int = 5, max_chars: int = 5000) -> tuple[str, list[dict[str, str]]]:
        sources = self.search(query, top_k=top_k)
        if not sources:
            return "", []
        sections: list[str] = []
        selected: list[dict[str, str]] = []
        remaining = max(800, int(max_chars))
        role_hint = _current_role_hint(query, sources)
        if role_hint:
            sections.append(role_hint)
            remaining -= len(role_hint) + 2
        for index, source in enumerate(sources, 1):
            block = f"[{index}] {source.title}\nURL: {source.url}\nSource: {source.domain}\n{source.snippet}".strip()
            if len(block) > remaining and sections:
                break
            sections.append(block[:remaining])
            selected.append(source.to_dict())
            remaining -= len(sections[-1]) + 2
            if remaining <= 0:
                break
        return "\n\n".join(sections), selected

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "backend": "duckduckgo_lite" if self.available else "disabled",
            "diagnostic": "Live web retrieval ready." if self.available else self.last_error,
        }
