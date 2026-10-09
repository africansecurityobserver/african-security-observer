import feedparser
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus, quote
from googlenewsdecoder import gnewsdecoder
from deep_translator import GoogleTranslator
from urllib.request import Request, urlopen


# =========================================================
# COUNTRIES
# =========================================================

SAHEL_COUNTRIES = [
    "Mali",
    "Niger",
    "Burkina Faso",
    "Chad",
    "Mauritania",
    "Senegal",
    "Guinea",
    "Ivory Coast"
]

NORTH_AFRICA_COUNTRIES = [
    "Tunisia",
    "Algeria",
    "Morocco",
    "Libya",
    "Egypt"
]

REST_OF_AFRICA_COUNTRIES = [
    "Nigeria",
    "Kenya",
    "Ethiopia",
    "Somalia",
    "Sudan",
    "South Sudan",
    "Democratic Republic of Congo",
    "Cameroon",
    "Uganda",
    "South Africa",
    "Zimbabwe",
    "Mozambique",
    "Tanzania",
    "Ghana",
    "Namibia",
    "Botswana",
    "Rwanda",
    "Burundi",
    "Angola",
    "Zambia",
    "Malawi",
    "Gabon",
    "Republic of Congo",
    "Central African Republic",
    "Sierra Leone",
    "Liberia",
    "Gambia",
    "Guinea-Bissau",
    "Togo",
    "Benin",
    "Djibouti",
    "Cabo Verde",
    "Equatorial Guinea",
    "Eritrea",
    "Eswatini",
    "Lesotho",
    "Madagascar",
    "Mauritius",
    "Seychelles",
    "Comoros",
    "Sao Tome and Principe"
]


# =========================================================
# SEARCH TERMS
# =========================================================

SECURITY_TERMS = [
    "security",
    "military",
    "army",
    "armed attack",
    "terrorist attack",
    "terrorism",
    "terrorist",
    "militants",
    "militant attack",
    "insurgents",
    "insurgency",
    "jihadist",
    "jihadists",
    "bombing",
    "bomb",
    "explosion",
    "explosions",
    "ambush",
    "clash",
    "clashes",
    "attack",
    "attacks",
    "kidnapping",
    "hostage",
    "IED",
    "counterterrorism",
    "counter-terrorism",
    "border security",
    "peacekeeping",
    "troops",
    "soldiers",
    "airstrike",
    "air strikes",
    "military operation",
    "military operations",
    "diplomacy",
    "diplomatic",
    "foreign minister",
    "foreign affairs",
    "bilateral talks",
    "official visit",
    "high-level meeting",
    "strategic partnership",
    "security cooperation",
    "defense cooperation",
    "defence cooperation",
    "military cooperation",
    "signed agreement",
    "memorandum of understanding",
    "treaty",
    "joint commission",
    "summit",
    "ministerial meeting",
    "presidential visit",
    "defense agreement",
    "defence agreement",
    "diplomatic relations",
    "joint statement",
    "strategic dialogue",
    "security agreement",
    "coopération",
    "visite officielle",
    "relations bilatérales",
    "accord de défense",
    "accord de sécurité",
    "ministre des affaires étrangères",
    "forces armées",
    "militaire",
    "diplomatie",
    "défense",
    "sécurité",
    "تعاون",
    "دبلوماسية",
    "زيارة رسمية",
    "اتفاقية",
    "مذكرة تفاهم",
    "وزير الخارجية",
    "تعاون دفاعي",
    "تعاون أمني",
    "قمة",
    "مباحثات",
    "عمليات عسكرية",
    "عملية عسكرية",
    "القوات المسلحة",
    "الجيش",
    "القوات الجوية",
    "القوات البحرية",
    "القوات البرية",
    "هجوم",
    "غارة جوية",
    "غارات جوية",
    "اشتباكات",
    "قصف",
    "قوات مشتركة",
    "نشر قوات",
    "تعزيزات عسكرية",
    "انسحاب القوات",
    "طائرات مسيرة",
    "طائرات بدون طيار",
    "صواريخ",
    "دفاع جوي",
    "مناورات عسكرية",
    "تدريبات عسكرية",
    "تسلح",
    "صفقة أسلحة",
    "قاعدة عسكرية",
    "أمن الحدود",
    "مكافحة الإرهاب",
    "جماعات مسلحة",
    "تنظيم إرهابي",
    "وزير الدفاع",
    "رئيس الأركان",
    "مخابرات",
    "استخبارات",
    "اتفاق أمني",
    "اتفاق دفاعي",
    "وقف إطلاق النار",
    "سفينة حربية",
    "غواصة",
    "مقاتلات",
    "مركبات مدرعة",
    "قوات حفظ السلام"
]


