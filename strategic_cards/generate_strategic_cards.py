import json
import os
import re
import xml.etree.ElementTree as ET
from html import unescape
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
ARCHIVE_DIR = ROOT / "strategic_cards" / "archive"
TZ = ZoneInfo("Africa/Tunis")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
MAX_CANDIDATES = 12
MAX_CARD_AGE_DAYS = 7
MAX_ARCHIVE_CARDS_PER_TYPE = 150

# Supplemental searches widen strategic coverage beyond the existing daily-news
# feed, especially for diplomacy, defense policy, logistics, and defense economics.
STRATEGIC_RSS_QUERIES = {
    "defense_economics": [
        '"defense economics" OR "defence economics" OR "critical minerals" OR "اقتصاد الدفاع" OR "المعادن الحيوية" Africa security'
    ],
    "military_transformation": [
        '"military restructuring" OR "autonomous warfare" OR "military AI" OR "إعادة هيكلة الجيش" OR "الذكاء الاصطناعي العسكري"'
    ],
    "defense_strategy": [
        '"military mobility" OR "defense readiness" OR "military logistics" OR "الجاهزية الدفاعية" OR "النقل العسكري" Europe NATO'
    ],
    "regional_crisis": [
        '"diplomatic crisis" OR "sever diplomatic relations" OR "crise diplomatique" OR "قطع العلاقات الدبلوماسية" Algeria UAE Libya Sudan Sahel'
    ],
    "strategic_assessment": [
        '"geopolitical competition" OR "regional security" OR "national security strategy" OR "security strategy" OR "defense policy" OR "defence policy" OR "توازنات إقليمية" OR "الأمن الإقليمي" OR "استراتيجية الأمن القومي" OR "السياسة الدفاعية" Africa Sahel North Africa'
    ]
}

TYPE_LABELS = {
    "deals": "صفقات التسلح الكبرى",
    "systems": "المنظومات والابتكارات العسكرية",
    "defense_economics": "الأمن الاقتصادي والجيو-اقتصادي",
    "military_transformation": "التحولات العسكرية والتنظيمية والتكنولوجية",
    "defense_strategy": "الاستراتيجيات والتحالفات والجاهزية الدفاعية",
    "regional_crisis": "الأزمات الإقليمية والتقييمات الجيوسياسية",
    "strategic_assessment": "التقديرات الاستراتيجية والأمنية"
}
TYPE_ORDER = list(TYPE_LABELS.keys())

