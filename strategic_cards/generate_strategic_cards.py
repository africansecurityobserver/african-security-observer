import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from selectolax.parser import HTMLParser
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
NEWS_FILE = ROOT / "news.json"
DATA_FILE = ROOT / "strategic_cards.json"
TZ = ZoneInfo("Africa/Tunis")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
MAX_CANDIDATES = 5

KEYWORDS = [
    "صفقة", "تسلح", "أسلحة", "سلاح", "مقاتلة", "طائرة حربية", "دبابة",
    "غواصة", "سفينة حربية", "فرقاطة", "مدرعة", "صاروخ", "دفاع جوي",
    "رادار", "منظومة", "ابتكار عسكري", "صناعة دفاعية", "ذخائر",
    "عقد دفاعي", "شراء أسلحة", "تصدير أسلحة", "تسليم معدات",
    "military deal", "arms deal", "defense contract", "defence contract",
    "weapons", "fighter jet", "warship", "submarine", "frigate", "tank",
    "air defense", "air-defence", "missile system", "radar system",
    "military technology", "defense industry", "defence industry",
    "armored vehicle", "drones", "unmanned aircraft", "procurement",
    "livraison d'armes", "contrat de défense", "contrat de defense",
    "avion de combat", "navire de guerre", "système de défense", "système d'armes"
]

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def save_json(path, value):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)

def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()

def parse_date(value):
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ)
    except Exception:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(TZ)
        except Exception:
            return None

def get_article_text(url):
    if not url or not url.startswith(("http://", "https://")):
        return ""
    try:
        response = requests.get(
            url,
            timeout=12,
            headers={"User-Agent": "Mozilla/5.0 (compatible; AfricanSecurityObserver/1.0)"}
        )
        if response.status_code != 200:
            return ""
        parser = HTMLParser(response.text)
        for selector in ("script", "style", "nav", "header", "footer", "aside", "form"):
            for node in parser.css(selector):
                node.decompose()
        article = parser.css_first("article") or parser.css_first("main") or parser.body
        if not article:
            return ""
        return clean_text(article.text(separator=" ", strip=True))[:9000]
    except Exception as exc:
        print(f"Source extraction failed for {url}: {type(exc).__name__}")
        return ""

def candidate_score(item):
    title = clean_text(item.get("original_title") or item.get("title"))
    desc = clean_text(item.get("original_description") or item.get("description"))
    value = (title + " " + desc).lower()
    score = sum(2 if term.lower() in title.lower() else 1 for term in KEYWORDS if term.lower() in value)
    if item.get("category") == "defense":
        score += 5
    if not item.get("url"):
        score = 0
    return score

