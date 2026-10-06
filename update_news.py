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


def classify_news(title):
    """Basic automatic classification."""

    text = title.lower()

    defense_words = [
        "weapon", "weapons", "missile", "missiles",
        "drone", "drones", "aircraft", "fighter",
        "tank", "tanks", "submarine", "frigate",
        "military equipment", "defense", "defence",
        "arms", "armament", "navy", "air force",
        "army", "military"
    ]

    north_words = [
        "tunisia", "algeria", "morocco",
        "libya", "egypt", "mauritania",
        "north africa", "maghreb"
    ]

    sahel_words = [
        "mali", "burkina faso", "niger",
        "chad", "mauritania", "sahel",
        "senegal", "guinea", "ivory coast",
        "cote d'ivoire"
    ]

    if any(word in text for word in defense_words):
        return "defense"

    if any(word in text for word in north_words):
        return "north"

    if any(word in text for word in sahel_words):
        return "sahel"

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

                # Ignore news older than 48 hours
                if published:
                    try:
                        published_dt = datetime.strptime(
                            published,
                            "%a, %d %b %Y %H:%M:%S %Z"
                        ).replace(tzinfo=timezone.utc)

                        if datetime.now(timezone.utc) - published_dt > timedelta(hours=MAX_NEWS_AGE_HOURS):
                            continue

                    except ValueError:
                        pass

                category = classify_news(title)

                items.append({
                    "title": title,
                    "url": article_url,
                    "source": source,
                    "published": published,
                    "category": category
                })

    # Sort news from newest to oldest
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
