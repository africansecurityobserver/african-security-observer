"""Collect Libya/Khoms and Judicial Police stories into owner-only Supabase tables.

This job runs after the public news collector. It uses the same free Google News RSS
feeds and translation helpers, but writes only to the two private Supabase tables.
"""
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, urlencode, quote, urlsplit
from urllib.request import Request, urlopen
import json

import feedparser

from update_news import decode_url, normalize_url, translate_article, parse_published_datetime

# Supabase URL and API keys are ASCII values. Strip invisible formatting
# characters copied into GitHub Secrets before using them in HTTP requests.
_raw_supabase_url = os.getenv("SUPABASE_URL", "")
SUPABASE_URL = "".join(
    char for char in _raw_supabase_url.strip()
    if char.isascii() and not char.isspace()
).rstrip("/")
# Supabase API keys are ASCII tokens. Remove invisible Unicode formatting
# characters that can be introduced when a key is copied into GitHub Secrets.
_raw_supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")
SUPABASE_KEY = "".join(
    char for char in _raw_supabase_key.strip()
    if char.isascii() and not char.isspace()
)

# Broad collection: general web/news plus public social posts indexed by Google News.
# Multiple queries per language increase recall; duplicates are removed by source_url.
FEEDS = {
    "khoms_news": [
        ('ar', 'EG', 'EG:ar', '(الخمس OR "مدينة الخمس" OR "ميناء الخمس" OR "بلدية الخمس") (عسكري OR عسكرية OR أمن OR أمني OR أمنية OR الجيش OR القوات OR كتيبة OR لواء OR دورية OR اشتباك OR ضبط OR مداهمة OR تدريب OR قاعدة OR ميناء OR سفينة OR حرس OR شرطة) ليبيا when:7d'),
        ('ar', 'EG', 'EG:ar', '(الخمس OR "مدينة الخمس" OR "ميناء الخمس") (site:facebook.com OR site:t.me OR site:x.com OR site:youtube.com OR site:instagram.com) (أمن OR عسكري OR الجيش OR الشرطة OR كتيبة OR دورية OR ضبط OR مداهمة) ليبيا when:7d'),
        ('en-US', 'US', 'US:en', '("Al Khums" OR "Al-Khums" OR Khoms OR "Khoms Libya") (military OR security OR army OR forces OR battalion OR brigade OR patrol OR clash OR raid OR police OR navy OR port OR vessel) Libya when:7d'),
        ('en-US', 'US', 'US:en', '("Al Khums" OR "Al-Khums" OR Khoms) (site:facebook.com OR site:t.me OR site:x.com OR site:youtube.com OR site:instagram.com) (military OR security OR army OR police OR patrol OR raid) Libya when:7d'),
        ('fr', 'FR', 'FR:fr', '(Khoms OR "Al Khums" OR "Al-Khums") (militaire OR sécurité OR armée OR forces OR brigade OR patrouille OR affrontement OR police OR port OR navire) Libye when:7d'),
        ('fr', 'FR', 'FR:fr', '(Khoms OR "Al Khums" OR "Al-Khums") (site:facebook.com OR site:t.me OR site:x.com OR site:youtube.com OR site:instagram.com) (militaire OR sécurité OR armée OR police) Libye when:7d'),
    ],
    "judicial_police_news": [
        ('ar', 'EG', 'EG:ar', '("الشرطة القضائية" OR "جهاز الشرطة القضائية" OR "الإدارة العامة للشرطة القضائية" OR "فرع الشرطة القضائية" OR "مؤسسات الإصلاح والتأهيل" OR "مراكز الإصلاح والتأهيل") ليبيا when:7d'),
        ('ar', 'EG', 'EG:ar', '("الشرطة القضائية" OR "جهاز الشرطة القضائية" OR "مؤسسات الإصلاح والتأهيل" OR "مراكز الإصلاح والتأهيل") (site:facebook.com OR site:t.me OR site:x.com OR site:youtube.com OR site:instagram.com) ليبيا when:7d'),
        ('ar', 'EG', 'EG:ar', '("جهاز الشرطة القضائية" OR "الشرطة القضائية") (سجن OR نزلاء OR نقل OR ترحيل OR ضبط OR قبض OR مداهمة OR تحقيق OR تأمين OR حراسة OR تدريب OR دورة OR افتتاح OR صيانة OR زيارة OR تفتيش OR تعميم OR قرار OR تكليف) ليبيا when:7d'),
        ('en-US', 'US', 'US:en', '("Judicial Police" OR "Judicial Police Department" OR "Criminal Investigation Department" OR "Correction and Rehabilitation Institutions") Libya when:7d'),
        ('en-US', 'US', 'US:en', '("Judicial Police" OR "Correction and Rehabilitation") (site:facebook.com OR site:t.me OR site:x.com OR site:youtube.com OR site:instagram.com) Libya when:7d'),
        ('fr', 'FR', 'FR:fr', '("police judiciaire" OR "établissements de réforme et de réhabilitation") Libye when:7d'),
        ('fr', 'FR', 'FR:fr', '("police judiciaire" OR "réforme et réhabilitation") (site:facebook.com OR site:t.me OR site:x.com OR site:youtube.com OR site:instagram.com) Libye when:7d'),
    ],
}

KHOMS_TERMS = (
    "الخمس", "مدينة الخمس", "ميناء الخمس", "بلدية الخمس",
    "khoms", "al khums", "al-khums", "alkhums"
)
JUDICIAL_TERMS = (
    "الشرطة القضائية", "جهاز الشرطة القضائية",
    "الإدارة العامة للشرطة القضائية", "فرع الشرطة القضائية",
    "مؤسسات الإصلاح والتأهيل", "مراكز الإصلاح والتأهيل",
    "مؤسسة الإصلاح والتأهيل", "سجن", "السجون",
    "judicial police", "judicial police department",
    "criminal investigation department", "police judiciaire",
    "correction and rehabilitation", "établissements de réforme",
)