TYPE_KEYWORDS = {
    "deals": [
        "صفقة تسلح", "صفقات أسلحة", "عقد دفاعي", "شراء أسلحة", "تصدير أسلحة",
        "تسليم معدات", "مقاتلة", "طائرة حربية", "دبابة", "غواصة", "فرقاطة",
        "منظومة دفاع جوي", "صاروخ", "ذخائر", "military deal", "arms deal",
        "defense contract", "defence contract", "weapons procurement", "fighter jet",
        "warship", "submarine", "frigate", "tank", "air defense", "missile system",
        "livraison d'armes", "contrat de défense", "avion de combat", "navire de guerre"
    ],
    "systems": [
        "منظومة عسكرية", "تقنية عسكرية", "ابتكار عسكري", "طائرة دون طيار",
        "أنظمة غير مأهولة", "روبوت عسكري", "ذكاء اصطناعي عسكري", "رادار",
        "military technology", "unmanned", "drone system", "autonomous warfare",
        "artificial intelligence", "robotic system", "radar system", "electronic warfare",
        "technologie militaire", "système autonome"
    ],
    "defense_economics": [
        "اقتصاد الدفاع", "الاقتصاد الدفاعي", "تمويل الدفاع", "تمويل عسكري",
        "الصناعات الدفاعية", "القاعدة الصناعية الدفاعية", "المعادن الحيوية",
        "سلاسل الإمداد", "تمويل التنمية", "النفوذ الاقتصادي", "الاستثمار الدفاعي",
        "defense economics", "defence economics", "defense industry", "defence industry",
        "defense financing", "military financing", "critical minerals", "supply chains",
        "economic statecraft", "defense production", "defence production",
        "financement de la défense", "industrie de défense"
    ],
    "military_transformation": [
        "إعادة هيكلة الجيش", "إعادة هيكلة القوات المسلحة", "قيادة عسكرية جديدة",
        "القيادات العسكرية", "القيادة والسيطرة", "الحرب الذاتية", "الأنظمة الذاتية",
        "الذكاء الاصطناعي", "التصنيع العسكري", "الإنتاج العسكري", "التحول العسكري",
        "military restructuring", "military command", "autonomous warfare",
        "artificial intelligence", "military reform", "force structure", "command and control",
        "military transformation", "defense reform", "defence reform",
        "réforme militaire", "restructuration militaire"
    ],
    "defense_strategy": [
        "استراتيجية دفاعية", "استراتيجية الأمن القومي", "الجاهزية الدفاعية",
        "التنقل العسكري", "النقل العسكري", "الإمداد اللوجستي", "التحالف العسكري",
        "تحالف دفاعي", "الردع", "الدفاع الجماعي", "ميزانية الدفاع", "الإنفاق الدفاعي",
        "defense strategy", "defence strategy", "national security strategy",
        "military mobility", "military logistics", "defense readiness", "defence readiness",
        "military alliance", "defense alliance", "collective defense", "defence spending",
        "budget militaire", "mobilité militaire", "alliance de défense"
    ],
    "regional_crisis": [
        "قطع العلاقات الدبلوماسية", "أزمة دبلوماسية", "أزمة إقليمية", "تصاعد التوتر",
        "نزاع إقليمي", "الحرب في", "وقف إطلاق النار", "محادثات سلام", "مفاوضات",
        "انقلاب عسكري", "صراع مسلح", "تدخل خارجي", "عقوبات", "السودان", "ليبيا",
        "الساحل والصحراء", "الصحراء الغربية", "قطع العلاقات", "تدهور العلاقات",
        "diplomatic crisis", "regional crisis", "armed conflict", "ceasefire",
        "peace talks", "military coup", "diplomatic relations", "regional tensions",
        "conflit régional", "crise diplomatique", "cessez-le-feu"
    ],
    "strategic_assessment": [
        "توازنات إقليمية", "توازن القوى", "تنافس استراتيجي", "تحولات جيوسياسية",
        "الأمن الإقليمي", "الأمن القومي", "السياسة الخارجية", "المصالح الاستراتيجية",
        "نفوذ إقليمي", "شراكة استراتيجية", "تداعيات أمنية", "موقف دولي",
        "geopolitical", "strategic competition", "regional security", "national security",
        "foreign policy", "strategic interests", "regional influence", "security implications",
        "geopolitique", "sécurité régionale", "politique étrangère"
    ]
}

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, json.JSONDecodeError):
        return default

def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)

def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()

def parse_date(value):
    if not value:
        return None
    try:
        from email.utils import parsedate_to_datetime
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(TZ)
    except Exception:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(TZ)
        except Exception:
            return None

def get_article_text(url):
    if not url or not url.startswith(("http://", "https://")):
        return ""
    try:
        response = requests.get(
            url,
            timeout=15,
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
        return clean_text(article.text(separator=" ", strip=True))[:6500]
    except Exception as exc:
        print(f"Source extraction failed for {url}: {type(exc).__name__}")
        return ""

def fetch_strategic_rss_items():
    """Fetch a small, recent, multilingual source pool for strategic topics."""
    collected = []
    seen = set()
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AfricanSecurityObserver/1.0)"}
    for topic, queries in STRATEGIC_RSS_QUERIES.items():
        for query in queries:
            try:
                response = requests.get(
                    "https://news.google.com/rss/search",
                    params={"q": query + " when:7d", "hl": "en-US", "gl": "US", "ceid": "US:en"},
                    timeout=18,
                    headers=headers
                )
                if response.status_code != 200:
                    print(f"Strategic RSS unavailable for {topic}: HTTP {response.status_code}")
                    continue
                root = ET.fromstring(response.content)
                entries = root.findall("./channel/item")
                for entry in entries[:4]:
                    title = clean_text(unescape(re.sub(r"<[^>]+>", " ", entry.findtext("title") or "")))
                    wrapper_url = clean_text(entry.findtext("link") or "")
                    description = clean_text(unescape(re.sub(
                        r"<[^>]+>", " ", entry.findtext("description") or ""
                    )))
                    published = clean_text(entry.findtext("pubDate") or "")
                    source_node = entry.find("source")
                    source = clean_text(source_node.text if source_node is not None else "")
                    if not title or not wrapper_url:
                        continue

                    # Follow Google's redirect so published source links point to
                    # the original publisher whenever the redirect is available.
                    article_url = wrapper_url
                    try:
                        redirected = requests.get(
                            wrapper_url, timeout=8, headers=headers,
                            allow_redirects=True, stream=True
                        )
                        if redirected.url and redirected.url.startswith(("http://", "https://")):
                            article_url = redirected.url
                        redirected.close()
                    except Exception:
                        pass

                    if article_url in seen:
                        continue
                    seen.add(article_url)
                    collected.append({
                        "title": title,
                        "original_title": title,
                        "description": description,
                        "original_description": description,
                        "published": published,
                        "source": source,
                        "category": "strategic",
                        "_strategic_topic": topic,
                        "url": article_url
                    })
            except Exception as exc:
                print(f"Strategic RSS search failed for {topic}: {type(exc).__name__}")
    print(f"Collected {len(collected)} supplemental strategic RSS items.")
    return collected