def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing. Reuse the existing GitHub Actions secret used by daily cards.")

    news = load_json(NEWS_FILE, {})
    items = news.get("items", []) if isinstance(news, dict) else []
    data = load_json(DATA_FILE, {"deals": [], "systems": [], "checked_urls": []})
    if not isinstance(data, dict):
        data = {"deals": [], "systems": [], "checked_urls": []}
    data.setdefault("deals", [])
    data.setdefault("systems", [])
    data.setdefault("checked_urls", [])
    checked = set(data["checked_urls"])

    cutoff = datetime.now(TZ) - timedelta(days=7)
    candidates = []
    seen = set()
    for item in items:
        url = clean_text(item.get("url"))
        if not url or url in checked or url in seen:
            continue
        published = parse_date(item.get("published"))
        if published and published < cutoff:
            continue
        score = candidate_score(item)
        if score < 2:
            continue
        seen.add(url)
        item["_score"] = score
        item["_url"] = url
        candidates.append(item)

    candidates.sort(
        key=lambda x: (x.get("_score", 0), parse_date(x.get("published")) or datetime.min.replace(tzinfo=TZ)),
        reverse=True
    )
    candidates = candidates[:MAX_CANDIDATES]
    if not candidates:
        print("No new suitable arms or military-technology items. Nothing published.")
        return

    source_material = []
    for index, item in enumerate(candidates, start=1):
        title = clean_text(item.get("original_title") or item.get("title"))
        desc = clean_text(item.get("original_description") or item.get("description"))
        body = get_article_text(item["_url"])
        source_material.append({
            "id": index,
            "title": title[:400],
            "description": desc[:1800],
            "published": clean_text(item.get("published")),
            "source": clean_text(item.get("source")),
            "url": item["_url"],
            "article_text": body
        })

    prompt = f"""
أنت محرر متخصص في التسلح والصناعات الدفاعية لدى «المرصد الإفريقي للأمن والدفاع».
أنشئ بطاقة استراتيجية واحدة فقط من المواد المرفقة، أو قرر عدم النشر إذا لم تتوفر مادة حديثة وذات قيمة ومعلومات كافية.

التصنيفات المسموحة:
- deal: صفقة تسلح كبرى، عقد، تسليم فعلي، اتفاق تصدير أو توطين صناعي محدد.
- system: منظومة أو ابتكار عسكري أو معدة أو تقنية دفاعية جديدة.

قواعد إلزامية:
1. اعتمد فقط على المواد المصدرية المرفقة. لا تستخدم معرفة خارجية لسد النقص.
2. لا تنشر إذا كانت المادة مجرد تعليق عام أو إعادة تدوير خبر قديم أو لا تحتوي تفاصيل كافية لبناء بطاقة مفيدة.
3. لا تخترع الأسعار أو الأعداد أو المديات أو المواصفات أو الدول المشاركة أو التواريخ أو حالة التسليم.
4. ميّز بدقة بين إعلان نية الشراء، وإبرام العقد، وبدء التصنيع، والتسليم الفعلي.
5. ميّز بين المواصفات التي تعلنها الشركة المصنعة والأداء المثبت ميدانياً. لا تصف قدرة معلنة بأنها مثبتة عملياً دون دليل.
6. اكتب بالعربية الفصحى بأسلوب رسمي تحليلي، على غرار بطاقات المرصد: مقدمة مرتبطة بالحدث، معلومات تقنية أو تعاقدية، دلالات صناعية واستراتيجية، تقييم عملياتي، ثم انعكاسات محتملة على تونس عند وجود أساس حقيقي لذلك.
7. اجعل الأقسام متخصصة في موضوع البطاقة، لا تستخدم قالباً واحداً جامداً. لصفقة تسلح ركّز على الأطراف والمعدات والقيمة والجدول الزمني إذا كانت موثقة، والأهداف والدلالات. لمنظومة ركّز على تعريفها ومكوناتها ومواصفاتها ومهامها والقيود المعروفة.
8. لا تفرض فقرة تونسية إذا لم تكن ذات قيمة؛ ولا تقدم توصية اقتناء مباشرة دون معلومات كافية.
9. استخدم من 4 إلى 7 أقسام قصيرة. لا تكرر المعلومات، ولا تكتب حشواً.
10. أدرج في المصادر روابط أصلية مأخوذة حصراً من المواد المرفقة. لا تخترع روابط أو أسماء مصادر.
11. إذا لم يكن هناك حدث مناسب أو لا تكفي البيانات، أعد publish=false.
12. لا تكتب أي نص خارج JSON.

تاريخ اليوم في تونس: {datetime.now(TZ).strftime("%Y-%m-%d")}

المواد:
{json.dumps(source_material, ensure_ascii=False)}
"""

    schema = {
        "type": "object",
        "properties": {
            "publish": {"type": "boolean"},
            "type": {"type": "string", "enum": ["deal", "system"]},
            "title": {"type": "string"},
            "region": {"type": "string"},
            "summary": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "heading": {"type": "string"},
                        "text": {"type": "string"}
                    },
                    "required": ["heading", "text"]
                }
            },
            "source_urls": {"type": "array", "items": {"type": "string"}}
        },
        "required": ["publish", "type", "title", "region", "summary", "sections", "source_urls"]
    }

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=180000)
    )
    result = client.interactions.create(
        model=MODEL,
        input=prompt,
        response_format={"type": "text", "mime_type": "application/json", "schema": schema},
        generation_config={"temperature": 0.2, "thinking_level": "low"}
    )
    output = json.loads(result.output_text or "{}")

    # Mark considered stories so the daily workflow does not repeatedly spend quota on them.
    data["checked_urls"] = list(dict.fromkeys(data["checked_urls"] + [x["_url"] for x in candidates]))[-1000:]

    if not output.get("publish"):
        save_json(DATA_FILE, data)
        print("Model found no sufficiently substantiated strategic card. No publication.")
        return

    valid_urls = {item["url"] for item in source_material}
    chosen_urls = [url for url in output.get("source_urls", []) if url in valid_urls]
    if not chosen_urls or not clean_text(output.get("title")) or len(output.get("sections", [])) < 3:
        save_json(DATA_FILE, data)
        print("Generated result failed validation. No publication.")
        return

    source_by_url = {item["url"]: item for item in source_material}
    sources = [
        {"title": source_by_url[url]["source"] or source_by_url[url]["title"], "url": url}
        for url in dict.fromkeys(chosen_urls)
    ]
    kind = "deals" if output.get("type") == "deal" else "systems"
    card = {
        "id": datetime.now(TZ).strftime("%Y%m%d%H%M%S"),
        "title": clean_text(output.get("title")),
        "date": datetime.now(TZ).strftime("%Y-%m-%d"),
        "region": clean_text(output.get("region")),
        "summary": clean_text(output.get("summary")),
        "sections": [
            {"heading": clean_text(section.get("heading")), "text": clean_text(section.get("text"))}
            for section in output.get("sections", [])
            if clean_text(section.get("heading")) and clean_text(section.get("text"))
        ],
        "sources": sources
    }

    existing_titles = {
        clean_text(card_item.get("title")).lower()
        for card_item in data[kind]
        if isinstance(card_item, dict)
    }
    if card["title"].lower() in existing_titles:
        save_json(DATA_FILE, data)
        print("A card with the same title already exists. Skipping duplicate.")
        return

    data[kind].append(card)
    data[kind] = data[kind][-150:]
    save_json(DATA_FILE, data)
    print(f"Published strategic card: {card['title']} ({kind})")

if __name__ == "__main__":
    main()
