import hashlib, json, os, re, io
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, urljoin
from zoneinfo import ZoneInfo
import requests
from googlenewsdecoder import gnewsdecoder
from selectolax.lexbor import LexborHTMLParser
from pypdf import PdfReader
from google import genai
from google.genai import types

ROOT=Path(__file__).resolve().parent.parent
PUB=ROOT/"research_publications.json"
DATA=ROOT/"research_cards.json"
ARCHIVE=ROOT/"research_cards"/"archive"
INDEX=ARCHIVE/"index.json"
TZ=ZoneInfo("Africa/Tunis")
MODEL=os.getenv("GEMINI_MODEL","gemini-3.5-flash-lite")
# Process every newly discovered, in-scope publication; do not select only a daily quota.
HEADERS={"User-Agent":"Mozilla/5.0 (compatible; AfricanSecurityObserver/1.0)"}

def load(path, default):
    try: return json.loads(path.read_text(encoding="utf-8"))
    except (OSError,json.JSONDecodeError): return default

def save(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")

def clean(value):
    value=re.sub(r"<[^>]+>"," ",str(value or ""))
    return re.sub(r"\s+"," ",value).strip()

def canonical_url(url):
    """Collapse Google News locale wrappers to one stable article identity."""
    url = clean(url)
    try:
        parsed = urlparse(url)
        if parsed.netloc.lower().endswith("news.google.com") and parsed.path.startswith("/rss/articles/"):
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    except Exception:
        pass
    return url.rstrip("/")


def identity_text(value):
    value = clean(value).casefold()
    value = re.sub(r"[^\w]+", " ", value, flags=re.UNICODE)
    return " ".join(value.split())


def publication_identity(item):
    center = identity_text(item.get("center") or item.get("source") or "")
    title = identity_text(item.get("title") or item.get("source_title") or "")
    return (center, title) if center and title else None


def is_generic_listing_title(title):
    return bool(re.match(
        r"^(?:publications|latest publications|all publications|research publications)\s*(?:[-|:]|$)",
        clean(title).casefold()
    ))


def fetch_url(url):
    """Fetch a page or PDF with bounded timeouts and redirects."""
    response = requests.get(url, headers=HEADERS, timeout=30, allow_redirects=True)
    response.raise_for_status()
    return response


def extract_pdf_text(response):
    """Extract readable text from a PDF; return empty text for scanned/unreadable files."""
    try:
        reader = PdfReader(io.BytesIO(response.content))
        pages = []
        for page in reader.pages[:100]:
            text = page.extract_text() or ""
            if text.strip():
                pages.append(text)
        return clean(" ".join(pages))[:60000]
    except Exception as exc:
        print(f"PDF text extraction failed: {type(exc).__name__}")
        return ""


def html_main_text(html):
    parser = LexborHTMLParser(html)
    for sel in ("script", "style", "nav", "footer", "header", "aside", "form", "noscript"):
        for node in parser.css(sel):
            node.decompose()
    candidates = []
    for sel in ("article", "main", "[role=main]", ".article-body", ".entry-content",
                ".post-content", ".report-content", ".publication-content"):
        for node in parser.css(sel):
            text = clean(node.text(separator=" "))
            if len(text) > max([len(x) for x in candidates] or [0]):
                candidates = [text]
    if not candidates:
        candidates = [clean(node.text(separator=" ")) for node in parser.css("p")]
    return max(candidates, key=len, default="")[:60000]


def find_official_article_url(title, center_site):
    """
    Resolve Google News wrappers to a publication page on the research
    centre's own domain. Try search engines and the centre's native search
    pages because Google News encoded URLs and external search results can fail.
    """
    title = clean(title)
    expected_host = urlparse(center_site).netloc.lower().removeprefix("www.")
    if not title or not expected_host:
        return ""

    headers = dict(HEADERS)
    headers["Accept-Language"] = "en-US,en;q=0.9,es;q=0.8,fr;q=0.7"
    # Keep a shorter query too; some centre search pages return nothing for a
    # long exact-title query.
    title_words = re.findall(r"[A-Za-z0-9À-ÿ]{3,}", title)
    short_title = " ".join(title_words[:7])
    searches = [
        ("https://www.google.com/search", {"q": f'site:{expected_host} "{title}"', "num": "10"}),
        ("https://www.bing.com/search", {"q": f'site:{expected_host} "{title}"', "count": "10"}),
        # GitHub-hosted runners are often blocked by Google/Bing and publisher
        # site-search endpoints. DuckDuckGo HTML is an independent fallback.
        ("https://html.duckduckgo.com/html/", {"q": f'site:{expected_host} "{title}"'}),
        ("https://html.duckduckgo.com/html/", {"q": f'site:{expected_host} {short_title}'}),
    ]
    # Common WordPress and CMS-native search patterns. These search the
    # official publisher itself and avoid depending exclusively on Google/Bing.
    base = "https://" + expected_host
    for query in (title, short_title):
        if query:
            searches.extend([
                (base + "/", {"s": query}),
                (base + "/en/", {"s": query}),
                (base + "/search/", {"q": query}),
            ])

    title_tokens = {
        token.lower() for token in re.findall(r"[A-Za-z0-9À-ÿ]{3,}", title)
    }
    best_url = ""
    best_score = 0.0
    seen_pages = set()

    for search_url, params in searches:
        try:
            response = requests.get(
                search_url, params=params, headers=headers, timeout=18,
                allow_redirects=True
            )
            if response.status_code != 200 or not response.text:
                print(f"Publication URL search returned HTTP {response.status_code}: {response.url}")
                continue
            page_key = response.url
            if page_key in seen_pages:
                continue
            seen_pages.add(page_key)
            parser = LexborHTMLParser(response.text)

            # Search-result links often have useful text in title/aria-label,
            # even when their visible text is just an icon.
            for node in parser.css("a[href]"):
                href = node.attributes.get("href", "").strip()
                # DuckDuckGo wraps outbound links; unwrap before checking the
                # destination domain, otherwise valid results look like DDG URLs.
                if "duckduckgo.com/l/?" in href:
                    from urllib.parse import parse_qs, unquote
                    href = parse_qs(urlparse(href).query).get("uddg", [""])[0]
                    href = unquote(href)
                elif href.startswith("//"):
                    href = "https:" + href
                label = clean(" ".join([
                    node.text(separator=" "),
                    node.attributes.get("title", ""),
                    node.attributes.get("aria-label", "")
                ]))
                if href.startswith("/url?q="):
                    from urllib.parse import parse_qs
                    href = parse_qs(urlparse(href).query).get("q", [""])[0]
                href = urljoin(response.url, href)
                parsed = urlparse(href)
                host = parsed.netloc.lower().removeprefix("www.")
                if not href.startswith("http") or not (
                    host == expected_host or host.endswith("." + expected_host)
                ):
                    continue
                path_lower = parsed.path.lower().rstrip("/")
                if path_lower in ("", "/") or any(
                    part in path_lower for part in (
                        "/search", "/tag/", "/category/", "/author/",
                        "/events", "/newsletter", "/contact", "/about",
                        "/privacy", "/cookie", "/page/"
                    )
                ):
                    continue

                path_words = parsed.path.replace("-", " ").replace("_", " ")
                haystack = (label + " " + path_words).lower()
                overlap = sum(1 for token in title_tokens if token in haystack)
                score = overlap / max(1, len(title_tokens))
                if label and label.lower() in ("read more", "learn more", "download", "click here"):
                    score *= 0.25
                # Prefer a candidate whose visible title closely matches the
                # publication title, and penalize links to generic landing pages.
                if len(path_lower.strip("/").split("/")) <= 1:
                    score *= 0.85
                if score > best_score:
                    best_score, best_url = score, href.split("#", 1)[0]

        except Exception as exc:
            print(f"Publication URL search failed ({search_url}): {type(exc).__name__}")

    # Require a meaningful title match. A low threshold previously accepted
    # unrelated pages as the source.
    if best_url and best_score >= 0.35:
        print(f"Resolved Google News item to official source ({best_score:.2f} title match): {best_url}")
        return best_url
    if best_url:
        print(f"Rejected weak official-source match ({best_score:.2f}); will keep searching: {best_url}")
    print(f"Could not resolve official article URL after search-engine and publisher-site searches: {title[:120]}")
    return ""


def fetch_reader_text(url):
    """Use Jina Reader as an independent extraction path for JS-heavy or blocked pages."""
    if not url or not str(url).startswith(("http://", "https://")):
        return ""
    try:
        target = "https://r.jina.ai/http://" + str(url).split("://", 1)[-1]
        response = requests.get(
            target,
            headers={**HEADERS, "Accept": "text/plain"},
            timeout=35,
            allow_redirects=True,
        )
        if response.status_code != 200 or not response.text:
            print(f"Reader fallback returned HTTP {response.status_code}: {url[:120]}")
            return ""
        return clean(response.text)[:60000]
    except Exception as exc:
        print(f"Reader fallback failed ({type(exc).__name__}): {url[:120]}")
        return ""


def get_text(url, center_site="", title=""):
    """
    Prefer the full report/PDF linked from the official publication page.
    A readable abstract is only a fallback after checking the page's download links.
    """
    original_url = url
    try:
        if "news.google.com" in urlparse(url).netloc.lower():
            try:
                decoded = gnewsdecoder(url, interval=0.5)
                if decoded.get("success") and decoded.get("decoded_url"):
                    url = decoded["decoded_url"]
            except Exception as exc:
                print(f"Google News URL decode failed: {type(exc).__name__}")

            # Recent Google News RSS links often remain opaque even after
            # decoding. Search for the exact headline on the official centre
            # domain rather than repeatedly requesting the unusable wrapper.
            if "news.google.com" in urlparse(url).netloc.lower():
                official_url = find_official_article_url(title, center_site)
                if official_url:
                    url = official_url

        response = fetch_url(url)
        content_type = response.headers.get("content-type", "").lower()
        if "pdf" in content_type or response.url.lower().split("?")[0].endswith(".pdf"):
            return response.url, extract_pdf_text(response)

        page_url = response.url
        html = response.text
        page_text = html_main_text(html)
        # Independent extraction for pages whose visible HTML is blocked,
        # sparse, or rendered by JavaScript. Keep the longer substantive version.
        if len(page_text.strip()) < 1800:
            reader_body = fetch_reader_text(response.url)
            if len(reader_body) > len(page_text):
                page_text = reader_body
        parser = LexborHTMLParser(html)
        expected_host = urlparse(center_site).netloc.lower().removeprefix("www.")
        page_host = urlparse(page_url).netloc.lower().removeprefix("www.")

        # If a Google News wrapper was not decoded cleanly, locate the publisher
        # link on the wrapper page, then inspect that publisher page for its report.
        if "news.google.com" in urlparse(page_url).netloc.lower():
            for node in parser.css("a[href]"):
                href = urljoin(page_url, node.attributes.get("href", ""))
                host = urlparse(href).netloc.lower().removeprefix("www.")
                if href.startswith("http") and (
                    not expected_host or host == expected_host or host.endswith("." + expected_host)
                ):
                    try:
                        direct = fetch_url(href)
                        direct_type = direct.headers.get("content-type", "").lower()
                        if "pdf" in direct_type or direct.url.lower().split("?")[0].endswith(".pdf"):
                            body = extract_pdf_text(direct)
                            if len(body) < 1200:
                                reader_body = fetch_reader_text(direct.url)
                                if len(reader_body) > len(body):
                                    body = reader_body
                            if len(body) >= 1200:
                                return direct.url, body
                        page_url, html = direct.url, direct.text
                        page_text = html_main_text(html)
                        parser = LexborHTMLParser(html)
                        page_host = urlparse(page_url).netloc.lower().removeprefix("www.")
                        break
                    except Exception:
                        continue

        # Prefer links on the actual publisher page, especially direct PDF/full-report
        # downloads. The download may be hosted on a subdomain/CDN of the same center.
        link_candidates = []
        seen_links = set()
        expected_host = expected_host or page_host
        positive = re.compile(
            r"full.?text|full.?report|download|pdf|read.?report|report|publication|"
            r"النص الكامل|تحميل|تنزيل|الدراسة كاملة|التقرير الكامل|الدراسة|التقرير",
            re.I
        )
        negative = re.compile(
            r"privacy|cookie|subscribe|newsletter|donate|login|register|"
            r"سياسة الخصوصية|اشترك|تبرع|تسجيل الدخول",
            re.I
        )
        for node in parser.css("a[href]"):
            href = urljoin(page_url, node.attributes.get("href", "").strip())
            label = clean(node.text(separator=" "))
            combined = label + " " + href
            if not href.startswith("http") or href in seen_links or negative.search(combined):
                continue
            host = urlparse(href).netloc.lower().removeprefix("www.")
            if not (host == expected_host or host.endswith("." + expected_host)):
                continue
            if positive.search(combined) or href.lower().split("?")[0].endswith(".pdf"):
                seen_links.add(href)
                score = 0
                if href.lower().split("?")[0].endswith(".pdf"):
                    score += 6
                if re.search(r"full.?text|full.?report|النص الكامل|الدراسة كاملة|التقرير الكامل", combined, re.I):
                    score += 8
                if re.search(r"download|تحميل|تنزيل|pdf", combined, re.I):
                    score += 4
                if re.search(r"executive.?summary|ملخص تنفيذي", combined, re.I):
                    score -= 4
                link_candidates.append((score, href, label))

        link_candidates.sort(key=lambda item: item[0], reverse=True)
        for score, candidate_url, label in link_candidates[:15]:
            try:
                linked = fetch_url(candidate_url)
                ctype = linked.headers.get("content-type", "").lower()
                if "pdf" in ctype or linked.url.lower().split("?")[0].endswith(".pdf"):
                    body = extract_pdf_text(linked)
                else:
                    body = html_main_text(linked.text)
                if len(body) < 1200:
                    reader_body = fetch_reader_text(linked.url)
                    if len(reader_body) > len(body):
                        body = reader_body
                if len(body) >= 1200:
                    print(
                        f"Found linked report/full text: {label[:80]} -> {linked.url} "
                        f"({len(body)} chars)"
                    )
                    return linked.url, body
            except Exception as exc:
                print(f"Official full-text candidate failed ({candidate_url}): {type(exc).__name__}")

        # If the official page already exposes a substantial article, the
        # official download links have been checked above; avoid wasting several
        # minutes searching the whole web for a duplicate copy of the same text.
        if len(page_text.strip()) >= 5000:
            print(f"Using substantial official publication text ({len(page_text.strip())} chars): {page_url}")
            return page_url, page_text

        # Deep fallback search: do not declare full text unavailable merely because
        # the first publisher page has no obvious PDF. Search for the exact title,
        # PDF variants, and repository copies; inspect actual candidate pages/files,
        # never use search snippets as evidence for a card.
        deep_candidates = []
        deep_seen = {page_url, url, original_url}
        # Publisher metadata sometimes points directly to a PDF even when no
        # visible download button exists.
        for node in parser.css('meta[content]'):
            meta_name = (node.attributes.get("name") or node.attributes.get("property") or "").lower()
            meta_value = node.attributes.get("content", "").strip()
            if meta_value and any(term in meta_name for term in (
                "citation_pdf_url", "eprints.document_url", "dc.identifier",
                "og:pdf", "pdf_url", "fulltext_url"
            )):
                candidate = urljoin(page_url, meta_value)
                if candidate.startswith("http") and candidate not in deep_seen:
                    deep_candidates.append((10 if ".pdf" in candidate.lower() else 6, candidate))
                    deep_seen.add(candidate)
        title_query = clean(title or "")
        if title_query:
            # Search the exact publication across the wider web, not only the
            # publisher domain. Full reports are often mirrored by universities,
            # think tanks, repositories, partner organizations, or other outlets.
            queries = [
                f'"{title_query}"',
                f'"{title_query}" PDF',
                f'"{title_query}" full text',
                f'"{title_query}" filetype:pdf',
                f'"{title_query}" report download',
            ]
            # Add an unquoted title query to find mirrors whose metadata or
            # punctuation differs from the original title.
            queries.append(title_query)
            for engine, base_url, key, extra in (
                ("Google", "https://www.google.com/search", "q", {"num": "10"}),
                ("Bing", "https://www.bing.com/search", "q", {"count": "10"}),
                ("DuckDuckGo", "https://html.duckduckgo.com/html/", "q", {}),
            ):
                for query in queries:
                    try:
                        params = {key: query, **extra}
                        search_response = requests.get(
                            base_url, params=params, headers=HEADERS, timeout=15,
                            allow_redirects=True
                        )
                        if search_response.status_code != 200 or not search_response.text:
                            print(f"Deep full-text search {engine} returned HTTP {search_response.status_code}")
                            continue
                        search_parser = LexborHTMLParser(search_response.text)
                        for anchor in search_parser.css("a[href]"):
                            href = anchor.attributes.get("href", "").strip()
                            if href.startswith("/url?q="):
                                from urllib.parse import parse_qs
                                href = parse_qs(urlparse(href).query).get("q", [""])[0]
                            elif "duckduckgo.com/l/?" in href:
                                from urllib.parse import parse_qs, unquote
                                href = unquote(parse_qs(urlparse(href).query).get("uddg", [""])[0])
                            elif href.startswith("//"):
                                href = "https:" + href
                            href = urljoin(search_response.url, href).split("#", 1)[0]
                            parsed_href = urlparse(href)
                            host = parsed_href.netloc.lower()
                            if not href.startswith("http") or host.endswith(("google.com", "bing.com", "duckduckgo.com")):
                                continue
                            if href in deep_seen:
                                continue
                            if any(term in parsed_href.path.lower() for term in (
                                "/search", "/login", "/signup", "/privacy", "/cookie"
                            )):
                                continue
                            label = clean(anchor.text(separator=" ") + " " + anchor.attributes.get("title", ""))
                            combined = (label + " " + href).lower()
                            score = 0
                            if parsed_href.path.lower().endswith(".pdf") or "pdf" in combined:
                                score += 8
                            if any(term in combined for term in (
                                "full text", "full report", "download", "repository",
                                "handle/", "bitstream", "document", "working paper"
                            )):
                                score += 4
                            title_tokens = set(re.findall(r"[a-z0-9À-ÿ]{4,}", title_query.lower()))
                            overlap = sum(1 for token in title_tokens if token in combined)
                            score += min(8, overlap)
                            # A mirror can be on any domain, but the result must
                            # still be fetched and checked for substantive text.
                            deep_candidates.append((score, href))
                            deep_seen.add(href)
                    except Exception as exc:
                        print(f"Deep full-text search failed ({engine}): {type(exc).__name__}")
        deep_candidates.sort(key=lambda item: item[0], reverse=True)
        tried_deep = 0
        for score, candidate_url in deep_candidates:
            if tried_deep >= 30:
                break
            tried_deep += 1
            try:
                linked = fetch_url(candidate_url)
                ctype = linked.headers.get("content-type", "").lower()
                final_url = linked.url
                if "pdf" in ctype or final_url.lower().split("?")[0].endswith(".pdf"):
                    body = extract_pdf_text(linked)
                elif "html" in ctype or "text" in ctype or not ctype:
                    body = html_main_text(linked.text)
                else:
                    continue
                if len(body.strip()) < 1200:
                    reader_body = fetch_reader_text(final_url)
                    if len(reader_body) > len(body):
                        body = reader_body
                if len(body.strip()) >= 1200:
                    print(
                        f"Deep search found accessible full text ({len(body.strip())} chars): "
                        f"{final_url}"
                    )
                    return final_url, body
                print(f"Deep-search candidate was not full text ({len(body.strip())} chars): {final_url}")
            except Exception as exc:
                print(f"Deep-search candidate failed ({candidate_url}): {type(exc).__name__}")

        # Only fall back to the publication page after attempting its linked
        # full report. The card generator still rejects pages under 1,800 chars.
        if len(page_text.strip()) >= 1200:
            print(
                f"No longer linked report found; using substantial source-page text: "
                f"{page_url} ({len(page_text)} chars)"
            )
            return page_url, page_text

        print(
            f"No substantial full text found after checking official download links: "
            f"{page_url}; page chars={len(page_text)}"
        )
        return page_url or original_url, page_text
    except Exception as exc:
        print(f"Article/full-text extraction failed: {type(exc).__name__}")
        return original_url, ""

def make_index(cards):
    years={}
    for c in cards:
        d=clean(c.get("date"))
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}",d): continue
        y,m,_=d.split("-")
        years.setdefault(y,{}).setdefault(m,[]).append({k:c.get(k,"") for k in ("date","title","subject","path","center","source_title")}|{"slot":"research","kind_label":"بطاقة استراتيجية من مراكز البحث"})
    out={"updated":datetime.now(TZ).isoformat(timespec="seconds"),"years":[]}
    for y in sorted(years,reverse=True):
        months=[]
        for m in sorted(years[y],reverse=True):
            months.append({"month":m,"cards":sorted(years[y][m],key=lambda x:(x["date"],x["title"]),reverse=True)})
        out["years"].append({"year":y,"months":months})
    return out