def score_types(item):
    title = clean_text(item.get("original_title") or item.get("title"))
    description = clean_text(
        item.get("original_description") or item.get("description") or item.get("summary")
    )
    title_lower = title.lower()
    combined = (title + " " + description).lower()
    scores = {}
    for kind, keywords in TYPE_KEYWORDS.items():
        score = 0
        for keyword in keywords:
            term = keyword.lower()
            if term in title_lower:
                score += 3
            elif term in combined:
                score += 1
        scores[kind] = score
    if item.get("category") == "defense":
        for kind in ("deals", "systems", "military_transformation", "defense_strategy"):
            scores[kind] += 2
        for kind in ("defense_economics", "regional_crisis", "strategic_assessment"):
            scores[kind] += 1
    return scores

def candidate_score(item):
    scores = score_types(item)
    return max(scores.values(), default=0), scores

def select_candidates(items, checked):
    now = datetime.now(TZ)
    cutoff = now - timedelta(days=MAX_CARD_AGE_DAYS)
    candidates = []
    seen_urls = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = clean_text(item.get("url"))
        if not url or not url.startswith(("http://", "https://")):
            continue
        if url in checked or url in seen_urls:
            continue
        published = parse_date(item.get("published"))
        if published and published < cutoff:
            continue
        score, scores = candidate_score(item)
        if score < 2:
            continue
        seen_urls.add(url)
        copy = dict(item)
        copy["_score"] = score
        copy["_type_scores"] = scores
        copy["_url"] = url
        candidates.append(copy)

    # Keep a mix of strategic topics instead of letting frequent arms stories
    # crowd every other subject out of the model's source packet.
    chosen = []
    chosen_urls = set()
    pools = {}
    for kind in TYPE_ORDER:
        pools[kind] = sorted(
            [item for item in candidates if item["_type_scores"].get(kind, 0) >= 2],
            key=lambda item: (
                item["_type_scores"].get(kind, 0),
                parse_date(item.get("published")) or datetime.min.replace(tzinfo=TZ)
            ),
            reverse=True
        )
    # First pass takes one candidate per available topic; second pass adds a
    # second candidate per topic, keeping the source packet balanced.
    for round_index in range(2):
        for kind in TYPE_ORDER:
            pool = pools[kind]
            if len(pool) <= round_index:
                continue
            item = pool[round_index]
            if item["_url"] not in chosen_urls:
                chosen.append(item)
                chosen_urls.add(item["_url"])
    for item in sorted(
        candidates,
        key=lambda item: (
            item["_score"],
            parse_date(item.get("published")) or datetime.min.replace(tzinfo=TZ)
        ),
        reverse=True
    ):
        if len(chosen) >= MAX_CANDIDATES:
            break
        if item["_url"] not in chosen_urls:
            chosen.append(item)
            chosen_urls.add(item["_url"])
    return chosen[:MAX_CANDIDATES]

