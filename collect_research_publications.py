import json
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from html import unescape

import requests

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "research_publications.json"
TZ = ZoneInfo("Africa/Tunis")
# Initial backfill begins on 1 October 2026. Subsequent scheduled runs
# add newly published items while retaining the existing list for up to 30 days.
SEED_START_DATE = "2026-10-01"
MAX_AGE_DAYS = 30
MAX_PER_CENTER = 30
MAX_TOTAL = 800
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AfricanSecurityObserver/1.0)"}

# Direct, source-specific searches: this file records publications and links only.
CENTERS = [
    {"name": "المعهد الدولي للدراسات الاستراتيجية (IISS)", "short": "IISS", "domain": "iiss.org", "query": "Africa OR Sahel OR security OR military OR defence OR defense OR strategy OR geopolitics"},
    {"name": "مركز الدراسات الاستراتيجية والدولية (CSIS)", "short": "CSIS", "domain": "csis.org", "query": "Africa OR Sahel OR security OR military OR defence OR defense OR strategy OR geopolitics"},
    {"name": "تشاتام هاوس", "short": "Chatham House", "domain": "chathamhouse.org", "query": "Africa OR Sahel OR security OR conflict OR defence OR defense OR geopolitics"},
    {"name": "معهد ستوكهولم الدولي لأبحاث السلام (SIPRI)", "short": "SIPRI", "domain": "sipri.org", "query": "Africa OR arms transfers OR military spending OR weapons OR conflict OR security"},
    {"name": "مؤسسة RAND", "short": "RAND", "domain": "rand.org", "query": "Africa OR national security OR military strategy OR defense OR defence OR conflict"},
    {"name": "معهد بروكينغز", "short": "Brookings", "domain": "brookings.edu", "query": "Africa OR security OR foreign policy OR military OR conflict OR strategy"},
    {"name": "المجلس الأطلسي", "short": "Atlantic Council", "domain": "atlanticcouncil.org", "query": "Africa OR Sahel OR defense OR security OR geopolitics OR military"},
    {"name": "المعهد الملكي للخدمات المتحدة (RUSI)", "short": "RUSI", "domain": "rusi.org", "query": "Africa OR military OR defence OR defense OR security OR strategy OR conflict"},
    {"name": "مؤسسة كارنيغي للسلام الدولي", "short": "Carnegie Endowment", "domain": "carnegieendowment.org", "query": "Africa OR security OR foreign policy OR military OR geopolitics"},
    {"name": "مركز كارنيغي للشرق الأوسط", "short": "Carnegie Middle East", "domain": "carnegie-mec.org", "query": "security OR defense OR military OR Middle East OR North Africa OR Libya"},
    {"name": "مجموعة الأزمات الدولية", "short": "International Crisis Group", "domain": "crisisgroup.org", "query": "Africa OR Sahel OR Libya OR Sudan OR conflict OR security"},
    {"name": "المجلس الأوروبي للعلاقات الخارجية (ECFR)", "short": "ECFR", "domain": "ecfr.eu", "query": "Africa OR security OR defense OR defence OR geopolitics OR Middle East"},
    {"name": "معهد ستيمسون", "short": "Stimson Center", "domain": "stimson.org", "query": "Africa OR security OR defense OR arms control OR military OR geopolitics"},
    {"name": "مركز ويلسون", "short": "Wilson Center", "domain": "wilsoncenter.org", "query": "Africa OR security OR military OR defense OR Middle East OR geopolitics"},
    {"name": "مجلس العلاقات الخارجية", "short": "CFR", "domain": "cfr.org", "query": "Africa OR security OR military OR foreign policy OR conflict OR defense"},
    {"name": "مؤسسة هيريتيج", "short": "Heritage Foundation", "domain": "heritage.org", "query": "national security OR military OR defense OR Africa OR Middle East"},
    {"name": "معهد هدسون", "short": "Hudson Institute", "domain": "hudson.org", "query": "military OR defense OR security OR Africa OR Middle East OR geopolitics"},
    {"name": "صندوق مارشال الألماني (GMF)", "short": "GMF", "domain": "gmfus.org", "query": "Africa OR security OR defense OR geopolitics OR NATO OR military"},
    {"name": "المعهد الألماني للشؤون الدولية والأمنية (SWP)", "short": "SWP", "domain": "swp-berlin.org", "query": "Africa OR security OR defense OR military OR geopolitics"},
    {"name": "معهد كلينغندايل", "short": "Clingendael", "domain": "clingendael.org", "query": "Africa OR Sahel OR security OR defense OR conflict"},
    {"name": "المعهد الفرنسي للعلاقات الدولية (IFRI)", "short": "IFRI", "domain": "ifri.org", "query": "Africa OR sécurité OR défense OR militaire OR géopolitique"},
    {"name": "معهد العلاقات الدولية والاستراتيجية (IRIS)", "short": "IRIS", "domain": "iris-france.org", "query": "Afrique OR sécurité OR défense OR géopolitique OR militaire"},
    {"name": "المعهد الملكي إلكانو", "short": "Elcano", "domain": "realinstitutoelcano.org", "query": "Africa OR security OR defense OR geopolitics OR military"},
    {"name": "المعهد الفنلندي للشؤون الدولية (FIIA)", "short": "FIIA", "domain": "fiia.fi", "query": "security OR defense OR NATO OR geopolitics OR military"},
    {"name": "معهد لوي", "short": "Lowy Institute", "domain": "lowyinstitute.org", "query": "Indo-Pacific OR security OR defense OR military OR geopolitics"},
    {"name": "المعهد الأسترالي للسياسة الاستراتيجية (ASPI)", "short": "ASPI", "domain": "aspistrategist.org.au", "query": "defence OR defense OR military OR security OR Indo-Pacific"},
    {"name": "مؤسسة أبحاث المراقب (ORF)", "short": "ORF", "domain": "orfonline.org", "query": "security OR defense OR military OR Africa OR geopolitics"},
    {"name": "معهد الدراسات الأمنية الإفريقي (ISS Africa)", "short": "ISS Africa", "domain": "issafrica.org", "query": "Africa OR Sahel OR security OR conflict OR military"},
    {"name": "مركز إفريقيا للدراسات الاستراتيجية", "short": "Africa Center for Strategic Studies", "domain": "africacenter.org", "query": "Africa OR security OR military OR Sahel OR conflict"},
    {"name": "معهد الدراسات الدفاعية والاستراتيجية الهندي (MP-IDSA)", "short": "MP-IDSA", "domain": "idsa.in", "query": "defence OR defense OR security OR military OR Africa OR geopolitics"},
    {"name": "المعهد الوطني الياباني للدراسات الدفاعية (NIDS)", "short": "NIDS", "domain": "nids.mod.go.jp", "query": "security OR defense OR military OR Indo-Pacific"},
    {"name": "المعهد الكوري للتحليلات الدفاعية (KIDA)", "short": "KIDA", "domain": "kida.re.kr", "query": "security OR defense OR military OR strategy"},
    {"name": "المكتب الوطني للأبحاث الآسيوية (NBR)", "short": "NBR", "domain": "nbr.org", "query": "Asia OR security OR defense OR military OR geopolitics"},
    {"name": "معهد أبحاث الدفاع والأمن الوطني التايواني (INDSR)", "short": "INDSR", "domain": "indsr.org.tw", "query": "defense OR security OR military OR strategy OR Indo-Pacific"},
]