DEFENSE_TERMS = [
    "weapon",
    "weapons",
    "missile",
    "missiles",
    "rocket",
    "rockets",
    "drone",
    "drones",
    "UAV",
    "fighter jet",
    "fighter jets",
    "fighter aircraft",
    "combat aircraft",
    "warplane",
    "warplanes",
    "tank",
    "tanks",
    "armored vehicle",
    "armoured vehicle",
    "armored vehicles",
    "armoured vehicles",
    "submarine",
    "submarines",
    "frigate",
    "frigates",
    "corvette",
    "corvettes",
    "warship",
    "warships",
    "naval vessel",
    "air defense",
    "air defence",
    "military equipment",
    "defense equipment",
    "defence equipment",
    "arms deal",
    "arms contract",
    "arms purchase",
    "weapons deal",
    "weapons contract",
    "military procurement",
    "defense contract",
    "defence contract",
    "military hardware",
    "armament",
    "armaments",
    "military aircraft",
    "military helicopter",
    "helicopters",
    "radar",
    "aircraft carrier",
    "military vehicle"
]


EXCLUDED_TERMS = [
    "football",
    "soccer",
    "match",
    "matches",
    "league",
    "premier league",
    "cup",
    "champions league",
    "sport",
    "sports",
    "player",
    "players",
    "coach",
    "goal",
    "goals",
    "tournament",
    "tennis",
    "basketball",
    "rugby",
    "tourism",
    "tourist",
    "travel",
    "hotel",
    "holiday",
    "vacation",
    "fashion",
    "music",
    "singer",
    "celebrity",
    "film",
    "movie",
    "entertainment",
    "recipe",
    "cooking",
    "food",
    "weather",
    "rainfall",
    "temperature",
    "stock market",
    "stocks",
    "shares",
    "banking",
    "finance",
    "financial markets",
    "cryptocurrency",
    "bitcoin",
    "university",
    "school",
    "education",
    "health",
    "hospital",
    "disease",
    "medical",
    "imf",
    "inflation",
    "gdp",
    "unesco"
]


# =========================================================
# GOOGLE NEWS RSS
# =========================================================

def make_country_feed(country):
    search_parts = []

    for term in SECURITY_TERMS:
        if " " in term:
            term_for_search = f'"{term}"'
        else:
            term_for_search = term

        search_parts.append(
            f'"{country}" {term_for_search}'
        )

    query = " OR ".join(search_parts)
    query = f'({query}) when:2d'

    encoded_query = quote_plus(query)

    return (
        "https://news.google.com/rss/search?q="
        + encoded_query
        + "&hl=en-US&gl=US&ceid=US:en"
    )


