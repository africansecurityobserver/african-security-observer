import os
import json
import re
from datetime import datetime

ARTICLES_FOLDER = "articles"
OUTPUT_FILE = "articles.json"


def parse_front_matter(text):
    data = {}

    if not text.startswith("---"):
        return data

    parts = text.split("---", 2)

    if len(parts) < 3:
        return data

    front_matter = parts[1]

    for line in front_matter.splitlines():
        if ":" not in line:
            continue

        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip()

        data[key] = value

    return data


def clean_value(value):
    value = value.strip()

    if len(value) >= 2:
        if (value[0] == '"' and value[-1] == '"') or (
            value[0] == "'" and value[-1] == "'"
        ):
            value = value[1:-1]

    return value


def slugify(text):
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\u0600-\u06ff\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    text = re.sub(r"-+", "-", text)

    return text.strip("-") or "article"


def parse_article(filepath):
    with open(filepath, "r", encoding="utf-8") as file:
        content = file.read()

    if not content.startswith("---"):
        return None

    parts = content.split("---", 2)

    if len(parts) < 3:
        return None

    front_matter = parse_front_matter(content)
    body = parts[2].strip()

    title = clean_value(front_matter.get("title", ""))
    image = clean_value(front_matter.get("image", ""))
    author = clean_value(front_matter.get("author", ""))
    date = clean_value(front_matter.get("date", ""))

    if not title:
        return None

    filename = os.path.basename(filepath)
    slug = os.path.splitext(filename)[0]

    return {
        "title": title,
        "image": image,
        "author": author,
        "date": date,
        "slug": slug,
        "body": body
    }


def main():
    articles = []

    if not os.path.isdir(ARTICLES_FOLDER):
        os.makedirs(ARTICLES_FOLDER, exist_ok=True)

    for filename in os.listdir(ARTICLES_FOLDER):
        if not filename.endswith(".md"):
            continue

        filepath = os.path.join(ARTICLES_FOLDER, filename)

        article = parse_article(filepath)

        if article:
            articles.append(article)

    articles.sort(
        key=lambda article: article.get("date", ""),
        reverse=True
    )

    with open(OUTPUT_FILE, "w", encoding="utf-8") as file:
        json.dump(
            articles,
            file,
            ensure_ascii=False,
            indent=2
        )

    print(f"Articles generated: {len(articles)}")


if __name__ == "__main__":
    main()
