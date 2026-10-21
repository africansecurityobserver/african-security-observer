import json
import os
import re
import hashlib
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
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
ARCHIVE = ROOT / "weekly_assessments" / "archive"
INDEX = ARCHIVE / "index.json"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AfricanSecurityObserver/1.0)"}
WINDOW_DAYS = 8
MAX_ITEMS = 55
REGIONS = [
    ("تونس وشمال إفريقيا", '"Tunisia" OR "Libya" OR "Algeria" OR "Morocco" OR "North Africa" security military defense'),
    ("بقية دول إفريقيا", 'Africa OR Sahel OR Sudan OR Somalia OR Ethiopia OR Congo military security conflict'),
    ("أوروبا", 'Europe OR NATO OR Ukraine OR Russia defense military security alliance'),
    ("الشرق الأوسط", '"Middle East" OR Israel OR Iran OR Gulf OR Syria OR Iraq security military conflict'),
    ("الولايات المتحدة", '"United States" OR Pentagon OR US military defense policy national security'),
    ("بقية مناطق العالم", '"Indo-Pacific" OR China OR Taiwan OR Japan OR Korea OR India OR Pakistan security military'),
    ("الأمن والتحالفات الدولية", 'arms control OR defense industry OR military technology OR strategic alliance OR UN security')
]

def clean(value):
    value = re.sub(r"<[^>]+>", " ", str(value or ""))
    return re.sub(r"\s+", " ", value).strip()

def parse_date(value):
    if not value:
        return None
    try:
        d = parsedate_to_datetime(value)
        if d.tzinfo is None:
            d = d.replace(tzinfo=TZ)
        return d.astimezone(TZ)
    except Exception:
        return None

def fetch_items():
    cutoff = datetime.now(TZ) - timedelta(days=WINDOW_DAYS)
    items, seen = [], set()
    for region, query in REGIONS:
        url = "https://news.google.com/rss/search?q=" + quote_plus(query + " when:8d") + "&hl=en-US&gl=US&ceid=US:en"
        try:
            response = requests.get(url, headers=HEADERS, timeout=18)
            response.raise_for_status()
            root = ET.fromstring(response.content)
            for node in root.findall(".//item"):
                title = clean(node.findtext("title"))
                link = clean(node.findtext("link"))
                description = clean(node.findtext("description"))
                source_node = node.find("source")
                source = clean(source_node.text if source_node is not None else "")
                published = clean(node.findtext("pubDate"))
                dt = parse_date(published)
                if not title or not link or (dt and dt < cutoff):
                    continue
                key = link.split("&")[0]
                if key in seen:
                    continue
                seen.add(key)
                items.append({"region": region, "title": title, "summary": description[:900],
                              "url": link, "source": source or "Google News RSS",
                              "published": dt.isoformat(timespec="minutes") if dt else published})
        except Exception as exc:
            print(f"RSS query failed ({region}): {type(exc).__name__}: {exc}")
    items.sort(key=lambda x: x["published"], reverse=True)
    return items[:MAX_ITEMS]