def make_leadership_feeds(country):
    """
    Dedicated searches for major meetings, talks and decisions involving
    senior political, diplomatic and military officials in Libya, Algeria
    and Morocco. The wider 7-day window helps avoid missing important events.
    """
    country_names = {
        "Algeria": {"en": '"Algeria"', "fr": '"Algérie"', "ar": '"الجزائر"'},
        "Morocco": {"en": '"Morocco"', "fr": '"Maroc"', "ar": '"المغرب"'},
        "Libya": {"en": '"Libya"', "fr": '"Libye"', "ar": '"ليبيا"'}
    }

    roles = {
        "en": '("president" OR "head of state" OR "prime minister" OR "head of government" OR "foreign minister" OR "minister of foreign affairs" OR "defense minister" OR "defence minister" OR "chief of staff" OR "army chief" OR "armed forces chief")',
        "fr": '("président" OR "chef de l’État" OR "Premier ministre" OR "chef du gouvernement" OR "ministre des Affaires étrangères" OR "ministre de la Défense" OR "chef d’état-major" OR "chef d’état-major général")',
        "ar": '("الرئيس" OR "رئيس الدولة" OR "رئيس الحكومة" OR "الوزير الأول" OR "وزير الخارجية" OR "وزير الشؤون الخارجية" OR "وزير الدفاع" OR "رئيس الأركان" OR "رئيس أركان الجيش" OR "القائد العام")'
    }

    events = {
        "en": '("meeting" OR "talks" OR "held talks" OR "received" OR "met with" OR "official visit" OR "consultations" OR "decision" OR "decree" OR "announced" OR "appointed" OR "agreement" OR "summit" OR "phone call")',
        "fr": '(rencontre OR entretiens OR discussions OR "reçu" OR "visite officielle" OR consultations OR décision OR décret OR annonce OR nommé OR accord OR sommet OR "entretien téléphonique")',
        "ar": '(اجتماع OR مباحثات OR محادثات OR استقبل OR التقى OR زيارة OR مشاورات OR قرار OR مرسوم OR أعلن OR تعيين OR اتفاق OR قمة OR اتصال)'
    }

    locales = {
        "en": ("en-US", "US", "US:en"),
        "fr": ("fr", "FR", "FR:fr"),
        "ar": ("ar", "EG", "EG:ar")
    }

    feeds = []
    for language in ("en", "fr", "ar"):
        query = f'{country_names[country][language]} {roles[language]} {events[language]} when:7d'
        hl, gl, ceid = locales[language]
        feeds.append(
            "https://news.google.com/rss/search?q="
            + quote_plus(query)
            + f"&hl={hl}&gl={gl}&ceid={ceid}"
        )
    return feeds


def make_strategic_diplomacy_feeds(country):
    """
    Localized North Africa security and diplomacy searches.
    Use Google News locale settings matching each search language.
    """
    local_names = {
        "Tunisia": {"fr": '"Tunisie"', "ar": '"تونس"'},
        "Algeria": {"fr": '"Algérie"', "ar": '"الجزائر"'},
        "Morocco": {"fr": '"Maroc"', "ar": '"المغرب"'},
        "Libya": {"fr": '"Libye"', "ar": '"ليبيا"'},
        "Egypt": {"fr": '"Égypte"', "ar": '"مصر"'}
    }

    terms = {
        "en": '("official visit" OR "bilateral talks" OR "strategic partnership" OR agreement OR "defense cooperation" OR "defence cooperation" OR "security cooperation" OR "foreign minister" OR summit OR diplomatic OR military OR security OR border)',
        "fr": '(diplomatie OR "visite officielle" OR accord OR coopération OR défense OR sécurité OR sommet OR militaire OR frontières OR ministre)',
        "ar": '(دبلوماسية OR "زيارة رسمية" OR اتفاقية OR تعاون OR دفاع OR أمن OR قمة OR عسكري OR حدود OR وزير)'
    }

    locales = {
        "en": ("en-US", "US", "US:en"),
        "fr": ("fr", "FR", "FR:fr"),
        "ar": ("ar", "EG", "EG:ar")
    }

    country_en = f'"{country}"'
    queries = [
        ("en", f'{country_en} {terms["en"]} when:7d'),
        ("fr", f'{local_names[country]["fr"]} {terms["fr"]} when:7d'),
        ("ar", f'{local_names[country]["ar"]} {terms["ar"]} when:7d')
    ]

    feeds = []
    for language, query in queries:
        hl, gl, ceid = locales[language]
        feeds.append(
            "https://news.google.com/rss/search?q="
            + quote_plus(query)
            + f"&hl={hl}&gl={gl}&ceid={ceid}"
        )

    return feeds