def clean(value):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", str(value or "")))).strip()

def parse_date(value):
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(TZ)
    except Exception:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(TZ)
        except Exception:
            return None

def direct_url(url):
    if not url:
        return ""
    try:
        response = requests.get(url, timeout=10, headers=HEADERS, allow_redirects=True, stream=True)
        result = response.url if response.url.startswith(("https://", "http://")) else url
        response.close()
        # Avoid publishing a Google News wrapper when it did not resolve.
        return result
    except Exception:
        return url

def main():
    now = datetime.now(TZ)
    rolling_cutoff = now - timedelta(days=MAX_AGE_DAYS)
    seed_start = datetime.strptime(SEED_START_DATE, "%Y-%m-%d").replace(tzinfo=TZ)
    cutoff = max(rolling_cutoff, seed_start)

    # Preserve previously collected items so a six-hour run adds new links
    # instead of replacing the whole list with a smaller search result.
    existing_items = []
    if OUTPUT.exists():
        try:
            old_data = json.loads(OUTPUT.read_text(encoding="utf-8"))
            old_items = old_data.get("publications", []) if isinstance(old_data, dict) else old_data
            if isinstance(old_items, list):
                existing_items = [item for item in old_items if isinstance(item, dict)]
        except Exception as exc:
            print(f"Could not read prior publication list: {type(exc).__name__}")

    publications = []
    seen = set()
    session = requests.Session()
    session.headers.update(HEADERS)

    for center in CENTERS:
        # Google News RSS supports after: dates; this gives the initial
        # 1–13 October backfill and then includes newer posts on every run.
        after_date = (cutoff.date() - timedelta(days=1)).isoformat()
        query = f"site:{center['domain']} ({center['query']}) after:{after_date}"
        params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
        try:
            response = session.get("https://news.google.com/rss/search", params=params, timeout=22)
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except Exception as exc:
            print(f"Search failed for {center['short']}: {type(exc).__name__}")
            continue

        center_items = []
        for entry in root.findall("./channel/item"):
            title = clean(entry.findtext("title"))
            wrapper = clean(entry.findtext("link"))
            published_raw = clean(entry.findtext("pubDate"))
            published_dt = parse_date(published_raw)
            if not title or not wrapper:
                continue
            if published_dt and published_dt < cutoff:
                continue
            url = direct_url(wrapper)
            if not url or url in seen:
                continue
            source_node = entry.find("source")
            source_name = clean(source_node.text if source_node is not None else center["short"])
            description = clean(entry.findtext("description"))
            item = {
                "center": center["name"],
                "center_short": center["short"],
                "title": title,
                "url": url,
                "published": published_dt.isoformat(timespec="minutes") if published_dt else published_raw,
                "date": published_dt.strftime("%Y-%m-%d") if published_dt else "",
                "source": source_name,
                "description": description[:500],
                "center_site": "https://" + center["domain"] + "/"
            }
            center_items.append(item)

        center_items.sort(key=lambda x: x.get("published", ""), reverse=True)
        for item in center_items[:MAX_PER_CENTER]:
            key = item["url"]
            if key in seen:
                continue
            seen.add(key)
            publications.append(item)
        print(f"{center['short']}: {min(len(center_items), MAX_PER_CENTER)} publications")

    # Merge the existing list after fresh results, deduplicate by URL, and
    # retain the newest item for each URL. This prevents six-hour refreshes
    # from deleting valid older entries that Google News no longer returns.
    merged = {}
    for item in existing_items + publications:
        url = str(item.get("url", "")).strip()
        if not url:
            continue
        published_dt = parse_date(item.get("published") or item.get("date"))
        if published_dt and published_dt < cutoff:
            continue
        if url not in merged:
            merged[url] = item
        else:
            old_dt = parse_date(merged[url].get("published") or merged[url].get("date"))
            if published_dt and (not old_dt or published_dt > old_dt):
                merged[url] = item

    publications = list(merged.values())
    publications.sort(
        key=lambda x: parse_date(x.get("published") or x.get("date")) or cutoff,
        reverse=True
    )
    publications = publications[:MAX_TOTAL]
    payload = {
        "updated": now.isoformat(timespec="minutes"),
        "window_days": MAX_AGE_DAYS,
        "seed_start_date": SEED_START_DATE,
        "count": len(publications),
        "publications": publications
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Saved {len(publications)} research publications to {OUTPUT.name}")

if __name__ == "__main__":
    main()
