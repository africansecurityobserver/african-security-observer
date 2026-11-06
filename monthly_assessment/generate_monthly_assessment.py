import json
import os
import re
import hashlib
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote_plus
from zoneinfo import ZoneInfo

import requests
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Africa/Tunis")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
NEWS_FILE = ROOT / "news.json"
ARCHIVE = ROOT / "monthly_assessments" / "archive"
INDEX = ARCHIVE / "index.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AfricanSecurityObserver/1.0)"}
MAX_ITEMS_PER_REGION = 22

EXPECTED = [
    "المقدمة", "تونس وشمال إفريقيا", "بقية إفريقيا",
    "أوروبا والفضاء الأطلسي", "الشرق الأوسط",
    "الولايات المتحدة والقوى الدولية الأخرى",
    "الاتجاهات الاستراتيجية العابرة للأقاليم",
    "الحوصلة الاستراتيجية والسيناريوهات وانعكاساتها على تونس",
]
MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
          "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]
REGIONS = [
    ("تونس وشمال إفريقيا", '"Tunisia" OR "Libya" OR "Algeria" OR "Morocco" OR "Egypt" OR "North Africa"'),
    ("بقية إفريقيا", 'Africa OR Sahel OR Sudan OR Somalia OR Ethiopia OR Congo OR Nigeria OR "West Africa"'),
    ("أوروبا والفضاء الأطلسي", 'Europe OR NATO OR Ukraine OR Russia OR France OR Germany OR Britain OR "European Union"'),
    ("الشرق الأوسط", '"Middle East" OR Israel OR Iran OR Lebanon OR Syria OR Iraq OR Yemen OR Gulf'),
    ("الولايات المتحدة والقوى الدولية الأخرى", '"United States" OR Pentagon OR China OR Taiwan OR Japan OR India OR Brazil OR "global security"'),
    ("الأمن والتحالفات الدولية", 'NATO OR "United Nations" OR "African Union" OR "arms control" OR "defense industry" OR "military technology"'),
]
UNREST = '(protest OR protests OR demonstration OR riot OR "civil unrest" OR "political crisis" OR terrorism OR coup OR "state of emergency")'


def clean(value):
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", value).strip()


def target_month():
    requested = os.getenv("ASSESSMENT_MONTH", "").strip()
    if requested:
        try:
            year, month = map(int, requested.split("-"))
            return date(year, month, 1)
        except Exception as exc:
            raise RuntimeError("ASSESSMENT_MONTH must use YYYY-MM.") from exc
    today = datetime.now(TZ).date()
    return (today.replace(day=1) - timedelta(days=1)).replace(day=1)


def month_end(first):
    if first.month == 12:
        return date(first.year + 1, 1, 1) - timedelta(days=1)
    return date(first.year, first.month + 1, 1) - timedelta(days=1)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_date(value):
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=TZ)
        return parsed.astimezone(TZ)
    except Exception:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(TZ)
        except Exception:
            return None