RSS_FEEDS = {
    "sahel": [
        make_country_feed(country)
        for country in SAHEL_COUNTRIES
    ],

    "north": (
        [
            make_country_feed(country)
            for country in NORTH_AFRICA_COUNTRIES
        ]
        + [
            feed_url
            for country in NORTH_AFRICA_COUNTRIES
            for feed_url in make_strategic_diplomacy_feeds(country)
        ]
    ),

    # Extra priority coverage for senior officials in Libya, Algeria and Morocco.
    "north_leaders": [
        feed_url
        for country in ("Libya", "Algeria", "Morocco")
        for feed_url in make_leadership_feeds(country)
    ],

    "rest": [
        make_country_feed(country)
        for country in REST_OF_AFRICA_COUNTRIES
    ],

    "defense": [
        (
            "https://news.google.com/rss/search?q="
            + quote_plus(
                '(Africa weapons OR Africa missiles OR Africa drones '
                'OR Africa fighter jets OR Africa aircraft '
                'OR Africa tanks OR Africa submarines '
                'OR Africa frigates OR Africa warships '
                'OR Africa arms deal OR Africa arms contract '
                'OR Africa military procurement) when:2d'
            )
            + "&hl=en-US&gl=US&ceid=US:en"
        ),

        (
            "https://news.google.com/rss/search?q="
            + quote_plus(
                '("African military" OR "African army") '
                '(weapons OR drones OR missiles OR aircraft '
                'OR tanks OR ships OR arms deal) when:2d'
            )
            + "&hl=en-US&gl=US&ceid=US:en"
        )
    ],

    # Arabic-language news feeds: direct publishers plus a news aggregator
    # with conflict/diplomacy categories and source-specific feeds.
    "arabic_sources": [
        "https://www.aljazeera.net/aljazeerarss/a7c186be-1baa-4bd4-9d80-a84db769f779/73d0e1b4-532f-45ef-b135-bfdff8b8cab9",
        "https://www.alaraby.co.uk/feeds",
        "https://aawsat.com/feed/news",
        "https://aawsat.com/feed/arab-world",
        "https://aawsat.com/feed/africa",
        "https://alikhbariya.net/feeds/topics/conflict.xml",
        "https://alikhbariya.net/feeds/topics/diplomacy.xml",
        "https://alikhbariya.net/feeds/sources/43f09431-308d-486d-bd8c-fd795d0104c1.xml",
        "https://alikhbariya.net/feeds/sources/0a01f2ab-c9f2-4ce5-a6e7-6c1e0fe8a7e9.xml",
        "https://alikhbariya.net/feeds/sources/699b81c7-946d-4597-9061-c0c730344be6.xml",
        "https://alikhbariya.net/feeds/sources/138ec82a-8a67-420c-b570-55893564ad21.xml",
        "https://alikhbariya.net/feeds/sources/f51d6e12-4518-4c15-9a3c-cd43dd8b8f94.xml",
        "https://alikhbariya.net/feeds/sources/ba242115-5d30-4e48-9562-12c462f01111.xml",
        "https://alikhbariya.net/feeds/sources/8f2e43a8-7327-4b8e-b95d-dea2a728ca4a.xml",
        "https://alikhbariya.net/feeds/sources/015f1c27-1e52-4bd9-8359-40f9e3da4910.xml",
        "https://alikhbariya.net/feeds/sources/dd0c2b11-8436-4ec3-96c5-d2931c08ae03.xml",
        "https://alikhbariya.net/feeds/sources/52db9398-113c-44e2-9843-8a395bea37a3.xml",
        "https://alikhbariya.net/feeds/sources/f9652135-4f5d-4dd6-b87f-55a324eed06e.xml",
        "https://alikhbariya.net/feeds/sources/aa4950ef-9749-42cf-b023-43433d60aafc.xml",
        "https://alikhbariya.net/feeds/sources/8c05d09d-1d37-4c04-a5f1-9c43f16ff71.xml",
        "https://alikhbariya.net/feeds/sources/c437d4bb-fe9b-4b10-ad19-16c71a81e32e.xml",
        "https://alikhbariya.net/feeds/sources/f65491e4-269b-4356-9f7e-6c92da9121f2.xml",
        "https://alikhbariya.net/feeds/sources/7d02e6ac-19d7-40f8-b3dc-f868fa79ed32.xml"
    ]
}


# =========================================================
# SETTINGS
# =========================================================

MAX_NEWS_PER_FEED = 15
MAX_NEWS_AGE_HOURS = 48
CURRENT_NEWS_DAYS = 2