def sync_archive(data):
    """Create the year/month index for all strategic-card categories."""
    years = {}
    for kind in TYPE_ORDER:
        cards = data.get(kind, [])
        for card in cards:
            if not isinstance(card, dict):
                continue
            date_value = clean_text(card.get("date"))[:10]
            try:
                year, month, _day = date_value.split("-")
            except ValueError:
                continue
            card_id = clean_text(card.get("id")) or date_value
            relative_path = f"{year}-{month}/{card_id}.json"
            target = ARCHIVE_DIR / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            archived = dict(card)
            archived["kind"] = kind
            archived["kind_label"] = TYPE_LABELS.get(kind, TYPE_LABELS["strategic_assessment"])
            save_json(target, archived)
            years.setdefault(year, {"year": year, "months": {}})
            years[year]["months"].setdefault(month, {"month": month, "cards": []})
            years[year]["months"][month]["cards"].append({
                "date": date_value,
                "title": clean_text(card.get("title")),
                "path": relative_path,
                "kind": kind,
                "kind_label": TYPE_LABELS.get(kind, TYPE_LABELS["strategic_assessment"])
            })

    output_years = []
    for year in sorted(years, reverse=True):
        months = []
        for month in sorted(years[year]["months"], reverse=True):
            cards = years[year]["months"][month]["cards"]
            cards.sort(key=lambda item: (item.get("date", ""), item.get("title", "")), reverse=True)
            months.append({"month": month, "cards": cards})
        output_years.append({"year": year, "months": months})
    save_json(ARCHIVE_DIR / "index.json", {
        "updated": datetime.now(TZ).isoformat(),
        "years": output_years
    })

def build_prompt(source_material):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    return f"""
أنت رئيس تحرير ومحلل استراتيجي في «المرصد الإفريقي للأمن والدفاع». المطلوب إعداد بطاقة استراتيجية عربية عميقة ومقنعة اعتمادًا على المواد المصدرية المرفقة وحدها. هذه ليست بطاقة خبرية قصيرة، بل تقدير موقف منظم يشرح الحدث وسياقه ودلالاته وتداعياته.

اختر تصنيفًا واحدًا فقط من التصنيفات التالية، بحسب جوهر الموضوع. ويشمل strategic_assessment أيضًا استراتيجيات الأمن القومي والسياسة الخارجية عندما لا ينطبق تصنيف أدق:
- deals: صفقات التسلح الكبرى والعقود والتسليمات والتعاون الصناعي المحدد.
- systems: المنظومات والابتكارات العسكرية والتقنيات والمعدات.
- defense_economics: اقتصاد الدفاع، التمويل، الاستثمارات، المعادن الحيوية، سلاسل الإمداد، أدوات النفوذ الاقتصادي.
- military_transformation: إعادة هيكلة القوات المسلحة، القيادات، العقيدة، الذكاء الاصطناعي، الأنظمة الذاتية، والتحولات التنظيمية والتكنولوجية.
- defense_strategy: الاستراتيجيات الدفاعية، التحالفات، الجاهزية، النقل والإمداد العسكري، الإنفاق الدفاعي، البنية التحتية والردع.
- regional_crisis: الأزمات الثنائية والإقليمية، النزاعات، تدهور العلاقات، تضارب المصالح، وآثارها السياسية والاقتصادية والأمنية.
- strategic_assessment: التقديرات الجيوسياسية والأمنية التي لا ينطبق عليها تصنيف أدق.

منهج البناء:
1. ابدأ بمقدمة رسمية تحدد الحدث أو التطور والجهة والتاريخ والمكان عند توافرها، وتوضح سبب أهميته.
2. أنشئ من 4 إلى 7 أقسام تحليلية بعناوين دقيقة متخصصة بالموضوع. لا تستخدم عناوين ثابتة لا تناسب المادة.
3. فسّر الخلفية والدوافع والمصالح ذات الصلة، ثم حلل مضمون القرار أو التطور وآليات تنفيذه، ثم اربطه بمؤشرات أوسع أو خطوات عملية مرتبطة به.
4. قيّم التداعيات المحتملة والقيود والمخاطر والسيناريوهات عند وجود أساس مصدري. في الأزمات، حلل مواقف الأطراف وأسباب الخلاف وآثاره السياسية والاقتصادية والأمنية، ثم الخيارات الممكنة لتونس ومزاياها ومخاطرها وحدودها.
5. اختم بخلاصة استراتيجية مركزة. بعدها أضف فقرة قصيرة من 4 إلى 5 أسطر عن دلالات الموضوع لتونس عندما تكون ذات صلة حقيقية. لا تجعل «تونس» عنوان قسم مستقلًا، ولا تفرض توصية عامة أو وساطة أو حيادًا إيجابيًا دون تحليل.
6. اجعل البطاقة غنية بالمعلومات لكنها غير مكررة. قدّم علاقات السبب والنتيجة بوضوح، وميّز بين ما هو معلن وما هو تحليل أو توقع.
7. استخدم العربية الفصحى الرسمية، وصحح أسماء المؤسسات والرتب والبلدان قدر الإمكان استنادًا إلى المصادر فقط. لا تنقل أخطاء المصدر على أنها حقائق مؤكدة.
8. اشرح الاختصار الأجنبي أو اسم المؤسسة أو الشركة أو المختبر أو الآلية بين قوسين عند أول ذكر مباشرة بعد الاسم، بتعريف موجز دقيق. لا تكرر التعريف بلا حاجة.
9. اكتب المبالغ بصيغة مختصرة دقيقة مثل «180.6 مليون أورو» أو «1.1 مليار أورو». استخدم «أورو» دائمًا لا «يورو». لا تنسب قيمة مشروع إجمالي إلى جزء واحد منه.
10. لا تخترع أي أرقام أو أسعار أو مواصفات أو اقتباسات أو تواريخ أو اتفاقات أو دوافع سرية أو مواعيد تنفيذ. ميّز بين النية والمقترح والموافقة والعقد والتنفيذ والتسليم.
11. عند نقل اتهام أو ادعاء من طرف في أزمة، انسبه بوضوح إلى الجهة التي طرحته ولا تعرضه كحقيقة مستقلة ما لم تؤيده أدلة مستقلة في المواد.
12. لا تخلط بين تصريحات المسؤولين، والقرارات الرسمية، والتحليلات الصحفية، والتوقعات. اذكر عدم اليقين أو نقص المعلومات حين يكون مؤثرًا.
13. استخدم فقط روابط المصادر الأصلية الواردة في المواد. لا تخترع مصادر أو روابط. اختر المصادر التي تسند فعلًا الاستنتاجات الجوهرية.
14. إذا كانت المواد قديمة أو متناقضة أو سطحية أو لا تسمح ببطاقة موثوقة ذات قيمة، فأعد publish=false بدل اختلاق تحليل.
15. إذا كانت البطاقة عن صفقة أو منظومة عسكرية، احتفظ بالتفاصيل التقنية والتعاقدية المهمة، وميّز بين الأداء الذي تعلنه الشركة والأداء المثبت ميدانيًا.
16. إذا كانت المادة عن تحول تنظيمي أو استراتيجية دفاعية، اشرح العلاقة بين التنظيم والقدرات والجاهزية والقاعدة الصناعية والعمليات، دون افتراض أن إعلانًا يعني أن القدرة دخلت الخدمة.
17. لا تُصدر حكمًا سياسيًا أو ترتيبًا غير مسند، ولا تصف نوايا طرف على أنها مؤكدة دون مصدر واضح.
18. لا تكتب أي شيء خارج JSON.

تاريخ اليوم في تونس: {today}

المواد المصدرية:
{json.dumps(source_material, ensure_ascii=False)}
"""