def fetch_month_news(first, last):
    """Fetch region-balanced news for the entire target month from Google/Bing RSS."""
    start_dt = datetime.combine(first, datetime.min.time(), TZ)
    end_dt = datetime.combine(last + timedelta(days=1), datetime.min.time(), TZ)
    collected, seen = [], set()
    for region, query in REGIONS:
        regional = []
        queries = [query, f"{query} {UNREST}"]
        for search_query in queries:
            encoded = quote_plus(f"({search_query}) after:{first.isoformat()} before:{(last + timedelta(days=1)).isoformat()}")
            feeds = [
                ("Google News RSS", "https://news.google.com/rss/search?q=" + encoded + "&hl=en-US&gl=US&ceid=US:en"),
                ("Bing News RSS", "https://www.bing.com/news/search?q=" + encoded + "&format=rss"),
            ]
            for feed_name, url in feeds:
                try:
                    response = requests.get(url, headers=HEADERS, timeout=18)
                    response.raise_for_status()
                    root = ET.fromstring(response.content)
                    for node in root.findall(".//item"):
                        title = clean(node.findtext("title"))
                        link = clean(node.findtext("link"))
                        description = clean(node.findtext("description"))
                        published_raw = clean(node.findtext("pubDate"))
                        published_dt = parse_date(published_raw)
                        if not title or not link:
                            continue
                        if published_dt and not (start_dt <= published_dt < end_dt):
                            continue
                        key = link.split("&")[0]
                        if key in seen:
                            continue
                        seen.add(key)
                        source_node = node.find("source")
                        source = clean(source_node.text if source_node is not None else "") or feed_name
                        regional.append({
                            "region": region, "title": title, "summary": description[:1000],
                            "url": link, "source": source,
                            "published": published_dt.isoformat(timespec="minutes") if published_dt else published_raw
                        })
                    # A successful provider response is enough for this query; retain fallback for failures.
                    break
                except Exception as exc:
                    print(f"Monthly RSS query failed ({feed_name}; {region}): {type(exc).__name__}: {exc}")
        regional.sort(key=lambda x: x.get("published", ""), reverse=True)
        collected.extend(regional[:MAX_ITEMS_PER_REGION])

    # Add recent stories already collected by the site's news pipeline when their dates
    # fall within the target month. This is a supplemental source, not the only news input.
    existing = load_json(NEWS_FILE, {})
    for item in existing.get("items", []) if isinstance(existing, dict) else []:
        published = parse_date(item.get("published"))
        if not published or not (start_dt <= published < end_dt):
            continue
        url = item.get("url", "")
        key = url.split("&")[0]
        if not url or key in seen:
            continue
        seen.add(key)
        collected.append({
            "region": item.get("region") or item.get("category") or "أخبار دولية",
            "title": clean(item.get("title") or item.get("original_title")),
            "summary": clean(item.get("description") or item.get("original_description"))[:1000],
            "url": url, "source": clean(item.get("source")),
            "published": published.isoformat(timespec="minutes")
        })
    collected.sort(key=lambda x: x.get("published", ""), reverse=True)
    print(f"Monthly news collected: {len(collected)}")
    return collected[:180]


def load_monthly_index():
    return load_json(INDEX, {"updated": "", "years": []})


def add_to_index(card):
    index = load_monthly_index()
    year, month = card["date"][:4], card["date"][5:7]
    y = next((v for v in index.get("years", []) if str(v.get("year")) == year), None)
    if y is None:
        y = {"year": year, "months": []}
        index.setdefault("years", []).append(y)
    m = next((v for v in y.get("months", []) if str(v.get("month")) == month), None)
    if m is None:
        m = {"month": month, "cards": []}
        y.setdefault("months", []).append(m)
    m["cards"] = [x for x in m.get("cards", []) if x.get("path") != card["path"]]
    m["cards"].append({
        "date": card["date"], "title": card["title"], "subject": card["subject"],
        "slot": "monthly", "path": card["path"], "kind": "monthly_assessment",
        "kind_label": "التقدير الاستراتيجي الشهري"
    })
    index["years"] = sorted(index["years"], key=lambda x: str(x["year"]), reverse=True)
    for y0 in index["years"]:
        y0["months"] = sorted(y0.get("months", []), key=lambda x: str(x["month"]), reverse=True)
        for m0 in y0["months"]:
            m0["cards"] = sorted(m0.get("cards", []), key=lambda x: x.get("date", ""), reverse=True)
    index["updated"] = datetime.now(TZ).isoformat(timespec="seconds")
    save_json(INDEX, index)