def parse_published_datetime(value):
    """Parse RSS or ISO publication dates into UTC-aware datetimes."""
    if not value:
        return None

    text_value = str(value).strip()
    if not text_value:
        return None

    try:
        dt = parsedate_to_datetime(text_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        pass

    try:
        dt = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


ARCHIVE_FOLDER = "archive"
NEWS_FILE = "news.json"

ARCHIVE_INDEX_FILE = os.path.join(
    ARCHIVE_FOLDER,
    "index.json"
)


# =========================================================
# TRANSLATION
# =========================================================

translator = GoogleTranslator(
    source="auto",
    target="ar"
)


def clean_html(text):
    if not text:
        return ""

    text = re.sub(
        r"<[^>]+>",
        " ",
        text
    )

    text = re.sub(
        r"\s+",
        " ",
        text
    )

    return text.strip()


def contains_arabic(text):
    if not text:
        return False

    arabic_chars = re.findall(
        r"[\u0600-\u06FF]",
        text
    )

    return len(arabic_chars) >= 2


def google_translate_fallback(text):
    if not text:
        return ""

    text = clean_html(text)

    if not text:
        return ""

    chunks = []

    max_length = 1200

    while len(text) > max_length:

        cut = text.rfind(
            ". ",
            0,
            max_length
        )

        if cut < 300:
            cut = max_length

        chunks.append(
            text[:cut + 1]
        )

        text = text[cut + 1:].strip()

    if text:
        chunks.append(text)

    translated_chunks = []

    for chunk in chunks:

        try:

            url = (
                "https://translate.googleapis.com/"
                "translate_a/single"
                "?client=gtx"
                "&sl=auto"
                "&tl=ar"
                "&dt=t"
                "&q="
                + quote(chunk)
            )

            request = Request(
                url,
                headers={
                    "User-Agent": "Mozilla/5.0"
                }
            )

            with urlopen(
                request,
                timeout=20
            ) as response:

                raw = response.read().decode(
                    "utf-8"
                )

            data = json.loads(raw)

            translated = ""

            if (
                isinstance(data, list)
                and len(data) > 0
                and isinstance(data[0], list)
            ):

                for part in data[0]:

                    if (
                        isinstance(part, list)
                        and len(part) > 0
                    ):

                        translated += str(
                            part[0]
                        )

            translated = translated.strip()

            if translated:
                translated_chunks.append(
                    translated
                )

        except Exception as e:

            print(
                f"Google fallback translation failed: {e}"
            )

            return ""

    return " ".join(
        translated_chunks
    ).strip()


def translate_text(text):

    if not text:
        return ""

    text = clean_html(text)

    if not text:
        return ""

    text = text[:5000]

    # محاولة الترجمة بواسطة deep-translator
    for attempt in range(2):

        try:

            translated = translator.translate(
                text
            )

            if (
                translated
                and translated.strip()
                and contains_arabic(
                    translated
                )
            ):

                return translated.strip()

            print(
                "Translator returned unchanged/non-Arabic text."
            )

        except Exception as e:

            print(
                f"Translation attempt {attempt + 1} failed: {e}"
            )

        time.sleep(1)

    # محاولة احتياطية مباشرة
    print(
        "Trying direct Google translation fallback..."
    )

    fallback = google_translate_fallback(
        text
    )

    if (
        fallback
        and contains_arabic(
            fallback
        )
    ):

        return fallback

    print(
        "WARNING: Translation failed completely."
    )

    return text


def translate_article(title, description):

    print(
        f"Translating: {title}"
    )

    arabic_title = translate_text(
        title
    )

    time.sleep(0.5)

    arabic_description = translate_text(
        description
    )

    return (
        arabic_title,
        arabic_description
    )


# =========================================================
# URL DECODER
# =========================================================

def decode_url(url):

    if not url or "news.google.com" not in url:
        return url

    try:

        result = gnewsdecoder(
            url,
            interval=0.5,
            timeout=15
        )

        if result.get("success"):

            return result.get(
                "decoded_url",
                url
            )

    except Exception as e:

        print(
            f"Could not decode URL: {e}"
        )

    return url


# =========================================================
# SECURITY FILTER
# =========================================================

def is_priority_magreb_leadership_news(title, description):
    """
    Preserve important political/diplomatic/military leadership news even
    when an unrelated keyword (for example finance or education) also appears.
    """
    text = (str(title) + " " + str(description)).lower()

    countries = [
        "libya", "libyan", "libye", "ليبيا", "الليبية",
        "algeria", "algerian", "algérie", "الجزائر", "الجزائرية",
        "morocco", "moroccan", "maroc", "المغرب", "المغربية"
    ]
    roles = [
        "president", "head of state", "prime minister", "head of government",
        "foreign minister", "minister of foreign affairs", "defense minister",
        "defence minister", "chief of staff", "army chief", "armed forces chief",
        "président", "premier ministre", "chef du gouvernement",
        "ministre des affaires étrangères", "ministre de la défense",
        "chef d’état-major", "chef d'etat-major",
        "الرئيس", "رئيس الدولة", "رئيس الحكومة", "الوزير الأول",
        "وزير الخارجية", "وزير الشؤون الخارجية", "وزير الدفاع",
        "رئيس الأركان", "رئيس أركان الجيش", "القائد العام",
        "محمد السادس", "عبد المجيد تبون", "عبد الحميد الدبيبة",
        "محمد المنفي", "ناصر بوريطة", "أحمد عطاف", "محمد بن بريك"
    ]
    event_terms = [
        "meeting", "talks", "received", "met with", "official visit",
        "consultations", "decision", "decree", "announced", "appointed",
        "agreement", "summit", "phone call", "rencontre", "entretiens",
        "discussions", "reçu", "visite officielle", "consultations",
        "décision", "décret", "annonce", "accord", "sommet",
        "اجتماع", "مباحثات", "محادثات", "استقبل", "التقى", "زيارة",
        "مشاورات", "قرار", "مرسوم", "أعلن", "تعيين", "اتفاق", "قمة", "اتصال"
    ]

    has_country = any(term in text for term in countries)
    has_role = any(term in text for term in roles)
    has_event = any(term in text for term in event_terms)
    return has_country and has_role and has_event


def is_relevant_security_news(
    title,
    description
):

    text = (
        str(title)
        + " "
        + str(description)
    ).lower()

    if is_priority_magreb_leadership_news(title, description):
        return True

    for word in EXCLUDED_TERMS:

        if word.lower() in text:
            return False

    if any(
        word.lower() in text
        for word in DEFENSE_TERMS
    ):

        return True

    if any(
        word.lower() in text
        for word in SECURITY_TERMS
    ):

        return True

    return False


# =========================================================
# CLASSIFICATION
# =========================================================

def classify_news(
    title,
    description,
    feed_category
):

    text = (
        str(title)
        + " "
        + str(description)
    ).lower()

    north_words = [
        "tunisia",
        "tunisian",
        "algeria",
        "algerian",
        "morocco",
        "moroccan",
        "libya",
        "libyan",
        "egypt",
        "egyptian",
        "north africa",
        "maghreb",
        "تونس",
        "التونسية",
        "الجزائر",
        "الجزائرية",
        "المغرب",
        "المغربية",
        "ليبيا",
        "الليبية",
        "مصر",
        "المصرية",
        "شمال أفريقيا",
        "المغرب العربي"
    ]

    sahel_words = [
        "mali",
        "malian",
        "niger",
        "nigerien",
        "burkina faso",
        "burkinabe",
        "chad",
        "chadian",
        "mauritania",
        "mauritanian",
        "senegal",
        "senegalese",
        "guinea",
        "guinean",
        "ivory coast",
        "cote d'ivoire",
        "côte d'ivoire",
        "sahel",
        "الساحل",
        "مالي",
        "النيجر",
        "بوركينا فاسو",
        "تشاد",
        "موريتانيا",
        "السنغال",
        "غينيا",
        "ساحل العاج"
    ]

    defense_words = [
        word.lower()
        for word in DEFENSE_TERMS
    ]

    if any(
        word in text
        for word in north_words
    ):

        return "north"

    if any(
        word in text
        for word in sahel_words
    ):

        return "sahel"

    if any(
        word in text
        for word in defense_words
    ):

        return "defense"

    if feed_category in ("north", "north_leaders"):
        return "north"

    if feed_category == "sahel":
        return "sahel"

    if feed_category == "defense":
        return "defense"

    return "rest"


# =========================================================
# LOAD EXISTING NEWS
# =========================================================

def load_existing_news():

    if not os.path.exists(
        NEWS_FILE
    ):

        return []

    try:

        with open(
            NEWS_FILE,
            "r",
            encoding="utf-8"
        ) as file:

            data = json.load(
                file
            )

        return data.get(
            "items",
            []
        )

    except Exception as e:

        print(
            f"Could not load existing news: {e}"
        )

        return []


# =========================================================
# GET NEW NEWS
# =========================================================

def get_news():

    items = []

    seen_urls = set()

    seen_titles = set()

    for feed_category, feeds in RSS_FEEDS.items():

        for feed_url in feeds:

            print(
                f"Reading feed: {feed_category}"
            )

            # Fetch RSS with a strict timeout so one unresponsive feed
            # cannot stall the entire scheduled update.
            try:
                request = Request(
                    feed_url,
                    headers={"User-Agent": "Mozilla/5.0"}
                )
                with urlopen(request, timeout=8) as response:
                    feed_content = response.read()
                feed = feedparser.parse(feed_content)
            except Exception as e:
                print(f"Skipping feed after fetch error: {e}")
                continue

            for entry in feed.entries[
                :MAX_NEWS_PER_FEED
            ]:

                title = entry.get(
                    "title",
                    ""
                ).strip()

                google_url = entry.get(
                    "link",
                    ""
                ).strip()

                if not title or not google_url:
                    continue

                description = clean_html(
                    entry.get(
                        "summary",
                        entry.get(
                            "description",
                            ""
                        )
                    )
                )

                if not is_relevant_security_news(
                    title,
                    description
                ):

                    print(
                        f"Rejected unrelated news: {title}"
                    )

                    continue

                article_url = decode_url(
                    google_url
                )

                if article_url in seen_urls:
                    continue

                normalized_title = (
                    title.lower()
                    .replace(" ", "")
                    .replace("-", "")
                    .replace(".", "")
                )

                if normalized_title in seen_titles:
                    continue

                published = entry.get(
                    "published",
                    ""
                )

                if published:
                    published_dt = parse_published_datetime(published)

                    if published_dt is not None:
                        age = datetime.now(timezone.utc) - published_dt

                        if age > timedelta(hours=MAX_NEWS_AGE_HOURS):
                            continue

                        # Reject clearly future-dated feed entries.
                        if age < -timedelta(hours=24):
                            continue

                source = entry.get(
                    "source",
                    {}
                ).get(
                    "title",
                    "مصدر غير معروف"
                )

                category = classify_news(
                    title,
                    description,
                    feed_category
                )

                # ترجمة الخبر الجديد
                arabic_title, arabic_description = (
                    translate_article(
                        title,
                        description
                    )
                )

                items.append({

                    "title": arabic_title,

                    "description": arabic_description,

                    "original_title": title,

                    "original_description": description,

                    "url": article_url,

                    "source": source,

                    "published": published,

                    "category": category
                })

                seen_urls.add(
                    article_url
                )

                seen_titles.add(
                    normalized_title
                )

    items.sort(
        key=lambda x: parse_published_datetime(x.get("published")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True
    )

    return items


# =========================================================
# ARCHIVE HELPERS
# =========================================================

def normalize_article(article):

    return {

        "title": article.get(
            "title",
            ""
        ),

        "description": article.get(
            "description",
            ""
        ),

        "original_title": article.get(
            "original_title",
            ""
        ),

        "original_description": article.get(
            "original_description",
            ""
        ),

        "url": article.get(
            "url",
            ""
        ),

        "source": article.get(
            "source",
            "مصدر غير معروف"
        ),

        "published": article.get(
            "published",
            ""
        ),

        "category": article.get(
            "category",
            "rest"
        )
    }


def article_key(article):

    url = article.get(
        "url",
        ""
    ).strip()

    if url:

        return "url:" + url

    title = article.get(
        "original_title",
        article.get(
            "title",
            ""
        )
    ).lower().strip()

    return "title:" + title


def archive_file_for(article):

    published = article.get(
        "published",
        ""
    )

    dt = parse_published_datetime(published)

    if dt is None:
        dt = datetime.now(timezone.utc)

    return os.path.join(
        ARCHIVE_FOLDER,
        dt.strftime("%Y-%m") + ".json"
    )


# =========================================================
# UPDATE ARCHIVE
# =========================================================

def update_archive(all_articles):

    os.makedirs(
        ARCHIVE_FOLDER,
        exist_ok=True
    )

    grouped = {}

    for article in all_articles:

        article = normalize_article(
            article
        )

        file_path = archive_file_for(
            article
        )

        if file_path not in grouped:

            grouped[file_path] = {}

        key = article_key(
            article
        )

        grouped[file_path][key] = article

    for file_path, articles in grouped.items():

        existing = {}

        if os.path.exists(
            file_path
        ):

            try:

                with open(
                    file_path,
                    "r",
                    encoding="utf-8"
                ) as file:

                    old_data = json.load(
                        file
                    )

                for article in old_data.get(
                    "items",
                    []
                ):

                    key = article_key(
                        article
                    )

                    existing[key] = article

            except Exception as e:

                print(
                    f"Could not read archive {file_path}: {e}"
                )

        existing.update(
            articles
        )

        final_items = list(
            existing.values()
        )

        final_items.sort(
            key=lambda x: parse_published_datetime(x.get("published")) or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True
        )

        archive_data = {

            "updated":
                datetime.now(
                    timezone.utc
                ).isoformat(),

            "items":
                final_items
        }

        with open(
            file_path,
            "w",
            encoding="utf-8"
        ) as file:

            json.dump(
                archive_data,
                file,
                ensure_ascii=False,
                indent=2
            )

        print(
            f"Archive updated: {file_path}"
        )


# =========================================================
# UPDATE ARCHIVE INDEX
# =========================================================

def update_archive_index():

    os.makedirs(
        ARCHIVE_FOLDER,
        exist_ok=True
    )

    months = []

    for filename in os.listdir(
        ARCHIVE_FOLDER
    ):

        if not filename.endswith(
            ".json"
        ):

            continue

        if filename == "index.json":

            continue

        month = filename[:-5]

        try:

            datetime.strptime(
                month,
                "%Y-%m"
            )

            months.append(
                month
            )

        except ValueError:

            continue

    months = sorted(
        set(months),
        reverse=True
    )

    archive_index = {

        "updated":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "months":
            months
    }

    with open(
        ARCHIVE_INDEX_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            archive_index,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(
        f"Archive index updated: {len(months)} month(s)"
    )


# =========================================================
# BUILD CURRENT NEWS
# =========================================================

def build_current_news(all_articles):

    now = datetime.now(
        timezone.utc
    )

    current = []

    seen = set()

    for article in all_articles:

        published = article.get(
            "published",
            ""
        )

        keep = True
        published_dt = parse_published_datetime(published)

        if published_dt is not None:
            age = now - published_dt

            if age > timedelta(days=CURRENT_NEWS_DAYS):
                keep = False

            # Do not publish items dated more than a day in the future.
            if age < -timedelta(days=1):
                keep = False

        if not keep:
            continue

        key = article_key(
            article
        )

        if key in seen:
            continue

        seen.add(
            key
        )

        current.append(
            article
        )

    current.sort(
        key=lambda x: parse_published_datetime(x.get("published")) or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True
    )

    return current


# =========================================================
# MAIN
# =========================================================

def main():

    print(
        "Starting African Security Observer update..."
    )

    old_news = load_existing_news()

    print(
        f"Existing current news: {len(old_news)}"
    )

    new_news = get_news()

    print(
        f"Fresh relevant news found: {len(new_news)}"
    )

    combined = {}

    for article in old_news:

        title_for_check = (
            article.get(
                "original_title"
            )
            or article.get(
                "title",
                ""
            )
        )

        description_for_check = (
            article.get(
                "original_description"
            )
            or article.get(
                "description",
                ""
            )
        )

        if not is_relevant_security_news(
            title_for_check,
            description_for_check
        ):

            continue

        article = normalize_article(
            article
        )

        # ترجمة الأخبار القديمة إذا كانت لا تزال بالإنجليزية
        if (
            not article.get(
                "original_title"
            )
            or not contains_arabic(
                article.get(
                    "title",
                    ""
                )
            )
        ):

            original_title = (
                article.get(
                    "original_title"
                )
                or article.get(
                    "title",
                    ""
                )
            )

            original_description = (
                article.get(
                    "original_description"
                )
                or article.get(
                    "description",
                    ""
                )
            )

            arabic_title, arabic_description = (
                translate_article(
                    original_title,
                    original_description
                )
            )

            article["original_title"] = (
                original_title
            )

            article["original_description"] = (
                original_description
            )

            article["title"] = (
                arabic_title
            )

            article["description"] = (
                arabic_description
            )

        key = article_key(
            article
        )

        combined[key] = article

    for article in new_news:

        key = article_key(
            article
        )

        combined[key] = normalize_article(
            article
        )

    all_articles = list(
        combined.values()
    )

    update_archive(
        all_articles
    )

    update_archive_index()

    current_news = build_current_news(
        all_articles
    )

    news_data = {

        "updated":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "items":
            current_news
    }

    with open(
        NEWS_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            news_data,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(
        f"Current news saved: {len(current_news)}"
    )

    print(
        "African Security Observer update completed successfully."
    )


# =========================================================
# START PROGRAM
# =========================================================

if __name__ == "__main__":
    main()
