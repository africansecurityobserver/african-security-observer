import feedparser
import json
from datetime import datetime, timezone

RSS_FEEDS = {
    "sahel": [
        "https://news.google.com/rss/search?q=Sahel+security+Africa&hl=en-US&gl=US&ceid=US:en"
    ],
    "north": [
        "https://news.google.com/rss/search?q=North+Africa+security&hl=en-US&gl=US&ceid=US:en"
    ],
    "rest": [
        "https://news.google.com/rss/search?q=Africa+military+security&hl=en-US&gl=US&ceid=US:en"
    ],
    "defense": [
        "https://news.google.com/rss/search?q=Africa+defense+military+weapons&hl=en-US&gl=US&ceid=US:en"
    ]
}

MAX_NEWS_PER_CATEGORY = 30


def get_news():
    items = []

    for category, feeds in RSS_FEEDS.items():
        for feed_url in feeds:
            feed = feedparser.parse(feed_url)

            for entry in feed.entries[:MAX_NEWS_PER_CATEGORY]:
                items.append({
                    "title": entry.get("title", "").strip(),
                    "url": entry.get("link", ""),
                    "source": entry.get("source", {}).get(
                        "title", "Google News"
                    ),
                    "published": entry.get("published", ""),
                    "category": category
                })

    return items


def main():
    items = get_news()

    data = {
        "updated": datetime.now(timezone.utc).isoformat(),
        "items": items
    }

    with open("news.json", "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    print(f"Updated {len(items)} news items.")


if __name__ == "__main__":
    main()