def build_prompt(first, last, news):
    news_block = "\n".join(
        f"- [{item.get('published')}] [{item.get('region')}] {item.get('title')} — "
        f"{item.get('summary', '')[:650]} (المصدر: {item.get('source')}; الرابط: {item.get('url')})"
        for item in news
    ) or "لم تتوفر نتائج إخبارية قابلة للتحقق للفترة المستهدفة."
    return f"""
أنت رئيس تحرير قسم التقديرات الاستراتيجية في «المرصد الإفريقي للأمن والدفاع».
أنجز تقديراً استراتيجياً شهرياً شاملاً باللغة العربية للفترة {first.isoformat()} إلى {last.isoformat()}.

قاعدة إلزامية: يجب إعداد التقرير وإكمال أقسامه الثمانية حتى إذا كانت المواد الإخبارية المتاحة محدودة. لا تعتبر محدودية الأخبار سبباً للإلغاء أو التأجيل. استند مباشرة إلى الأخبار الإقليمية والدولية المرفقة، واستكمل الفجوات بالبحث الإخباري المتاح لا بالاختلاق. إذا لم تتوفر معطيات كافية في محور أو فترة، اذكر حدود المعلومات بوضوح وقدّم تحليلاً متحفظاً بدلاً من اختراع وقائع. لا تدّعِ أن التغطية كاملة إذا لم تكن كذلك.

هذا ليس تجميعاً زمنياً للتقارير الأسبوعية. أعد تركيب الأحداث عبر الشهر، واستخرج الاتجاهات والتحولات وروابطها الاستراتيجية. ميّز بين الوقائع الواردة في المواد والاستنتاجات التحليلية والسيناريوهات الاحتمالية. لا تكرر الأحداث، ولا تضف أرقاماً أو صفقات أو نوايا غير مسندة إلى المواد. لا تعامل عنوان الخبر وحده كدليل على كل تفاصيله.

يجب أن يتضمن التقدير ثمانية أقسام بالضبط وبالترتيب والعناوين الحرفية التالية:
1. المقدمة
2. تونس وشمال إفريقيا
3. بقية إفريقيا
4. أوروبا والفضاء الأطلسي
5. الشرق الأوسط
6. الولايات المتحدة والقوى الدولية الأخرى
7. الاتجاهات الاستراتيجية العابرة للأقاليم
8. الحوصلة الاستراتيجية والسيناريوهات وانعكاساتها على تونس

توجيهات الأقسام:
- المقدمة: صورة تركيبية للمشهد الاستراتيجي خلال الشهر.
- تونس وشمال إفريقيا: تونس وليبيا والجزائر والمغرب والساحل والمتوسط والحدود والتعاون الدفاعي والهجرة والأمن البحري.
- بقية إفريقيا: النزاعات والتحولات السياسية والأمنية والتنافس الدولي.
- أوروبا والفضاء الأطلسي: الدفاع الأوروبي والناتو وأوكرانيا وروسيا والعلاقات عبر الأطلسي.
- الشرق الأوسط: توازنات القوى والنزاعات والممرات البحرية والطاقة.
- الولايات المتحدة والقوى الدولية الأخرى: خيارات القوى الكبرى وأثرها على النظام الدولي.
- الاتجاهات العابرة للأقاليم: التحالفات والتسلح والتنافس على النفوذ وأمن الطاقة والملاحة.
- الخلاصة: النتائج الأساسية، وسيناريوهان أو ثلاثة للمرحلة التالية مع مؤشرات تحققها، وانعكاساتها المحتملة على الأمن القومي التونسي.

استهدف 110-170 كلمة لكل قسم، مع إعطاء الأولوية للدقة لا للحشو. الملخص التنفيذي 90-140 كلمة. إذا كانت المادة ضعيفة، لا تملأ المساحة بكلام عام أو حقائق غير موثقة؛ بيّن النقص وحدود التقدير. عرّف الاختصارات عند أول ورودها.

أعد JSON فقط بالحقول title وsubject وsummary وsections وcoverage_note. يجب أن تحتوي sections على ثمانية عناصر بالضبط، وكل عنصر heading وtext. اجعل coverage_note تشرح عدد المواد الإخبارية المتاحة وتوزيعها الجغرافي وحدود التغطية الفعلية.

الأخبار المجمعة للفترة ({len(news)} مادة):
{news_block}
"""


