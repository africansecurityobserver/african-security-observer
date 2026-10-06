import feedparser
import json
from datetime import datetime, timezone, timedelta
from googlenewsdecoder import gnewsdecoder


RSS_FEEDS = {
    "sahel": [
        "https://news.google.com/rss/search?q=Sahel+Africa+security+military&hl=en-US&gl=US&ceid=US:en"
    ],
    "north": [
        "https://news.google.com/rss/search?q=North+Africa+security+military&hl=en-US&gl=US&ceid=US:en"
    ],
    "defense": [
        "https://news.google.com/rss/search?q=Africa+defense+military+weapons&hl=en-US&gl=US&ceid=US:en"
    ],
    "rest": [
        "https://news.google.com/rss/search?q=Africa+security+military&hl=en-US&gl=US&ceid=US:en"
    ]
}


MAX_NEWS_PER_CATEGORY = 30
MAX_NEWS_AGE_HOURS = 48


def decode_url(url):
    """Convert Google News URL into the original publisher URL."""

    if not url or "news.google.com" not in url:
        return url

    try:
        result = gnewsdecoder(
            url,
            interval=0.5,
            timeout=15
        )

        if result.get("success"):
            return result.get("decoded_url", url)

    except Exception as e:
        print(f"Could not decode URL: {e}")

    return url


def classify_news(title, feed_category):
    """
    Automatic news classification.

    Priority:
    1. Specific African country/region
    2. Genuine defense/armament news
    3. General African security news
    """

    text = title.lower()

    # ---------------------------------------------------------
    # 1. NORTH AFRICA
    # ---------------------------------------------------------

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

    # ---------------------------------------------------------
    # 2. SAHEL
    # ---------------------------------------------------------

    sahel_words = [
        "mali",
        "malian",
        "burkina faso",
        "burkinabe",
        "niger",
        "nigerien",
        "chad",
        "chadian",
        "mauritania",
        "mauritanian",
        "sahel",
        "senegal",
        "senegalese",
        "guinea",
        "guinean",
        "ivory coast",
        "cote d'ivoire",
        "côte d'ivoire"
    ]

    # ---------------------------------------------------------
    # 3. TRUE DEFENSE / ARMAMENT NEWS
    # ---------------------------------------------------------

    defense_words = [
        "weapons",
        "weapon",
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
        "aircraft",
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

    # ---------------------------------------------------------
    # IMPORTANT:
    # Regional classification comes BEFORE defense classification.
    # This prevents "Mali + military" from going to defense.
    # ---------------------------------------------------------

    if any(word in text for word in north_words):
        return "north"

    if any(word in text for word in sahel_words):
        return "sahel"

    # Only after checking geographical regions,
    # classify genuine armament/defense stories.
    if any(word in text for word in defense_words):
        return "defense"

    # ---------------------------------------------------------
    # 4. USE THE RSS CATEGORY AS A FALLBACK
    # ---------------------------------------------------------

    if feed_category == "north":
        return "north"

    if feed_category == "sahel":
        return "sahel"

    if feed_category == "defense":
        return "defense"

    # ---------------------------------------------------------
    # 5. REST OF AFRICA
    # ---------------------------------------------------------

    return "rest"


def get_news():

    items = []
    seen_urls = set()

    for feed_category, feeds in RSS_FEEDS.items():

        for feed_url in feeds:

            print(f"Reading feed: {feed_url}")

            feed = feedparser.parse(feed_url)

            for entry in feed.entries[:MAX_NEWS_PER_CATEGORY]:

                title = entry.get("title", "").strip()
                google_url = entry.get("link", "").strip()

                if not title or not google_url:
                    continue

                # Convert Google News URL to original article URL
                article_url = decode_url(google_url)

                # Avoid duplicates
                if article_url in seen_urls:
                    continue

                seen_urls.add(article_url)

                source = entry.get("source", {}).get(
                    "title",
                    "مصدر غير معروف"
                )

                published = entry.get("published", "")

                # -------------------------------------------------
                # Ignore news older than 48 hours
                # -------------------------------------------------

                if published:
                    try:
                        published_dt = datetime.strptime(
                            published,
                            "%a, %d %b %Y %H:%M:%S %Z"
                        ).replace(tzinfo=timezone.utc)

                        if (
                            datetime.now(timezone.utc) - published_dt
                            > timedelta(hours=MAX_NEWS_AGE_HOURS)
                        ):
                            continue

                    except ValueError:
                        pass

                # -------------------------------------------------
                # Classify news
                # -------------------------------------------------

                category = classify_news(
                    title,
                    feed_category
                )

                items.append({
                    "title": title,
                    "url": article_url,
                    "source": source,
                    "published": published,
                    "category": category
                })

    # ---------------------------------------------------------
    # Sort news from newest to oldest
    # ---------------------------------------------------------

    items.sort(
        key=lambda x: x.get("published", ""),
        reverse=True
    )

    return items


def main():

    items = get_news()

    data = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "items": items
    }

    with open("news.json", "w", encoding="utf-8") as file:

        json.dump(
            data,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(f"Updated {len(items)} news items.")


if __name__ == "__main__":
    main()
