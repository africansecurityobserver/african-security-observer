import json
import os
import re
import xml.etree.ElementTree as ET
from html import unescape
from datetime import datetime, timezone, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from selectolax.lexbor import LexborHTMLParser
try:
    from googlenewsdecoder import gnewsdecoder
except ImportError:
    gnewsdecoder = None
from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
NEWS_FILE = ROOT / "news.json"
DATA_FILE = ROOT / "strategic_cards.json"
ARCHIVE_DIR = ROOT / "strategic_cards" / "archive"
TZ = ZoneInfo("Africa/Tunis")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
MAX_CANDIDATES = 60
MAX_CARD_AGE_DAYS = 7
MAX_ARCHIVE_CARDS_PER_TYPE = 300
MAX_CARDS_PER_RUN = 6

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
    ],
    # Broad source-specific monitoring list kept in code only. The public page
    # displays research cards, not this internal list of institutions.
    "source_iiss": ['site:iiss.org (Africa OR Sahel OR security OR military OR defence OR defense OR strategy OR geopolitics)'],
    "source_csis": ['site:csis.org (Africa OR Sahel OR security OR military OR defence OR defense OR strategy OR geopolitics)'],
    "source_chatham_house": ['site:chathamhouse.org (Africa OR Sahel OR security OR conflict OR defence OR defense OR geopolitics)'],
    "source_sipri": ['site:sipri.org (Africa OR arms transfers OR military spending OR weapons OR conflict OR security)'],
    "source_rand": ['site:rand.org (Africa OR national security OR military strategy OR defense OR defence OR conflict)'],
    "source_brookings": ['site:brookings.edu (Africa OR security OR foreign policy OR military OR conflict OR strategy)'],
    "source_atlantic_council": ['site:atlanticcouncil.org (Africa OR Sahel OR defense OR security OR geopolitics OR military)'],
    "source_rusi": ['site:rusi.org (Africa OR military OR defence OR defense OR security OR strategy OR conflict)'],
    "source_carnegie_endowment": ['site:carnegieendowment.org (Africa OR security OR foreign policy OR military OR geopolitics)'],
    "source_carnegie_middle_east": ['site:carnegie-mec.org (security OR defense OR military OR Middle East OR North Africa OR Libya)'],
    "source_crisis_group": ['site:crisisgroup.org (Africa OR Sahel OR Libya OR Sudan OR conflict OR security)'],
    "source_ecfr": ['site:ecfr.eu (Africa OR security OR defense OR defence OR geopolitics OR Middle East)'],
    "source_stimson": ['site:stimson.org (Africa OR security OR defense OR arms control OR military OR geopolitics)'],
    "source_wilson_center": ['site:wilsoncenter.org (Africa OR security OR military OR defense OR Middle East OR geopolitics)'],
    "source_cfr": ['site:cfr.org (Africa OR security OR military OR foreign policy OR conflict OR defense)'],
    "source_heritage": ['site:heritage.org (national security OR military OR defense OR Africa OR Middle East)'],
    "source_hudson": ['site:hudson.org (military OR defense OR security OR Africa OR Middle East OR geopolitics)'],
    "source_gmf": ['site:gmfus.org (Africa OR security OR defense OR geopolitics OR NATO OR military)'],
    "source_swp": ['site:swp-berlin.org (Africa OR security OR defense OR military OR geopolitics)'],
    "source_clingendael": ['site:clingendael.org (Africa OR Sahel OR security OR defense OR conflict)'],
    "source_ifri": ['site:ifri.org (Africa OR sécurité OR défense OR militaire OR géopolitique)'],
    "source_iris": ['site:iris-france.org (Afrique OR sécurité OR défense OR géopolitique OR militaire)'],
    "source_elcano": ['site:realinstitutoelcano.org (Africa OR security OR defense OR geopolitics OR military)'],
    "source_fiia": ['site:fiia.fi (security OR defense OR NATO OR geopolitics OR military)'],
    "source_lowy": ['site:lowyinstitute.org (Indo-Pacific OR security OR defense OR military OR geopolitics)'],
    "source_aspi": ['site:aspistrategist.org.au (defence OR defense OR military OR security OR Indo-Pacific)'],
    "source_orf": ['site:orfonline.org (security OR defense OR military OR Africa OR geopolitics)'],
    "source_iss_africa": ['site:issafrica.org (Africa OR Sahel OR security OR conflict OR military)'],
    "source_africa_center": ['site:africacenter.org (Africa OR security OR military OR Sahel OR conflict)'],
    "source_acss": ['site:africom.mil "Africa Center for Strategic Studies" (security OR Africa OR military)'],
    "source_idsa": ['site:idsa.in (defence OR defense OR security OR military OR Africa OR geopolitics)'],
    "source_nids": ['site:nids.mod.go.jp (security OR defense OR military OR Indo-Pacific)'],
    "source_kida": ['site:kida.re.kr (security OR defense OR military OR strategy)'],
    "source_nbr": ['site:nbr.org (Asia OR security OR defense OR military OR geopolitics)'],
    "source_taiwan_institute": ['site:indsr.org.tw (defense OR security OR military OR strategy OR Indo-Pacific)'],
    "source_clingendael_insights": ['site:clingendael.org/publication (Africa OR security OR conflict OR defense)']
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