def main():
    key=os.getenv("GEMINI_API_KEY")
    if not key: raise RuntimeError("GEMINI_API_KEY is missing.")
    pd=load(PUB,{}); publications=pd.get("publications",[]) if isinstance(pd,dict) else []
    data=load(DATA,{"updated":None,"cards":[],"processed_urls":[]})
    if not isinstance(data,dict): data={"updated":None,"cards":[],"processed_urls":[]}
    data.setdefault("cards",[]); data.setdefault("processed_urls",[])

    # Clean duplicates already stored from Google News locale variants.
    unique_cards = {}
    removed_paths = []
    for card in data["cards"]:
        if not isinstance(card, dict):
            continue
        if is_generic_listing_title(card.get("source_title") or card.get("title", "")):
            if card.get("path"):
                removed_paths.append(card["path"])
            continue
        identity = publication_identity({
            "center": card.get("center"),
            "title": card.get("source_title") or card.get("title")
        })
        if not identity:
            identity = ("url", canonical_url(card.get("source_url", "")))
        previous = unique_cards.get(identity)
        if previous is None:
            unique_cards[identity] = card
            continue
        # Keep the richer card if two versions of the same publication exist.
        score = lambda item: len(clean(item.get("summary", ""))) + sum(
            len(clean(section.get("text", ""))) for section in item.get("sections", [])
            if isinstance(section, dict)
        )
        if score(card) > score(previous):
            if previous.get("path"):
                removed_paths.append(previous["path"])
            unique_cards[identity] = card
        elif card.get("path"):
            removed_paths.append(card["path"])

    data["cards"] = list(unique_cards.values())
    for relative_path in set(removed_paths):
        target = (ARCHIVE / relative_path).resolve()
        if target.is_file() and ARCHIVE.resolve() in target.parents:
            target.unlink()
            print("Removed duplicate research-card archive file:", relative_path)

    known = {canonical_url(url) for url in data["processed_urls"] if url}
    known.update(canonical_url(card.get("source_url", "")) for card in data["cards"] if isinstance(card, dict))
    # Also mark each publication identity as known, not only its raw RSS URL.
    known_identities = {publication_identity({
        "center": card.get("center"),
        "title": card.get("source_title") or card.get("title")
    }) for card in data["cards"] if isinstance(card, dict)}
    candidates = []
    candidate_ids = set()
    for publication in publications:
        if not isinstance(publication, dict) or not publication.get("url"):
            continue
        if is_generic_listing_title(publication.get("title", "")):
            continue
        url_key = canonical_url(publication["url"])
        identity = publication_identity(publication)
        if url_key in known or (identity and identity in known_identities) or (identity and identity in candidate_ids):
            continue
        candidates.append(publication)
        known.add(url_key)
        if identity:
            candidate_ids.add(identity)
    # Process highest relevance first, newest first within each relevance tier.
    ordered=[]
    for tier in sorted({int(p.get("relevance_tier",9) or 9) for p in candidates}):
        ordered.extend(sorted(
            [p for p in candidates if int(p.get("relevance_tier",9) or 9)==tier],
            key=lambda p:str(p.get("published") or p.get("date") or ""),
            reverse=True
        ))
    candidates=ordered
    if not candidates:
        data["processed_urls"] = list(dict.fromkeys(
            canonical_url(url) for url in data["processed_urls"] if url
        ))
        data["updated"] = datetime.now(TZ).isoformat(timespec="seconds")
        save(DATA, data)
        save(INDEX, make_index(data["cards"]))
        print("No new research publications to convert; duplicate cleanup saved.")
        return
    client=genai.Client(api_key=key,http_options=types.HttpOptions(timeout=180000))
    schema={"type":"object","properties":{
      "publish":{"type":"boolean"},"introduction":{"type":"string"},
      "analysis_one":{"type":"array","items":{"type":"string"}},
      "analysis_two":{"type":"array","items":{"type":"string"}},
      "analysis_three":{"type":"array","items":{"type":"string"}},
      "strategic_conclusion":{"type":"string"},"summary":{"type":"string"}},
      "required":["publish","introduction","analysis_one","analysis_two","analysis_three","strategic_conclusion","summary"]}
    count=0
    failed_count=0
    for p in candidates:
        title=clean(p.get("title")); title_ar=clean(p.get("title_ar")); center=clean(p.get("center") or p.get("source") or "مركز بحثي")
        url=clean(p.get("url")); desc=clean(p.get("description")); published=clean(p.get("published") or p.get("date"))
        resolved,body=get_text(url,clean(p.get("center_site")),title)

        # Hard rule: a strategic card may only be written from the publication's
        # accessible full text. A title, snippet, search-result extract, or short
        # description is not enough evidence for a multi-section analytical card.
        # Leave incomplete items unprocessed so a later six-hour run can retry.
        MIN_FULL_TEXT_CHARS = 1200
        if len(body.strip()) < MIN_FULL_TEXT_CHARS:
            print(
                "Full publication text unavailable after checking the source page "
                "and linked official full-text/PDF downloads; skipping card and "
                "leaving publication pending for retry:",
                title[:140],
                f"(extracted_chars={len(body.strip())}, source={resolved})"
            )
            failed_count += 1
            continue
        prompt=f"""
أنت محرر بطاقة استراتيجية عربية في المرصد الإفريقي للأمن والدفاع. أنشئ بطاقة واحدة تلخص هذه النشرية وحدها.
العنوان الأصلي الذي نشره المركز يجب أن يبقى محفوظاً كما هو: {title}
العنوان العربي المتاح: {title_ar}
المركز: {center}; تاريخ النشر: {published}; الرابط: {resolved}
أعد JSON:
publish boolean; introduction string; analysis_one, analysis_two, analysis_three مصفوفات نصوص؛ strategic_conclusion string؛ summary string.
الشكل خمسة عناصر فقط: (1) مقدمة عربية تذكر الموضوع والمركز وتنتهي حرفياً «وفيما يلي أهم ما ورد فيها.»؛
(2)(3)(4) ثلاثة عناصر تحليلية، كل عنصر مصفوفة من أربع نقاط بالضبط، كل نقطة نحو 45-65 كلمة عربية، تشرح جوانب مختلفة من النشرية؛
(5) حوصلة استراتيجية 120-170 كلمة، تستند إلى المادة، وتنتهي بجملة تربط الموضوع بتونس وتحتوي كلمة «تونس».
لا تخترع حقائق أو أرقاماً أو دوافع. ميّز بين وقائع الكاتب وتحليله. اشرح الاختصارات عند أول ذكر. لا تكرر المعنى. أعد publish=false إذا كانت المادة سطحية أو لا تسمح ببطاقة موثوقة.
وصف المصدر: {desc[:2200]}
النص المستخرج من صفحة المصدر:
{body[:22000]}
"""
        try:
            response=client.interactions.create(model=MODEL,input=prompt,response_format={"type":"text","mime_type":"application/json","schema":schema},generation_config={"temperature":0.2,"thinking_level":"low"})
            out=json.loads(response.output_text or "{}")
        except Exception as e:
            print("Generation failed; will retry later:",type(e).__name__)
            failed_count+=1
            continue
        if not out.get("publish"):
            # Never mark a relevant publication as permanently processed just
            # because one model response judged its content insufficient.
            # It stays pending and is retried on the next scheduled three-hour run.
            print("Model could not yet produce a reliable card; leaving publication pending:",title[:100])
            failed_count+=1
            continue
        intro=clean(out.get("introduction")); groups=[out.get("analysis_one",[]),out.get("analysis_two",[]),out.get("analysis_three",[])]
        if not intro.endswith("وفيما يلي أهم ما ورد فيها."): intro=re.sub(r"[.!؟؛،\s]+$","",intro)+". وفيما يلي أهم ما ورد فيها."
        if any(not isinstance(g,list) or len(g)!=4 for g in groups):
            print("Invalid bullet structure; will retry later:",title[:100])
            failed_count+=1
            continue
        sections=[{"heading":"المقدمة","text":intro}]
        for h,g in zip(["أولاً: محاور النشرية ومرتكزاتها","ثانياً: النتائج والتفاصيل الرئيسية","ثالثاً: التداعيات والقيود"],groups):
            sections.append({"heading":h,"text":"\n".join("• "+clean(x) for x in g)})
        conclusion=clean(out.get("strategic_conclusion"))
        conclusion=conclusion.rstrip(" .،؛")+". وتبرز أهمية هذه المسائل بالنسبة إلى تونس في متابعة انعكاساتها على أمنها ومصالحها الاستراتيجية."
        if len(intro)<80 or len(conclusion)<180 or any(len(clean(x))<180 for g in groups for x in g):
            print("Card failed length validation; will retry later:",title[:100])
            failed_count+=1
            continue
        slug=hashlib.sha1(url.encode()).hexdigest()[:12]
        now=datetime.now(TZ); date=published[:10] if re.match(r"\d{4}-\d{2}-\d{2}",published) else now.strftime("%Y-%m-%d")
        path=f"{date[:7]}/{date.replace('-','')}_{slug}.json"
        card={"id":slug,"kind":"research_center","kind_label":"بطاقة استراتيجية من مراكز البحث",
          "title":title_ar or title,"source_title":title,"subject":title_ar or title,"center":center,
          "date":date,"published":published,"region":clean(p.get("relevance_label")),"summary":clean(out.get("summary")),
          "sections":sections,"sources":[{"title":f"{center} — {title}","url":resolved if resolved.startswith("http") else url}],
          "source_url":url,"source_original_url":resolved if resolved.startswith("http") else url,"path":path}
        save(ARCHIVE/path,card); data["cards"].append(card); data["processed_urls"].append(canonical_url(url)); count+=1
        print("Published research card:",card["title"][:150])
    data["cards"]=sorted(data["cards"],key=lambda c:(c.get("date",""),c.get("title","")),reverse=True)
    data["processed_urls"]=list(dict.fromkeys(canonical_url(url) for url in data["processed_urls"] if url))
    data["updated"]=datetime.now(TZ).isoformat(timespec="seconds")
    save(DATA,data); save(INDEX,make_index(data["cards"]))
    print(f"Research cards total={len(data['cards'])}; generated this run={count}; pending for retry={failed_count}; new candidates seen={len(candidates)}")

if __name__=="__main__": main()
