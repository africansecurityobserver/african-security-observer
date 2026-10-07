import feedparser
import json
import os
import re
import time
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
from googlenewsdecoder import gnewsdecoder
from deep_translator import GoogleTranslator


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
    "military operations"
]


# =========================================================
# DEFENSE TERMS
# =========================================================

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


# =========================================================
# WORDS TO EXCLUDE
# =========================================================

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


RSS_FEEDS = {

    "sahel": [
        make_country_feed(country)
        for country in SAHEL_COUNTRIES
    ],

    "north": [
        make_country_feed(country)
        for country in NORTH_AFRICA_COUNTRIES
    ],

    "rest": [
        make_country_feed(country)
        for country in REST_OF_AFRICA_COUNTRIES
    ],

    "defense": [

        "https://news.google.com/rss/search?q="
        + quote_plus(
            '(Africa weapons OR Africa missiles OR Africa drones '
            'OR Africa fighter jets OR Africa aircraft '
            'OR Africa tanks OR Africa submarines '
            'OR Africa frigates OR Africa warships '
            'OR Africa arms deal OR Africa arms contract '
            'OR Africa military procurement) when:2d'
        )
        + "&hl=en-US&gl=US&ceid=US:en",

        "https://news.google.com/rss/search?q="
        + quote_plus(
            '("African military" OR "African army") '
            '(weapons OR drones OR missiles OR aircraft '
            'OR tanks OR ships OR arms deal) when:2d'
        )
        + "&hl=en-US&gl=US&ceid=US:en"
    ]
}


# =========================================================
# SETTINGS
# =========================================================

MAX_NEWS_PER_FEED = 10

MAX_NEWS_AGE_HOURS = 48

CURRENT_NEWS_DAYS = 7

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


def translate_text(text):

    if not text:
        return ""

    text = clean_html(text)

    if not text:
        return ""

    # لا نرسل نصوصًا ضخمة إلى خدمة الترجمة
    text = text[:5000]

    try:

        translated = translator.translate(
            text
        )

        if translated:
            return translated.strip()

    except Exception as e:

        print(
            f"Translation failed: {e}"
        )

    # في حالة فشل الترجمة نحتفظ بالنص الأصلي
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

def is_relevant_security_news(
    title,
    description
):

    text = (
        str(title)
        + " "
        + str(description)
    ).lower()

    # -----------------------------------------------------
    # Reject clearly unrelated subjects
    # -----------------------------------------------------

    for word in EXCLUDED_TERMS:

        if word.lower() in text:
            return False

    # -----------------------------------------------------
    # Accept defense stories
    # -----------------------------------------------------

    if any(
        word.lower() in text
        for word in DEFENSE_TERMS
    ):
        return True

    # -----------------------------------------------------
    # Accept security stories
    # -----------------------------------------------------

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
        "maghreb"
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
        "sahel"
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

    if feed_category == "north":
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

            feed = feedparser.parse(
                feed_url
            )

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

                # -------------------------------------------------
                # SECURITY FILTER
                # -------------------------------------------------

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

                    try:

                        published_dt = datetime.strptime(
                            published,
                            "%a, %d %b %Y %H:%M:%S %Z"
                        ).replace(
                            tzinfo=timezone.utc
                        )

                        age = (
                            datetime.now(
                                timezone.utc
                            )
                            - published_dt
                        )

                        if age > timedelta(
                            hours=MAX_NEWS_AGE_HOURS
                        ):
                            continue

                    except ValueError:

                        pass

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

                # -------------------------------------------------
                # TRANSLATION
                # -------------------------------------------------

                arabic_title, arabic_description = (
                    translate_article(
                        title,
                        description
                    )
                )

                items.append({

                    # Arabic content shown on newspaper
                    "title": arabic_title,

                    "description": arabic_description,

                    # Original content kept for reference
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
        key=lambda x: x.get(
            "published",
            ""
        ),
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

    try:

        dt = datetime.strptime(
            published,
            "%a, %d %b %Y %H:%M:%S %Z"
        )

        return os.path.join(
            ARCHIVE_FOLDER,
            dt.strftime("%Y-%m") + ".json"
        )

    except ValueError:

        return os.path.join(
            ARCHIVE_FOLDER,
            datetime.now(
                timezone.utc
            ).strftime("%Y-%m") + ".json"
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
            key=lambda x: x.get(
                "published",
                ""
            ),
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
# ========================
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

        try:

            published_dt = datetime.strptime(
                published,
                "%a, %d %b %Y %H:%M:%S %Z"
            ).replace(
                tzinfo=timezone.utc
            )

            age = now - published_dt

            if age > timedelta(
                days=CURRENT_NEWS_DAYS
            ):
                keep = False

        except ValueError:

            pass

        if not keep:
            continue

        key = article_key(
            article
        )

        if key in seen:
            continue

        seen.add(key)

        current.append(
            article
        )

    current.sort(
        key=lambda x: x.get(
            "published",
            ""
        ),
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

        key = article_key(
            article
        )

        combined[key] = normalize_article(
            article
        )

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


if __name__ == "__main__":
    main()