def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing. The workflow reuses the existing GitHub Actions secret.")

    news = load_json(NEWS_FILE, {})
    items = news.get("items", []) if isinstance(news, dict) else []
    items = list(items) + fetch_strategic_rss_items()
    default_data = {kind: [] for kind in TYPE_ORDER}
    default_data["checked_urls"] = []
    data = load_json(DATA_FILE, default_data)
    if not isinstance(data, dict):
        data = default_data
    for kind in TYPE_ORDER:
        if not isinstance(data.get(kind), list):
            data[kind] = []
    if not isinstance(data.get("checked_urls"), list):
        data["checked_urls"] = []

    # Migrate the old two-category data shape without discarding existing cards.
    checked = set(clean_text(url) for url in data["checked_urls"] if clean_text(url))
    sync_archive(data)

    candidates = select_candidates(items, checked)
    if not candidates:
        print("No new, sufficiently relevant strategic sources were found.")
        return

    source_material = []
    for index, item in enumerate(candidates, start=1):
        title = clean_text(item.get("original_title") or item.get("title"))
        description = clean_text(
            item.get("original_description") or item.get("description") or item.get("summary")
        )
        body = get_article_text(item["_url"])
        scores = item.get("_type_scores", {})
        likely_types = [
            TYPE_LABELS[kind] for kind in TYPE_ORDER
            if scores.get(kind, 0) >= 2
        ]
        source_material.append({
            "id": index,
            "title": title[:450],
            "description": description[:1600],
            "published": clean_text(item.get("published")),
            "source": clean_text(item.get("source")),
            "category_from_feed": clean_text(item.get("category")),
            "possible_topics": likely_types,
            "url": item["_url"],
            "article_text": body
        })

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=180000)
    )
    schema = {
        "type": "object",
        "properties": {
            "publish": {"type": "boolean"},
            "type": {"type": "string", "enum": TYPE_ORDER},
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
    result = client.interactions.create(
        model=MODEL,
        input=build_prompt(source_material),
        response_format={"type": "text", "mime_type": "application/json", "schema": schema},
        generation_config={"temperature": 0.2, "thinking_level": "low"}
    )
    try:
        output = json.loads(result.output_text or "{}")
    except json.JSONDecodeError:
        raise RuntimeError("Gemini returned invalid JSON; no card was published.")

    # If no reliable card can be produced, mark these sources as reviewed so
    # weak material is not submitted repeatedly. When a card is published, only
    # its selected sources are marked; other candidates remain available later.
    if not output.get("publish"):
        data["checked_urls"] = list(dict.fromkeys(
            [clean_text(url) for url in data["checked_urls"] if clean_text(url)]
            + [item["_url"] for item in candidates]
        ))[-1500:]
        save_json(DATA_FILE, data)
        sync_archive(data)
        print("Model rejected the source material as insufficient for a reliable card.")
        return

    kind = clean_text(output.get("type"))
    title = clean_text(output.get("title"))
    sections = [
        {"heading": clean_text(section.get("heading")), "text": clean_text(section.get("text"))}
        for section in output.get("sections", [])
        if isinstance(section, dict) and clean_text(section.get("heading")) and clean_text(section.get("text"))
    ]
    valid_urls = {item["url"] for item in source_material}
    chosen_urls = [
        clean_text(url) for url in output.get("source_urls", [])
        if clean_text(url) in valid_urls
    ]
    chosen_urls = list(dict.fromkeys(chosen_urls))

    if (
        kind not in TYPE_LABELS
        or not title
        or len(clean_text(output.get("summary"))) < 60
        or len(sections) < 4
        or not any(
            "خلاصة استراتيجية" in section["heading"]
            or "الخلاصة الاستراتيجية" in section["heading"]
            for section in sections
        )
        or not chosen_urls
    ):
        data["checked_urls"] = list(dict.fromkeys(
            [clean_text(url) for url in data["checked_urls"] if clean_text(url)]
            + [item["_url"] for item in candidates]
        ))[-1500:]
        save_json(DATA_FILE, data)
        sync_archive(data)
        print("Generated card failed type, section, title, or source validation. No publication.")
        return

    data["checked_urls"] = list(dict.fromkeys(
        [clean_text(url) for url in data["checked_urls"] if clean_text(url)]
        + chosen_urls
    ))[-1500:]

    source_by_url = {item["url"]: item for item in source_material}
    sources = [
        {
            "title": source_by_url[url]["source"] or source_by_url[url]["title"],
            "url": url
        }
        for url in chosen_urls
    ]
    card = {
        "id": datetime.now(TZ).strftime("%Y%m%d%H%M%S"),
        "type": kind,
        "kind": kind,
        "kind_label": TYPE_LABELS[kind],
        "title": title,
        "date": datetime.now(TZ).strftime("%Y-%m-%d"),
        "region": clean_text(output.get("region")),
        "summary": clean_text(output.get("summary")),
        "sections": sections,
        "sources": sources
    }

    existing_cards = [
        card_item
        for category in TYPE_ORDER
        for card_item in data.get(category, [])
        if isinstance(card_item, dict)
    ]
    title_key = re.sub(r"\W+", "", title.lower())
    if any(
        re.sub(r"\W+", "", clean_text(existing.get("title")).lower()) == title_key
        for existing in existing_cards
    ):
        save_json(DATA_FILE, data)
        sync_archive(data)
        print("A card with the same title already exists. Skipping duplicate.")
        return

    data[kind].append(card)
    data[kind] = data[kind][-MAX_ARCHIVE_CARDS_PER_TYPE:]
    sync_archive(data)
    save_json(DATA_FILE, data)
    print(f"Published strategic card: {title} ({TYPE_LABELS[kind]})")

if __name__ == "__main__":
    main()