def make_feed_url(locale, query):
    hl, gl, ceid = locale
    return ("https://news.google.com/rss/search?q=" + quote_plus(query)
            + f"&hl={hl}&gl={gl}&ceid={ceid}")


def contains_any(text, terms):
    text = (text or "").casefold()
    return any(term.casefold() in text for term in terms)


def parse_entry_date(entry):
    value = entry.get("published") or entry.get("updated") or ""
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return parse_published_datetime(value)


def fetch_feed(url):
    request = Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; AfricanSecurityObserverPrivateNews/1.0)",
        "Accept": "application/rss+xml, application/xml, text/xml, */*",
    })
    with urlopen(request, timeout=18) as response:
        parsed = feedparser.parse(response.read())
    if getattr(parsed, "bozo", False) and not parsed.entries:
        raise ValueError("RSS feed could not be parsed")
    return parsed


def supabase_headers():
    headers = {
        "apikey": SUPABASE_KEY,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # New sb_secret_* API keys are not JWTs: send them as apikey only.
    # Legacy service_role keys are JWTs and also need the Authorization header.
    if not SUPABASE_KEY.startswith("sb_secret_"):
        headers["Authorization"] = "Bearer " + SUPABASE_KEY
    return headers


def already_exists(table, source_url):
    params = urlencode({"select": "id", "source_url": "eq." + source_url, "limit": "1"})
    request = Request(
        f"{SUPABASE_URL}/rest/v1/{table}?{params}",
        headers=supabase_headers(),
        method="GET",
    )
    with urlopen(request, timeout=20) as response:
        rows = json.loads(response.read().decode("utf-8"))
    return bool(rows)


def insert_story(table, title, content, source_url, published_at):
    payload = [{
        "title": title[:1000],
        "content": content[:12000],
        "source_url": source_url,
        "published_at": published_at.isoformat(),
    }]
    request = Request(
        f"{SUPABASE_URL}/rest/v1/{table}",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={**supabase_headers(), "Prefer": "return=minimal"},
        method="POST",
    )
    with urlopen(request, timeout=25) as response:
        return response.status in (200, 201, 204)


def main():
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("Private-news sync skipped: add SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY to GitHub Actions secrets.")
        return 0

    now = datetime.now(timezone.utc)
    inserted = {table: 0 for table in FEEDS}
    failures = 0
    stats = {
        "feeds": 0, "entries": 0, "empty_titles": 0, "excluded": 0,
        "unmatched_topic": 0, "invalid_dates": 0, "outside_window": 0,
        "missing_links": 0, "duplicates": 0, "inserted": 0,
    }

    for table, searches in FEEDS.items():
        for language, gl, ceid, query in searches:
            url = make_feed_url((language, gl, ceid), query)
            stats["feeds"] += 1
            try:
                feed = fetch_feed(url)
            except Exception as exc:
                failures += 1
                print(f"RSS error [{table}/{language}]: {type(exc).__name__}: {exc}")
                continue

            print(f"RSS fetched [{table}/{language}]: {len(feed.entries)} entr(y/ies)")
            for entry in feed.entries[:100]:
                stats["entries"] += 1
                raw_title = str(entry.get("title", "") or "").strip()
                raw_description = str(entry.get("summary", entry.get("description", "")) or "").strip()
                text = (raw_title + " " + raw_description).casefold()
                if not raw_title:
                    stats["empty_titles"] += 1
                    continue
                if contains_any(text, EXCLUDED):
                    stats["excluded"] += 1
                    continue

                if table == "khoms_news" and not contains_any(text, KHOMS_TERMS):
                    stats["unmatched_topic"] += 1
                    continue
                if table == "judicial_police_news" and not contains_any(text, JUDICIAL_TERMS):
                    stats["unmatched_topic"] += 1
                    continue

                published = parse_entry_date(entry)
                if published is None:
                    stats["invalid_dates"] += 1
                    continue
                if now - published > timedelta(days=7) or published - now > timedelta(hours=24):
                    stats["outside_window"] += 1
                    continue

                google_url = str(entry.get("link", "") or "").strip()
                if not google_url:
                    stats["missing_links"] += 1
                    continue
                try:
                    source_url = normalize_url(decode_url(google_url))
                except Exception:
                    source_url = normalize_url(google_url)
                if not source_url.startswith(("http://", "https://")):
                    continue

                source_obj = entry.get("source") or {}
                source = str(source_obj.get("title", "") or "").strip() if hasattr(source_obj, "get") else ""
                if not source:
                    source = (urlsplit(source_url).hostname or "").removeprefix("www.")

                try:
                    title, description = translate_article(raw_title, raw_description)
                    content = description.strip()
                    if source:
                        content = (content + "\n\nالمصدر: " + source).strip()
                    if already_exists(table, source_url):
                        stats["duplicates"] += 1
                        continue
                    if insert_story(table, title or raw_title, content, source_url, published):
                        inserted[table] += 1
                        stats["inserted"] += 1
                        print(f"Inserted [{table}]: {title or raw_title}")
                    time.sleep(0.15)
                except Exception as exc:
                    failures += 1
                    print(f"Supabase sync error [{table}]: {type(exc).__name__}: {exc}")

    print("Private-news sync totals: " + ", ".join(f"{table}={count}" for table, count in inserted.items()))
    print("Private-news diagnostic counters: " + ", ".join(f"{name}={count}" for name, count in stats.items()))
    if failures:
        print(f"Completed with {failures} feed/record error(s); next scheduled cycle will retry.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
