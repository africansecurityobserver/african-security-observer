import feedparser
import json
from datetime import datetime, timezone

RSS_FEEDS = {
    "Sahel": [
        "https://news.google.com/rss/search?q=Sahel+security+Africa&hl=en-US&gl=US&ceid=US:en"
    ],
    "North Africa": [
        "https://news.google.com/rss/search?q=North+Africa+security&hl=en-US&gl=US&ceid=US:en"
    ],
    "Africa": [
        "https://news.google.com/rss/search?q=Africa+military+security&hl=en-US&gl=US&ceid=US:en"
    ]
}

MAX_NEWS_PER_SECTION = 10


def get_news():
    news = []

    for category, feeds in RSS_FEEDS.items():
        for feed_url in feeds:
            feed = feedparser.parse(feed_url)

            for entry in feed.entries[:MAX_NEWS_PER_SECTION]:
                news.append({
                    "title": entry.get("title", "").strip(),
                    "link": entry.get("link", ""),
                    "source": entry.get("source", {}).get("title", "Google News"),
                    "published": entry.get("published", ""),
                    "category": category
                })

    return news


def main():
    news = get_news()

    data = {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "articles": news
    }

    with open("news.json", "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)

    print(f"Updated {len(news)} articles.")


if __name__ == "__main__":
    main()
