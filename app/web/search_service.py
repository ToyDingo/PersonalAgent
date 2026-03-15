from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from html import unescape
from typing import Any, Dict, List, Tuple
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from openai import OpenAI

SEARCH_TIMEOUT = httpx.Timeout(6.0, connect=3.0)
PAGE_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
WEB_SEARCH_BUDGET_SECONDS = 12.0
DOCUMENT_FETCH_BUDGET_SECONDS = 6.0
DOCUMENT_FETCH_CANDIDATE_MULTIPLIER = 3
MAX_DOCUMENTS_FOR_EXTRACTION = 2
MAX_SEARCH_RESULTS_IN_PROMPT = 20
MAX_DOCUMENT_CONTENT_CHARS = 10000
EXTRACTION_TIMEOUT_SECONDS = 25.0
DISCOVERY_CACHE_TTL_SECONDS = 300.0
DISCOVERY_MAX_RESULTS_CAP = 60
DISCOVERY_PER_PROVIDER_CAP = 15
DISCOVERY_MIN_USEFUL_RESULTS = 12
DISCOVERY_MAX_VARIANTS_WITH_NO_HITS = 2
PROVIDER_ORDER = ["ddg", "google", "bing"]
_DISCOVERY_CACHE: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}


def _clean_html_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value)
    text = unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_trusted_schedule_source(url: str) -> bool:
    trusted_hosts = (
        "atlutd.com",
        "mlssoccer.com",
        "espn.com",
        "transfermarkt.com",
    )
    host = urlparse(url).netloc.lower()
    return any(host.endswith(domain) for domain in trusted_hosts)


def _extract_html_text(html: str) -> str:
    html = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", html)
    html = re.sub(r"(?is)<noscript[^>]*>.*?</noscript>", " ", html)
    return _clean_html_text(html)


def _fetch_page_text(url: str) -> str:
    with httpx.Client(timeout=PAGE_TIMEOUT, follow_redirects=True) as client:
        response = client.get(
            url,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/122.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        response.raise_for_status()
        return _extract_html_text(response.text)


def _prepare_documents(
    search_results: List[Dict[str, str]],
    max_documents: int,
    budget_seconds: float = DOCUMENT_FETCH_BUDGET_SECONDS,
) -> List[Dict[str, str]]:
    docs: List[Dict[str, str]] = []
    prioritized = sorted(
        search_results,
        key=lambda item: (not _is_trusted_schedule_source(item.get("url", ""))),
    )
    candidate_limit = max_documents * DOCUMENT_FETCH_CANDIDATE_MULTIPLIER
    candidates = prioritized[:candidate_limit]
    fetched_content: Dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=max(2, min(8, len(candidates) or 2))) as pool:
        futures = {}
        for item in candidates:
            url = item.get("url", "")
            if not url:
                continue
            futures[pool.submit(_fetch_page_text, url)] = url

        try:
            completed = as_completed(futures, timeout=max(0.1, budget_seconds))
            for future in completed:
                url = futures[future]
                try:
                    page_text = future.result()
                except Exception:
                    page_text = ""
                if page_text:
                    fetched_content[url] = page_text
        except Exception:
            # Budget exhausted; keep whatever completed.
            pass

    for item in candidates:
        if len(docs) >= max_documents:
            break
        url = item.get("url", "")
        if not url:
            continue
        page_text = fetched_content.get(url, "")
        if not page_text:
            continue
        docs.append(
            {
                "title": item.get("title", ""),
                "url": url,
                "snippet": item.get("snippet", ""),
                "content": page_text[:MAX_DOCUMENT_CONTENT_CHARS],
                "trusted_source": str(_is_trusted_schedule_source(url)).lower(),
            }
        )
    return docs


def _decode_duckduckgo_url(raw_href: str) -> str:
    if raw_href.startswith("//"):
        raw_href = f"https:{raw_href}"
    if raw_href.startswith("http://") or raw_href.startswith("https://"):
        parsed_http = urlparse(raw_href)
        if parsed_http.netloc.endswith("duckduckgo.com") and parsed_http.path.startswith("/l/"):
            params_http = parse_qs(parsed_http.query)
            uddg_http = params_http.get("uddg", [None])[0]
            if uddg_http:
                return unquote(uddg_http)
        return raw_href
    if raw_href.startswith("/l/?") or raw_href.startswith("https://duckduckgo.com/l/?"):
        parsed = urlparse(raw_href)
        params = parse_qs(parsed.query)
        uddg = params.get("uddg", [None])[0]
        if uddg:
            return unquote(uddg)
    return raw_href