def load_index():
    try:
        data = json.loads(INDEX.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("years"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"updated": "", "years": []}

def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def add_to_index(card):
    index = load_index()
    year, month = card["date"][:4], card["date"][5:7]
    y = next((v for v in index.get("years", []) if str(v.get("year")) == year), None)
    if y is None:
        y = {"year": year, "months": []}
        index.setdefault("years", []).append(y)
    m = next((v for v in y.get("months", []) if str(v.get("month")) == month), None)
    if m is None:
        m = {"month": month, "cards": []}
        y.setdefault("months", []).append(m)
    m["cards"] = [c for c in m.get("cards", []) if c.get("path") != card["path"]]
    m["cards"].append({"date": card["date"], "title": card["title"], "subject": card["subject"],
                       "slot": "weekly", "path": card["path"], "kind": "weekly_assessment",
                       "kind_label": "التقدير الاستراتيجي الأسبوعي"})
    index["years"] = sorted(index["years"], key=lambda x: str(x["year"]), reverse=True)
    for y0 in index["years"]:
        y0["months"] = sorted(y0.get("months", []), key=lambda x: str(x["month"]), reverse=True)
        for m0 in y0["months"]:
            m0["cards"] = sorted(m0.get("cards", []), key=lambda x: (x.get("date", ""), x.get("title", "")), reverse=True)
    index["updated"] = datetime.now(TZ).isoformat(timespec="seconds")
    save_json(INDEX, index)

def main():
    now = datetime.now(TZ)
    week_end = now.date()
    week_start = (now - timedelta(days=7)).date()
    date_tag = week_end.strftime("%Y-%m-%d")
    slug = hashlib.sha1(date_tag.encode()).hexdigest()[:10]
    relative_path = f"{date_tag[:7]}/{date_tag.replace('-', '')}_{slug}.json"
    card_path = ARCHIVE / relative_path
    if card_path.exists() and os.getenv("FORCE_REGENERATE", "0") != "1":
        print(f"Weekly assessment already exists for {date_tag}; preserving it.")
        return
    items = fetch_items()
    regions_present = {item["region"] for item in items}
    if len(items) < 12 or len(regions_present) < 5:
        raise RuntimeError(f"Insufficient current source material: {len(items)} items across {len(regions_present)} regions; refusing to publish a weak assessment.")
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing; no assessment was published.")
    client = genai.Client(api_key=api_key)
    source_block = "\n".join(
        f"[{i+1}] المنطقة: {x['region']} | العنوان: {x['title']} | المصدر: {x['source']} | التاريخ: {x['published']} | الملخص: {x['summary']} | الرابط: {x['url']}"
        for i, x in enumerate(items))
    schema = {"type": "object", "properties": {
        "title": {"type": "string"}, "subject": {"type": "string"}, "summary": {"type": "string"},
        "sections": {"type": "array", "items": {"type": "object", "properties": {
            "heading": {"type": "string"}, "text": {"type": "string"}},
            "required": ["heading", "text"]}},
    }, "required": ["title", "subject", "summary", "sections"]}
    prompt = f"""
أنت رئيس تحرير قسم «التقديرات الاستراتيجية» في المرصد الإفريقي للأمن والدفاع. أعد تقديراً استراتيجياً عالمياً أسبوعياً موسعاً باللغة العربية، لا ملخص أخبار ولا تجميع منشورات مراكز أبحاث.
الفترة: من {week_start.isoformat()} إلى {week_end.isoformat()} بتوقيت تونس.
ثمانية أقسام بالضبط وبالترتيب: المقدمة؛ تونس وشمال إفريقيا؛ بقية دول إفريقيا؛ أوروبا؛ الشرق الأوسط؛ الولايات المتحدة؛ بقية مناطق العالم؛ الخلاصة الاستراتيجية.
كل قسم 180-300 كلمة تقريباً، المقدمة 130-200 كلمة، والخلاصة 220-320 كلمة. لا تحشو النص إذا كانت الأدلة محدودة؛ اذكر حدود المعلومات.
في كل قسم، اربط الوقائع المؤكدة بدلالتها الأمنية/العسكرية والاستراتيجية، ثم الاتجاهات المحتملة ومؤشرات المتابعة. ميّز بين الواقعة والتحليل والاحتمال. لا تعرض التوقعات كحقائق، ولا تستنتج نوايا غير مثبتة، ولا تخترع أرقاماً أو أحداثاً أو صفقات.
اجعل تونس وشمال إفريقيا محورا مهما: الآثار على تونس، الحدود، ليبيا والجزائر والمغرب، الساحل، المتوسط، التعاون الدفاعي والهجرة والأمن البحري عند الصلة. لا تفرض موضوع تونس على أقسام غير مناسبة.
استخدم أرقام المصادر بين معقوفين مثل [3] عند إسناد الوقائع. الأرقام تشير إلى قائمة المصادر التي ستضاف لاحقاً. اعتمد فقط على المواد أدناه، ولا تدّع الاطلاع على نصوص كاملة إذا كانت مجرد عناوين ومقتطفات.
اكتب عربية صحفية رصينة وتحليلاً مترابطاً، وعرّف الاختصارات عند أول ورودها، وتجنب التكرار.
أعد JSON بالحقول title وsubject وsummary (100-150 كلمة) وsections (ثمانية عناصر بالضبط، كل عنصر heading وtext وبالعناوين المحددة حرفياً).
المواد المجمعة خلال الفترة:
{source_block}
"""
    response = client.models.generate_content(model=MODEL, contents=prompt,
        config=types.GenerateContentConfig(response_mime_type="application/json",
        response_schema=schema, temperature=0.2, max_output_tokens=14000))
    output = json.loads(response.text or "{}")
    sections = output.get("sections", [])
    expected = ["المقدمة", "تونس وشمال إفريقيا", "بقية دول إفريقيا", "أوروبا", "الشرق الأوسط", "الولايات المتحدة", "بقية مناطق العالم", "الخلاصة الاستراتيجية"]
    if len(sections) != 8 or [clean(x.get("heading")) for x in sections] != expected:
        raise RuntimeError("Model output failed the exact eight-section structure check.")
    if any(len(clean(s.get("text"))) < 450 for s in sections):
        raise RuntimeError("At least one section is too short; refusing to publish an incomplete assessment.")
    title = clean(output.get("title")) or "التقدير الاستراتيجي الأسبوعي"
    card = {"id": slug, "kind": "weekly_assessment", "kind_label": "التقدير الاستراتيجي الأسبوعي",
        "title": title, "subject": clean(output.get("subject")) or title, "date": date_tag,
        "period_start": week_start.isoformat(), "period_end": week_end.isoformat(),
        "region": "تقدير استراتيجي عالمي — تونس وشمال إفريقيا والعالم",
        "summary": clean(output.get("summary")), "sections": sections,
        "sources": [{"title": f"[{i+1}] {x['source']} — {x['title']} ({x['published']})", "url": x["url"]}
                    for i, x in enumerate(items)], "path": relative_path}
    if len(card["summary"]) < 250:
        raise RuntimeError("Summary too short; refusing to publish.")
    save_json(card_path, card)
    add_to_index(card)
    print(f"Published weekly assessment: {title}; sections={len(sections)}; sources={len(items)}; path={relative_path}")

if __name__ == "__main__":
    main()
