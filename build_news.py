"""Fetch public RSS metadata and build a small JSON feed for the static site.
Only titles, feed descriptions, dates, source names and canonical links are stored.
"""
import json, re, hashlib
from datetime import datetime, timezone
from pathlib import Path
import feedparser

ROOT = Path(__file__).parent
FEEDS = json.loads((ROOT / 'config/feeds.json').read_text(encoding='utf-8'))
OUT = ROOT / 'data/news.json'

KEYWORDS = {
    'defense': ['defense', 'military', 'army', 'air force', 'navy', 'arms', 'weapon', 'drone', 'missile', 'defence', 'sécurité', 'armée', 'militaire', 'défense', 'armement'],
    'sahel': ['mali', 'niger', 'burkina', 'sahel', 'chad', 'tchad', 'mauritania', 'mauritanie'],
    'north': ['libya', 'tunisia', 'algeria', 'morocco', 'egypt', 'libye', 'tunisie', 'algérie', 'maroc', 'égypte'],
    'rest': ['africa', 'african', 'sudan', 'somalia', 'ethiopia', 'congo', 'mozambique', 'soudan', 'somalie', 'éthiopie']
}

def clean(s):
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', str(s or ''))).strip()

def classify(text):
    t = text.casefold()
    matches = {k: sum(1 for word in words if word in t) for k, words in KEYWORDS.items()}
    # Prioritize geographic categories; keep broader defense stories in defense.
    best = max(matches, key=matches.get)
    return best if matches[best] else 'rest'

items, seen = [], set()
for feed in FEEDS:
    if not feed.get('enabled'):
        continue
    try:
        parsed = feedparser.parse(feed['url'])
        for entry in parsed.entries[:40]:
            title = clean(entry.get('title'))
            link = entry.get('link', '').strip()
            summary = clean(entry.get('summary') or entry.get('description'))
            if not title or not link:
                continue
            key = hashlib.sha256((title.casefold() + link).encode()).hexdigest()
            if key in seen:
                continue
            seen.add(key)
            published = entry.get('published') or entry.get('updated') or ''
            category = classify(title + ' ' + summary)
            items.append({
                'id': key[:16], 'title': title, 'summary': summary[:500],
                'url': link, 'source': feed['name'], 'published': published,
                'category': category
            })
    except Exception as exc:
        print(f"Feed failed: {feed.get('name')}: {exc}")

items.sort(key=lambda x: x['published'], reverse=True)
OUT.parent.mkdir(exist_ok=True)
OUT.write_text(json.dumps({'updated': datetime.now(timezone.utc).isoformat(), 'items': items[:150]}, ensure_ascii=False, indent=2), encoding='utf-8')
print(f'Wrote {min(len(items),150)} items')