def _decode_google_url(raw_href: str) -> str:
    # Google frequently wraps outbound links as /url?q=<target>.
    if raw_href.startswith("/url?"):
        parsed = urlparse(raw_href)
        params = parse_qs(parsed.query)
        q = params.get("q", [None])[0]
        if q:
            return unquote(q)
    return raw_href


def _duckduckgo_search(query: str, max_results: int) -> List[Dict[str, str]]:
    url = "https://html.duckduckgo.com/html/"
    try:
        with httpx.Client(timeout=SEARCH_TIMEOUT, follow_redirects=True) as client:
            response = client.get(
                url,
                params={"q": query},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            if response.status_code != 200:
                return []
            html = response.text
    except Exception:
        return []

    result_pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippet_pattern = re.compile(
        r'<[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</[^>]+>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippets = [_clean_html_text(m.group("snippet")) for m in snippet_pattern.finditer(html)]

    results: List[Dict[str, str]] = []
    for idx, match in enumerate(result_pattern.finditer(html)):
        href = _decode_duckduckgo_url(match.group("href"))
        title = _clean_html_text(match.group("title"))
        snippet = snippets[idx] if idx < len(snippets) else ""
        if not href or not title:
            continue
        results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def _google_search(query: str, max_results: int) -> List[Dict[str, str]]:
    url = "https://www.google.com/search"
    try:
        with httpx.Client(timeout=SEARCH_TIMEOUT, follow_redirects=True) as client:
            response = client.get(
                url,
                params={"q": query, "num": min(max_results, 50), "hl": "en"},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            if response.status_code != 200:
                return []
            html = response.text
    except Exception:
        return []

    link_pattern = re.compile(
        r'<a[^>]*href="(?P<href>/url\?q=[^"]+|https?://[^"]+)"[^>]*>(?P<body>.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    title_pattern = re.compile(r"<h3[^>]*>(?P<title>.*?)</h3>", flags=re.IGNORECASE | re.DOTALL)
    snippet_pattern = re.compile(
        r'<div[^>]*class="[^"]*(?:VwiC3b|BNeawe)[^"]*"[^>]*>(?P<snippet>.*?)</div>',
        flags=re.IGNORECASE | re.DOTALL,
    )

    snippets = [_clean_html_text(m.group("snippet")) for m in snippet_pattern.finditer(html)]
    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    snippet_idx = 0
    for match in link_pattern.finditer(html):
        href = _decode_google_url(match.group("href"))
        if href.startswith("/search?") or href.startswith("#"):
            continue
        body = match.group("body")
        title_match = title_pattern.search(body)
        if not title_match:
            continue
        title = _clean_html_text(title_match.group("title"))
        if not href or not title:
            continue
        if not href.startswith("http://") and not href.startswith("https://"):
            continue
        if href in seen:
            continue
        seen.add(href)
        snippet = snippets[snippet_idx] if snippet_idx < len(snippets) else ""
        snippet_idx += 1
        results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def _bing_search(query: str, max_results: int) -> List[Dict[str, str]]:
    url = "https://www.bing.com/search"
    try:
        with httpx.Client(timeout=SEARCH_TIMEOUT, follow_redirects=True) as client:
            response = client.get(
                url,
                params={"q": query, "count": min(max_results, 50), "setlang": "en-US"},
                headers={
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/122.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
            )
            if response.status_code != 200:
                return []
            html = response.text
    except Exception:
        return []

    item_pattern = re.compile(
        r'<li[^>]*class="[^"]*\bb_algo\b[^"]*"[^>]*>(?P<item>.*?)</li>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    link_pattern = re.compile(
        r'<h2[^>]*>\s*<a[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippet_pattern = re.compile(r"<p>(?P<snippet>.*?)</p>", flags=re.IGNORECASE | re.DOTALL)

    results: List[Dict[str, str]] = []
    seen: set[str] = set()
    for item_match in item_pattern.finditer(html):
        item = item_match.group("item")
        link_match = link_pattern.search(item)
        if not link_match:
            continue
        href = link_match.group("href")
        title = _clean_html_text(link_match.group("title"))
        if not href or not title:
            continue
        if not href.startswith("http://") and not href.startswith("https://"):
            continue
        if href in seen:
            continue
        seen.add(href)
        snippet_match = snippet_pattern.search(item)
        snippet = _clean_html_text(snippet_match.group("snippet")) if snippet_match else ""
        results.append({"title": title, "url": href, "snippet": snippet})
        if len(results) >= max_results:
            break
    return results


def _provider_search(provider: str, query: str, max_results: int) -> List[Dict[str, str]]:
    if provider == "ddg":
        return _duckduckgo_search(query=query, max_results=max_results)
    if provider == "google":
        return _google_search(query=query, max_results=max_results)
    if provider == "bing":
        return _bing_search(query=query, max_results=max_results)
    return []


def _make_discovery_cache_key(subject: str, timeframe_hint: str | None, max_results: int) -> str:
    return f"{subject.strip().lower()}|{(timeframe_hint or '').strip().lower()}|{max_results}"


def _get_discovery_cache(cache_key: str) -> List[Dict[str, str]] | None:
    row = _DISCOVERY_CACHE.get(cache_key)
    if not row:
        return None
    expires_at, results = row
    if time.time() >= expires_at:
        _DISCOVERY_CACHE.pop(cache_key, None)
        return None
    return results


def _set_discovery_cache(cache_key: str, results: List[Dict[str, str]]) -> None:
    _DISCOVERY_CACHE[cache_key] = (time.time() + DISCOVERY_CACHE_TTL_SECONDS, results)


def _multi_query_web_search(
    subject: str,
    timeframe_hint: str | None,
    max_results: int,
    budget_seconds: float = WEB_SEARCH_BUDGET_SECONDS,
) -> Tuple[List[Dict[str, str]], Dict[str, Any]]:
    started_at = time.perf_counter()
    effective_max_results = max(1, min(DISCOVERY_MAX_RESULTS_CAP, int(max_results)))
    cache_key = _make_discovery_cache_key(subject=subject, timeframe_hint=timeframe_hint, max_results=effective_max_results)
    cached = _get_discovery_cache(cache_key)
    if cached is not None:
        return cached, {
            "cache_hit": True,
            "budget_seconds": budget_seconds,
            "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
            "effective_max_results": effective_max_results,
            "provider_attempts": 0,
            "variant_attempts": 0,
        }

    year = datetime.now().year
    normalized_hint = timeframe_hint or "upcoming"
    variants = [
        f"{subject} {normalized_hint} schedule date time",
        f"{subject} next match date time",
        f"{subject} fixtures {year}",
        f"{subject} official schedule",
    ]
    iso_dates = re.findall(r"\d{4}-\d{2}-\d{2}", normalized_hint)
    if iso_dates:
        variants.insert(1, f"{subject} {' '.join(iso_dates)} schedule")
    deduped: List[Dict[str, str]] = []
    seen_urls: set[str] = set()
    provider_attempts = 0
    variant_attempts = 0
    empty_variants_in_a_row = 0
    per_query = max(4, min(DISCOVERY_PER_PROVIDER_CAP, effective_max_results))
    providers = list(PROVIDER_ORDER)

    for variant in variants:
        elapsed = time.perf_counter() - started_at
        if elapsed >= budget_seconds:
            break
        variant_attempts += 1
        rows_by_provider: Dict[str, List[Dict[str, str]]] = {}
        with ThreadPoolExecutor(max_workers=len(providers)) as pool:
            futures = {
                pool.submit(
                    _provider_search,
                    provider=provider,
                    query=variant,
                    max_results=per_query,
                ): provider
                for provider in providers
            }
            remaining = max(0.1, budget_seconds - (time.perf_counter() - started_at))
            try:
                completed = as_completed(futures, timeout=remaining)
                for future in completed:
                    provider = futures[future]
                    provider_attempts += 1
                    try:
                        rows_by_provider[provider] = future.result()
                    except Exception:
                        rows_by_provider[provider] = []
            except Exception:
                # Any timeout here simply means we stop waiting and use completed rows.
                pass

        for provider in providers:
            rows = rows_by_provider.get(provider, [])
            for row in rows:
                url = row.get("url", "").strip()
                if not url or url in seen_urls:
                    continue
                seen_urls.add(url)
                deduped.append(row)
                if len(deduped) >= effective_max_results:
                    _set_discovery_cache(cache_key, deduped)
                    return deduped, {
                        "cache_hit": False,
                        "budget_seconds": budget_seconds,
                        "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
                        "effective_max_results": effective_max_results,
                        "provider_attempts": provider_attempts,
                        "variant_attempts": variant_attempts,
                    }

        if not deduped:
            empty_variants_in_a_row += 1
            if empty_variants_in_a_row >= DISCOVERY_MAX_VARIANTS_WITH_NO_HITS:
                break
        else:
            empty_variants_in_a_row = 0
            # We only need enough links to fetch a few quality documents.
            if len(deduped) >= DISCOVERY_MIN_USEFUL_RESULTS:
                break

    _set_discovery_cache(cache_key, deduped)
    return deduped, {
        "cache_hit": False,
        "budget_seconds": budget_seconds,
        "elapsed_ms": round((time.perf_counter() - started_at) * 1000, 1),
        "effective_max_results": effective_max_results,
        "provider_attempts": provider_attempts,
        "variant_attempts": variant_attempts,
    }


def _safe_json_extract(text: str) -> Dict[str, Any]:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    return {"events": []}


def search_events_on_web(
    *,
    openai_api_key: str,
    model: str,
    subject: str,
    timeframe_hint: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    max_results: int = 200,
    max_events: int = 200,
    include_extra_details: bool = False,
    timezone: str = "UTC",
) -> Dict[str, Any]:
    """
    Search the public web for event schedule details and normalize into
    event candidates suitable for create_event calls.
    """
    started_total = time.perf_counter()
    timeframe_text = timeframe_hint or "upcoming schedule"
    if start_time and end_time:
        timeframe_text = f"{timeframe_text} between {start_time} and {end_time}"
    query = f"{subject} {timeframe_text} schedule date time"
    discovery_started = time.perf_counter()
    search_results, discovery_meta = _multi_query_web_search(
        subject=subject,
        timeframe_hint=timeframe_text,
        max_results=max_results,
    )
    discovery_elapsed_ms = round((time.perf_counter() - discovery_started) * 1000, 1)
    if not search_results:
        return {
            "query": query,
            "search_results": [],
            "documents_count": 0,
            "events": [],
            "events_count": 0,
            "error": "web_search_unavailable",
            "message": "Web search provider returned no accessible results.",
            "performance": {
                "total_elapsed_ms": round((time.perf_counter() - started_total) * 1000, 1),
                "discovery_elapsed_ms": discovery_elapsed_ms,
                "documents_elapsed_ms": 0.0,
                "extraction_elapsed_ms": 0.0,
                "discovery": discovery_meta,
            },
        }
    docs_started = time.perf_counter()
    documents = _prepare_documents(
        search_results=search_results,
        max_documents=MAX_DOCUMENTS_FOR_EXTRACTION,
        budget_seconds=DOCUMENT_FETCH_BUDGET_SECONDS,
    )
    docs_elapsed_ms = round((time.perf_counter() - docs_started) * 1000, 1)
    if not documents:
        return {
            "query": query,
            "search_results": search_results,
            "documents_count": 0,
            "events": [],
            "events_count": 0,
            "error": "web_search_unavailable",
            "message": "Search results found, but no accessible event documents were fetched in time.",
            "performance": {
                "total_elapsed_ms": round((time.perf_counter() - started_total) * 1000, 1),
                "discovery_elapsed_ms": discovery_elapsed_ms,
                "documents_elapsed_ms": docs_elapsed_ms,
                "extraction_elapsed_ms": 0.0,
                "discovery": discovery_meta,
            },
        }

    client = OpenAI(api_key=openai_api_key)
    extraction_prompt = (
        "You convert schedule web page content into calendar-ready events.\n"
        "Return strict JSON only with this schema:\n"
        "{\n"
        '  "events": [\n'
        "    {\n"
        '      "summary": "string",\n'
        '      "start": "ISO-8601 with timezone offset",\n'
        '      "end": "ISO-8601 with timezone offset",\n'
        '      "timezone": "IANA timezone",\n'
        '      "description": "string",\n'
        '      "source_url": "string",\n'
        '      "confidence": 0.0\n'
        "    }\n"
        "  ]\n"
        "}\n"
        f"Use timezone '{timezone}' when exact timezone is unclear.\n"
        f"Strict required window start: {start_time or 'none'}\n"
        f"Strict required window end: {end_time or 'none'}\n"
        "Only output events grounded in the provided documents.\n"
        "Prefer trusted_source=true documents when available.\n"
        "If a start time is not available but date is known, default to 12:00 local time "
        "and set end one hour later.\n"
        f"Include extra context in description: {'yes' if include_extra_details else 'no'}.\n"
        f"Return at most {max_events} events.\n"
        "Never invent dates; skip uncertain entries."
    )
    prompt_search_results = search_results[:MAX_SEARCH_RESULTS_IN_PROMPT]
    user_payload = {
        "now_local_iso": datetime.now().astimezone().isoformat(),
        "subject": subject,
        "timeframe_hint": timeframe_hint,
        "start_time": start_time,
        "end_time": end_time,
        "timezone": timezone,
        "max_events": max_events,
        "search_results": prompt_search_results,
        "documents": documents,
    }

    extraction_started = time.perf_counter()
    completion = None
    extraction_error: str | None = None
    try:
        completion = client.chat.completions.create(
            model=model,
            temperature=0,
            timeout=EXTRACTION_TIMEOUT_SECONDS,
            messages=[
                {"role": "system", "content": extraction_prompt},
                {"role": "user", "content": json.dumps(user_payload)},
            ],
        )
    except Exception as exc:
        extraction_error = str(exc)
    extraction_elapsed_ms = round((time.perf_counter() - extraction_started) * 1000, 1)
    if completion is None:
        return {
            "query": query,
            "search_results": search_results,
            "documents_count": len(documents),
            "events": [],
            "events_count": 0,
            "error": "web_search_unavailable",
            "message": "Extraction model timed out or failed before events could be parsed.",
            "extraction_error": extraction_error or "unknown_extraction_error",
            "performance": {
                "total_elapsed_ms": round((time.perf_counter() - started_total) * 1000, 1),
                "discovery_elapsed_ms": discovery_elapsed_ms,
                "documents_elapsed_ms": docs_elapsed_ms,
                "extraction_elapsed_ms": extraction_elapsed_ms,
                "extraction_timeout_seconds": EXTRACTION_TIMEOUT_SECONDS,
                "prompt_search_results_count": len(prompt_search_results),
                "prompt_documents_count": len(documents),
                "discovery": discovery_meta,
            },
        }
    content = completion.choices[0].message.content or "{}"
    parsed = _safe_json_extract(content)
    events = parsed.get("events")
    if not isinstance(events, list):
        events = []

    min_dt = None
    max_dt = None
    if start_time:
        try:
            min_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        except ValueError:
            min_dt = None
    if end_time:
        try:
            max_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
        except ValueError:
            max_dt = None

    normalized_events: List[Dict[str, Any]] = []
    for item in events[:max_events]:
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary", "")).strip()
        start = str(item.get("start", "")).strip()
        end = str(item.get("end", "")).strip()
        if not summary or not start or not end:
            continue
        try:
            start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
        except ValueError:
            continue
        if min_dt and start_dt < min_dt:
            continue
        if max_dt and end_dt > max_dt:
            continue
        confidence_raw = item.get("confidence", 0.0)
        try:
            confidence = float(confidence_raw)
        except (TypeError, ValueError):
            confidence = 0.0

        normalized_events.append(
            {
                "summary": summary,
                "start": start,
                "end": end,
                "timezone": str(item.get("timezone", timezone)).strip() or timezone,
                "description": str(item.get("description", "")).strip() or None,
                "source_url": str(item.get("source_url", "")).strip() or None,
                "confidence": confidence,
            }
        )

    return {
        "query": query,
        "search_results": search_results,
        "documents_count": len(documents),
        "events": normalized_events,
        "events_count": len(normalized_events),
        "performance": {
            "total_elapsed_ms": round((time.perf_counter() - started_total) * 1000, 1),
            "discovery_elapsed_ms": discovery_elapsed_ms,
            "documents_elapsed_ms": docs_elapsed_ms,
            "extraction_elapsed_ms": extraction_elapsed_ms,
            "extraction_timeout_seconds": EXTRACTION_TIMEOUT_SECONDS,
            "prompt_search_results_count": len(prompt_search_results),
            "prompt_documents_count": len(documents),
            "discovery": discovery_meta,
        },
    }