def main():
    first = target_month()
    last = month_end(first)
    month_slug = hashlib.sha1(f"{first:%Y-%m}".encode()).hexdigest()[:10]
    month_path = ARCHIVE / f"{first:%Y-%m}/{last:%Y%m%d}_{month_slug}.json"
    if month_path.exists() and os.getenv("FORCE_MONTHLY") != "1":
        print(f"Monthly assessment already exists for {first:%Y-%m}; skipping duplicate scheduled run.")
        return
    news = fetch_month_news(first, last)
    print(f"Monthly inputs: news_items={len(news)}")

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing; cannot generate the analytical monthly report.")
    client = genai.Client(api_key=api_key)
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"}, "subject": {"type": "string"},
            "summary": {"type": "string"}, "coverage_note": {"type": "string"},
            "sections": {"type": "array", "items": {"type": "object",
                "properties": {"heading": {"type": "string"}, "text": {"type": "string"}},
                "required": ["heading", "text"]}}
        },
        "required": ["title", "subject", "summary", "coverage_note", "sections"]
    }
    last_error = None
    output = None
    for attempt in range(1, 4):
        try:
            response = client.models.generate_content(
                model=MODEL,
                contents=build_prompt(first, last, news),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json", response_schema=schema,
                    temperature=0.2, max_output_tokens=14000
                )
            )
            output = json.loads(response.text or "{}")
            break
        except Exception as exc:
            last_error = exc
            print(f"Gemini generation attempt {attempt}/3 failed: {type(exc).__name__}: {exc}")
    if output is None:
        raise RuntimeError(f"Monthly report generation failed after 3 attempts: {last_error}")

    sections = output.get("sections", [])
    if len(sections) != 8 or [clean(x.get("heading")) for x in sections] != EXPECTED:
        raise RuntimeError("Monthly assessment failed the exact eight-section structure check.")
    if any(len(clean(x.get("text")).split()) < 35 for x in sections):
        raise RuntimeError("Monthly section validation failed: one or more sections are empty or too short.")
    summary = clean(output.get("summary"))
    if len(summary.split()) < 45:
        raise RuntimeError("Monthly executive summary is too short.")

    date_tag = last.isoformat()
    slug = hashlib.sha1(f"{first:%Y-%m}".encode()).hexdigest()[:10]
    relative_path = f"{first:%Y-%m}/{last:%Y%m%d}_{slug}.json"
    title = clean(output.get("title")) or f"التقدير الاستراتيجي الشهري — {MONTHS[first.month - 1]} {first.year}"
    source_map = {}
    for item in news:
        url = item.get("url")
        if url and url not in source_map:
            source_map[url] = {"title": item.get("title", url), "url": url}
    coverage = clean(output.get("coverage_note"))
    if not coverage:
        coverage = f"اعتمد التقدير على {len(news)} مادة إخبارية جُمعت للفترة المستهدفة؛ وتُقرأ النتائج في ضوء التوزيع الجغرافي الفعلي للمواد وحدود المصادر المتاحة."
    card = {
        "id": slug, "kind": "monthly_assessment",
        "kind_label": "التقدير الاستراتيجي الشهري",
        "title": title,
        "subject": clean(output.get("subject")) or "التقدير الاستراتيجي الشهري للتطورات الأمنية والدفاعية والجيوسياسية",
        "date": date_tag, "period_start": first.isoformat(), "period_end": last.isoformat(),
        "region": "تقدير استراتيجي شامل — تونس وشمال إفريقيا وإفريقيا والعالم",
        "summary": summary, "coverage_note": coverage,
        "sections": sections,
        "sources": list(source_map.values())[:100], "news_items_count": len(news),
        "path": relative_path
    }
    save_json(ARCHIVE / relative_path, card)
    add_to_index(card)
    print(f"Published monthly assessment: {title}; news={len(news)}; sources={len(card['sources'])}; path={relative_path}")


if __name__ == "__main__":
    main()
