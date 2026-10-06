import feedparser
import json
from datetime import datetime, timezone, timedelta
from urllib.parse import quote_plus
from googlenewsdecoder import gnewsdecoder


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
    "Equatorial Guinea",
    "Eritrea",
    "Eswatini",
    "Lesotho",
    "Madagascar",
    "Mauritius",
    "Seychelles",
    "Comoros"
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
    "IED"
]


# =========================================================
# GOOGLE NEWS RSS URL
# =========================================================

def make_country_feed(country):

    # Every important search term contains the country name.
    # This prevents OR from bringing unrelated countries.

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

    # Limit Google News search to recent stories.
    query = f"({query}) when:2d"

    encoded_query = quote_plus(query)

    return (
        "https://news.google.com/rss/search?q="
        + encoded_query
        + "&hl=en-US&gl=US&ceid=US:en"
    )


# =========================================================
# BUILD RSS FEEDS
# =========================================================

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


# =========================================================
# DECODE GOOGLE NEWS URL
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
# CLASSIFICATION
# =========================================================

def classify_news(title, feed_category):

    text = title.lower()


    # -----------------------------------------------------
    # NORTH AFRICA
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # SAHEL
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # DEFENSE / ARMAMENT
    # -----------------------------------------------------

    defense_words = [

        "weapon",
        "weapons",

        "missile",
        "missiles",

        "rocket",
        "rockets",

        "drone",
        "drones",
        "uav",

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
        "armaments"
    ]


    # -----------------------------------------------------
    # GEOGRAPHICAL PRIORITY
    #
    # If an article is about Mali, for example, it goes
    # to Sahel even if the title also contains "military"
    # or "weapons".
    # -----------------------------------------------------

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


    # -----------------------------------------------------
    # DEFENSE
    # -----------------------------------------------------

    if any(
        word in text
        for word in defense_words
    ):

        return "defense"


    # -----------------------------------------------------
    # FALLBACK
    # -----------------------------------------------------

    if feed_category == "north":
        return "north"

    if feed_category == "sahel":
        return "sahel"

    if feed_category == "defense":
        return "defense"

    return "rest"


# =========================================================
# GET NEWS
# =========================================================

def get_news():

    items = []

    seen_urls = set()

    seen_titles = set()


    for feed_category, feeds in RSS_FEEDS.items():

        for feed_url in feeds:

            print(
                f"Reading feed: {feed_url}"
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


                # -------------------------------------------------
                # Decode original article URL
                # -------------------------------------------------

                article_url = decode_url(
                    google_url
                )


                # -------------------------------------------------
                # Remove duplicate URL
                # -------------------------------------------------

                if article_url in seen_urls:
                    continue


                # -------------------------------------------------
                # Remove duplicate title
                # -------------------------------------------------

                normalized_title = (
                    title.lower()
                    .replace(" ", "")
                )


                if normalized_title in seen_titles:
                    continue


                seen_urls.add(
                    article_url
                )

                seen_titles.add(
                    normalized_title
                )


                # -------------------------------------------------
                # Source
                # -------------------------------------------------

                source = entry.get(
                    "source",
                    {}
                ).get(
                    "title",
                    "مصدر غير معروف"
                )


                # -------------------------------------------------
                # Publication date
                # -------------------------------------------------

                published = entry.get(
                    "published",
                    ""
                )


                # -------------------------------------------------
                # Ignore news older than 48 hours
                # -------------------------------------------------

                if published:

                    try:

                        published_dt = datetime.strptime(
                            published,
                            "%a, %d %b %Y %H:%M:%S %Z"
                        ).replace(
                            tzinfo=timezone.utc
                        )


                        if (
                            datetime.now(
                                timezone.utc
                            )
                            - published_dt
                            > timedelta(
                                hours=MAX_NEWS_AGE_HOURS
                            )
                        ):

                            continue


                    except ValueError:

                        pass


                # -------------------------------------------------
                # Classify
                # -------------------------------------------------

                category = classify_news(
                    title,
                    feed_category
                )


                # -------------------------------------------------
                # Save article
                # -------------------------------------------------

                items.append({

                    "title": title,

                    "url": article_url,

                    "source": source,

                    "published": published,

                    "category": category

                })


    # =========================================================
    # SORT NEWEST FIRST
    # =========================================================

    items.sort(
        key=lambda x: x.get(
            "published",
            ""
        ),
        reverse=True
    )


    return items


# =========================================================
# MAIN
# =========================================================

def main():

    items = get_news()


    data = {

        "updated":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "items":
            items

    }


    with open(
        "news.json",
        "w",
        encoding="utf-8"
    ) as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )


    print(
        f"Updated {len(items)} news items."
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