def decode_google_news_url(url):
    """Resolve Google News redirect links to the publisher's canonical article."""
    if not url or "news.google.com" not in url or gnewsdecoder is None:
        return url
    try:
        result = gnewsdecoder(url, interval=0.5)
        if result.get("success") and result.get("decoded_url"):
            return result["decoded_url"]
    except Exception as exc:
        print(f"Google News URL decoding failed: {type(exc).__name__}")
    return url


def get_article_text(url):
    if not url or not url.startswith(("http://", "https://")):
        return ""

    resolved_url = decode_google_news_url(url)
    urls_to_try = []
    for candidate_url in (resolved_url, url):
        if candidate_url and candidate_url not in urls_to_try:
            urls_to_try.append(candidate_url)

    for candidate_url in urls_to_try:
        try:
            response = requests.get(
                candidate_url,
                timeout=20,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/126.0 Safari/537.36",
                    "Accept-Language": "en-US,en;q=0.9,fr;q=0.8,ar;q=0.7"
                },
                allow_redirects=True
            )
            if response.status_code != 200 or not response.text:
                continue

            parser = LexborHTMLParser(response.text)
            # Capture JSON-LD article text before stripping non-content nodes.
            structured_parts = []
            for node in parser.css('script[type="application/ld+json"]'):
                raw = node.text(strip=True)
                if raw and any(token in raw.lower() for token in ('articlebody', 'articlebody', 'description')):
                    structured_parts.append(raw)

            for selector in ("script", "style", "nav", "header", "footer", "aside", "form"):
                for node in parser.css(selector):
                    node.decompose()

            article = parser.css_first("article") or parser.css_first("main") or parser.body
            article_text = clean_text(article.text(separator=" ", strip=True)) if article else ""

            # Try common publisher content containers if the generic article/main
            # element was a shell or navigation wrapper.
            if len(article_text) < 500:
                chunks = []
                for selector in (
                    '[itemprop="articleBody"]', '.article-body', '.article__body',
                    '.story-body', '.entry-content', '.post-content', '.article-content',
                    '[data-testid="article-body"]'
                ):
                    for node in parser.css(selector):
                        value = clean_text(node.text(separator=" ", strip=True))
                        if len(value) > len(article_text):
                            chunks.append(value)
                if chunks:
                    article_text = max(chunks, key=len)

            if len(article_text) < 500:
                meta_parts = []
                for selector in (
                    'meta[property="og:description"]',
                    'meta[name="description"]',
                    'meta[name="twitter:description"]'
                ):
                    node = parser.css_first(selector)
                    if node:
                        value = clean_text(node.attributes.get("content", ""))
                        if value:
                            meta_parts.append(value)
                if meta_parts:
                    article_text = clean_text(article_text + " " + " ".join(meta_parts))

            if len(article_text) >= 500:
                print(f"Extracted {len(article_text)} characters from {response.url}")
                return article_text[:12000]

            # A page may contain structured articleBody even when visible HTML
            # extraction fails; keep this as a last extraction path.
            for raw in structured_parts:
                try:
                    parsed = json.loads(raw)
                    objects = parsed if isinstance(parsed, list) else [parsed]
                    for obj in objects:
                        if not isinstance(obj, dict):
                            continue
                        body = clean_text(obj.get("articleBody") or obj.get("description"))
                        if len(body) >= 500:
                            print(f"Extracted {len(body)} characters from JSON-LD at {response.url}")
                            return body[:12000]
                except Exception:
                    continue

        except Exception as exc:
            print(f"Source extraction failed for {candidate_url}: {type(exc).__name__}")

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
                for entry in entries[:5 if topic.startswith('source_') else 4]:
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
                    article_url = decode_google_news_url(wrapper_url)
                    try:
                        redirected = requests.get(
                            article_url, timeout=8, headers=headers,
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
                        "url": article_url,
                        "google_news_url": wrapper_url
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
        # Center-specific searches already constrain results to security,
        # defense, strategy, or geopolitics. Allow a lower score for those
        # results so the generator has enough full-text candidates to try;
        # the strict text-extraction and card-quality gates remain unchanged.
        is_center_search = str(item.get("_strategic_topic", "")).startswith("source_")
        # Keep a wider fallback pool. The source-text extraction and strict
        # model-quality checks below remain mandatory, so broader selection
        # improves availability without allowing weak snippets to be published.
        if score < 1:
            continue
        seen_urls.add(url)
        copy = dict(item)
        copy["_score"] = score
        copy["_type_scores"] = scores
        copy["_url"] = url
        candidates.append(copy)

    # First diversify across research-center feeds, so one prolific publisher
    # cannot crowd every other center out of the morning/evening source packet.
    chosen = []
    chosen_urls = set()
    source_items = sorted(
        [item for item in candidates if str(item.get("_strategic_topic", "")).startswith("source_")],
        key=lambda item: (
            item["_score"],
            parse_date(item.get("published")) or datetime.min.replace(tzinfo=TZ)
        ),
        reverse=True
    )
    seen_source_topics = set()
    for item in source_items:
        topic = item.get("_strategic_topic")
        if topic in seen_source_topics:
            continue
        chosen.append(item)
        chosen_urls.add(item["_url"])
        seen_source_topics.add(topic)
        # Give several independent research centers a chance to supply
        # a full-text article, while leaving most slots for concrete current
        # strategic developments. The article-text and quality gates below
        # reject thin search snippets and unrelated results.
        if len(chosen) >= min(8, MAX_CANDIDATES):
            break

    # Fill remaining slots with a balanced mix of strategic topics.
    pools = {}
    for kind in TYPE_ORDER:
        pools[kind] = sorted(
            [
                item for item in candidates
                if not str(item.get("_strategic_topic", "")).startswith("source_")
                and item["_type_scores"].get(kind, 0) >= 2
            ],
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
            str(item.get("_strategic_topic", "")).startswith("source_"),
            -item["_score"],
            -(parse_date(item.get("published")) or datetime.min.replace(tzinfo=TZ)).timestamp()
        )
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
                "subject": clean_text(card.get("subject")),
                "slot": clean_text(card.get("slot")),
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

def build_prompt(source_material, slot_label, window_label, date_label):
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    return f"""
أنت رئيس تحرير ومحلل استراتيجي في «المرصد الإفريقي للأمن والدفاع». المطلوب إعداد بطاقة استراتيجية عربية عميقة ومقنعة اعتمادًا على مقال بحثي واحد مرفق في المواد المصدرية. كل بطاقة يجب أن تعالج مقالًا أصليًا واحدًا فقط، وألا تدمج موضوعات مقالات مختلفة في بطاقة واحدة. هذه ليست بطاقة خبرية قصيرة، بل تقدير موقف منظم يشرح أطروحة المقال وأدلته وسياقه ودلالاته وتداعياته. استهدف 900 إلى 1200 كلمة عربية تقريبًا، بما يعادل نحو صفحة ونصف إلى صفحتين عند الطباعة. لا تكرر الفكرة نفسها لإطالة النص؛ إذا كان المقال الأصلي قصيرًا أو لا يتيح تحليلًا موثوقًا بهذا الحجم، أعد publish=false.

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
2. أنشئ من 5 إلى 7 أقسام تحليلية بعناوين دقيقة متخصصة بالموضوع، بما فيها قسم مستقل بعنوان «خلاصة استراتيجية». لا تستخدم عناوين ثابتة لا تناسب المادة.
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
الفترة الزمنية المعتمدة لهذه البطاقة: {window_label}
نوع البطاقة المطلوب: {slot_label}
عنوان الموضوع الذي تقترحه داخل JSON يجب أن يكون عنوانًا تحليليًا يعبّر عن القضية المختارة، لا عنوان النشرة اليومية. سيُحفظ هذا العنوان بوصفه موضوع البطاقة، بينما سيُعتمد عنوان النشر الثابت وفق الفترة: «البطاقة الاستراتيجية {slot_label} ليوم {date_label}».
لا تستخدم أي خبر خارج الفترة الزمنية المحددة أعلاه، ولا تعتمد على معلومات منشورة قبل بدايتها أو بعدها.

المواد المصدرية:
{json.dumps(source_material, ensure_ascii=False)}
"""

ARABIC_MONTHS = {
    1: "جانفي", 2: "فيفري", 3: "مارس", 4: "أفريل",
    5: "ماي", 6: "جوان", 7: "جويلية", 8: "أوت",
    9: "سبتمبر", 10: "أكتوبر", 11: "نوفمبر", 12: "ديسمبر"
}

def arabic_date_label(value):
    return f"{value.day} {ARABIC_MONTHS[value.month]} {value.year}"

def filter_items_for_slot(items, slot, now):
    """Select recent source items for event-driven generation, not fixed daily slots."""
    end = now
    start = now - timedelta(hours=48)
    slot_label = "من مصدر إخباري"
    window_label = f"آخر 48 ساعة حتى {now.strftime('%Y-%m-%d %H:%M')} بتوقيت تونس"

    filtered = []
    undated = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        published = parse_date(item.get("published"))
        # Keep undated official feed items eligible; their source text and
        # duplicate checks still apply. Dated items must be within 48 hours.
        if published is None:
            undated += 1
            filtered.append(item)
        elif start <= published <= end:
            filtered.append(item)
    print(
        f"Mode=event-driven; window={window_label}; "
        f"recent_items={len(filtered)}; undated_items={undated}"
    )
    return filtered, slot_label, window_label

def unpack_generated_card(output):
    """Normalize model output and return fields used by deterministic quality checks."""
    kind = clean_text(output.get("type"))
    subject = clean_text(output.get("title"))
    sections = [
        {"heading": clean_text(section.get("heading")), "text": clean_text(section.get("text"))}
        for section in output.get("sections", [])
        if isinstance(section, dict)
        and clean_text(section.get("heading"))
        and clean_text(section.get("text"))
    ]
    summary_text = clean_text(output.get("summary"))
    full_text = " ".join([summary_text] + [section["text"] for section in sections])
    word_count = len(re.findall(r"\S+", full_text))
    has_conclusion = any(
        "خلاصة استراتيجية" in section["heading"]
        or "الخلاصة الاستراتيجية" in section["heading"]
        for section in sections
    )
    valid = (
        kind in TYPE_LABELS
        and bool(subject)
        and len(summary_text) >= 60
        and len(sections) >= 5
        and has_conclusion
        and word_count >= 650
    )
    return kind, subject, sections, summary_text, word_count, has_conclusion, valid


def main():
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing. The workflow reuses the existing GitHub Actions secret.")

    # News cards are event-driven: the workflow checks frequently and creates
    # a separate card for each qualifying source item, rather than fixed morning
    # and evening slots.
    slot = "news"
    now = datetime.now(TZ)
    date_label = arabic_date_label(now)

    news = load_json(NEWS_FILE, {})
    items = news.get("items", []) if isinstance(news, dict) else []
    # Combine general strategic searches with a broad, code-only list of research-center feeds.
    items = list(items) + fetch_strategic_rss_items()
    items, slot_label, window_label = filter_items_for_slot(items, slot, now)

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

    checked = set(clean_text(url) for url in data["checked_urls"] if clean_text(url))
    sync_archive(data)

    # Duplicate prevention is source-URL based (checked_urls) and subject based;
    # several independent cards may be published on the same day.
    candidates = select_candidates(items, checked)
    if not candidates:
        print(f"No new, sufficiently relevant source articles were found for the {slot_label} period; no card will be fabricated.")
        return

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

    published_count = 0
    reviewed_count = 0
    # One source article -> one independent card. Process several per run;
    # frequent scheduled runs continue with remaining unchecked candidates.
    for index, item in enumerate(candidates, start=1):
        if published_count >= MAX_CARDS_PER_RUN:
            print(f"Per-run safety limit reached ({MAX_CARDS_PER_RUN}); remaining sources stay eligible for the next run.")
            break

        url = item["_url"]
        title_original = clean_text(item.get("original_title") or item.get("title"))
        description = clean_text(
            item.get("original_description") or item.get("description") or item.get("summary")
        )
        body = get_article_text(url)

        # Do not pretend to summarize a full article if both the extracted
        # body and the publisher's abstract/description are too thin.
        if len(body) < 500 and len(description) < 350:
            # Some RSS records carry a canonical publisher URL and a Google
            # News wrapper URL. Try the alternate link before abandoning it.
            alternate_url = clean_text(item.get("google_news_url"))
            if alternate_url and alternate_url != url:
                body = get_article_text(alternate_url)
            if len(body) < 500 and len(description) < 350:
                print(
                    f"Skipping source with insufficient readable text "
                    f"(body={len(body)}, description={len(description)}); "
                    f"will remain eligible for a later run: {title_original[:120]}"
                )
                reviewed_count += 1
                continue

        scores = item.get("_type_scores", {})
        likely_types = [
            TYPE_LABELS[kind] for kind in TYPE_ORDER
            if scores.get(kind, 0) >= 2
        ]
        source_material = [{
            "id": index,
            "title": title_original[:450],
            "description": description[:1600],
            "published": clean_text(item.get("published")),
            "source": clean_text(item.get("source")),
            "category_from_feed": clean_text(item.get("category")),
            "possible_topics": likely_types,
            "url": url,
            "article_text": body
        }]

        base_prompt = build_prompt(source_material, slot_label, window_label, date_label)
        try:
            result = client.interactions.create(
                model=MODEL,
                input=base_prompt,
                response_format={"type": "text", "mime_type": "application/json", "schema": schema},
                generation_config={"temperature": 0.2, "thinking_level": "low"}
            )
            output = json.loads(result.output_text or "{}")
        except Exception as exc:
            # A transient model/API error must not permanently blacklist the source.
            print(f"Strategic-card generation failed for one article: {type(exc).__name__}")
            continue

        reviewed_count += 1
        if not output.get("publish"):
            checked.add(url)
            data["checked_urls"] = list(checked)[-1500:]
            save_json(DATA_FILE, data)
            sync_archive(data)
            print(f"Source rejected as insufficient for a reliable standalone card: {title_original[:120]}")
            continue

        kind, subject, sections, summary_text, word_count, has_conclusion, valid = unpack_generated_card(output)

        if not valid:
            print(
                f"First draft failed quality validation (words={word_count}, sections={len(sections)}); "
                "requesting one corrective rewrite."
            )
            retry_prompt = base_prompt + """

مراجعة تصحيحية إلزامية: المسودة السابقة لم تحقق الحد الأدنى المطلوب من الجودة.
أعد كتابة البطاقة كاملة، لا تكتفِ بإضافة جمل قصيرة. المطلوب 900 كلمة عربية تقريباً،
وخمسة أقسام تحليلية على الأقل بما فيها قسم مستقل بعنوان «خلاصة استراتيجية».
احرص على أن يكون لكل قسم مضمون مختلف، وأن تشرح الخلفية والأدلة والتداعيات والقيود،
مع فقرة ختامية عن تونس حين تكون ذات صلة. إذا كان المصدر لا يتيح ذلك بصدق، أعد publish=false.
أخرج JSON المطابق للمخطط فقط.
"""
            try:
                retry_result = client.interactions.create(
                    model=MODEL,
                    input=retry_prompt,
                    response_format={"type": "text", "mime_type": "application/json", "schema": schema},
                    generation_config={"temperature": 0.15, "thinking_level": "low"}
                )
                retry_output = json.loads(retry_result.output_text or "{}")
                reviewed_count += 1
                if not retry_output.get("publish"):
                    checked.add(url)
                    data["checked_urls"] = list(checked)[-1500:]
                    save_json(DATA_FILE, data)
                    sync_archive(data)
                    print(f"Corrective rewrite rejected as insufficient: {title_original[:120]}")
                    continue
                output = retry_output
                kind, subject, sections, summary_text, word_count, has_conclusion, valid = unpack_generated_card(output)
            except Exception as exc:
                print(f"Corrective rewrite failed; source remains eligible for retry: {type(exc).__name__}")
                continue

        if not valid:
            print(
                f"Generated card still failed quality validation (words={word_count}, sections={len(sections)}); "
                "source remains unchecked for a later retry."
            )
            continue

        existing_cards = [
            existing
            for category in TYPE_ORDER
            for existing in data.get(category, [])
            if isinstance(existing, dict)
        ]
        subject_key = re.sub(r"\W+", "", subject.lower())
        if any(
            re.sub(r"\W+", "", clean_text(existing.get("subject") or existing.get("title")).lower()) == subject_key
            for existing in existing_cards
        ):
            print(f"Duplicate strategic subject skipped: {subject}")
            checked.add(url)
            data["checked_urls"] = list(checked)[-1500:]
            save_json(DATA_FILE, data)
            sync_archive(data)
            continue

        publication_title = f"بطاقة استراتيجية من مصدر إخباري — {subject}"
        card = {
            "id": datetime.now(TZ).strftime("%Y%m%d%H%M%S") + "_" + re.sub(r"[^a-zA-Z0-9]", "", __import__("hashlib").sha1(url.encode("utf-8")).hexdigest())[:8],
            "slot": slot,
            "type": kind,
            "kind": kind,
            "kind_label": TYPE_LABELS[kind],
            "title": publication_title,
            "subject": subject,
            "date": now.strftime("%Y-%m-%d"),
            "region": clean_text(output.get("region")),
            "summary": f"موضوع البطاقة: {subject}. {summary_text}",
            "sections": sections,
            "sources": [{
                "title": clean_text(item.get("source")) or title_original,
                "url": url
            }]
        }

        data[kind].append(card)
        data[kind] = data[kind][-MAX_ARCHIVE_CARDS_PER_TYPE:]
        checked.add(url)
        data["checked_urls"] = list(checked)[-1500:]
        save_json(DATA_FILE, data)
        sync_archive(data)
        published_count += 1
        print(f"Published independent strategic card: {subject} ({TYPE_LABELS[kind]}; approx. {word_count} words)")

    print(f"Strategic-card run complete: published={published_count}; reviewed={reviewed_count}; candidates={len(candidates)}.")

if __name__ == "__main__":
    main()
