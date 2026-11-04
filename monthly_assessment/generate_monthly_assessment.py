import json
import os
import re
import hashlib
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types

ROOT = Path(__file__).resolve().parent.parent
TZ = ZoneInfo("Africa/Tunis")
MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
WEEKLY_INDEX = ROOT / "weekly_assessments" / "archive" / "index.json"
ARCHIVE = ROOT / "monthly_assessments" / "archive"
INDEX = ARCHIVE / "index.json"

EXPECTED = [
    "المقدمة",
    "تونس وشمال إفريقيا",
    "بقية إفريقيا",
    "أوروبا والفضاء الأطلسي",
    "الشرق الأوسط",
    "الولايات المتحدة والقوى الدولية الأخرى",
    "الاتجاهات الاستراتيجية العابرة للأقاليم",
    "الحوصلة الاستراتيجية والسيناريوهات وانعكاساتها على تونس",
]
MONTHS = ["يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
          "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"]


def clean(value):
    return re.sub(r"\\s+", " ", str(value or "")).strip()


def target_month():
    requested = os.getenv("ASSESSMENT_MONTH", "").strip()
    if requested:
        try:
            year, month = map(int, requested.split("-"))
            return date(year, month, 1)
        except Exception as exc:
            raise RuntimeError("ASSESSMENT_MONTH must use YYYY-MM.") from exc
    now = datetime.now(TZ).date()
    first = now.replace(day=1)
    return (first - timedelta(days=1)).replace(day=1)


def month_end(first):
    if first.month == 12:
        return date(first.year + 1, 1, 1) - timedelta(days=1)
    return date(first.year, first.month + 1, 1) - timedelta(days=1)


def load_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")


def weekly_entries():
    index = load_json(WEEKLY_INDEX, {"years": []})
    entries = []
    for year in index.get("years", []):
        for month in year.get("months", []):
            for item in month.get("cards", []):
                if item.get("kind") == "weekly_assessment" and item.get("path"):
                    entries.append(item)
    return entries


def collect_reports(first, last):
    reports = []
    for entry in weekly_entries():
        path = ROOT / "weekly_assessments" / "archive" / entry["path"]
        card = load_json(path, None)
        if not isinstance(card, dict):
            print(f"Skipping unreadable weekly report: {path}")
            continue
        try:
            start = date.fromisoformat(card["period_start"])
            end = date.fromisoformat(card["period_end"])
        except Exception:
            continue
        if start <= last and end >= first:
            reports.append(card)
    reports.sort(key=lambda x: (x.get("period_start", ""), x.get("period_end", "")))
    return reports


def load_monthly_index():
    return load_json(INDEX, {"updated": "", "years": []})


def add_to_index(card):
    index = load_monthly_index()
    year, month = card["date"][:4], card["date"][5:7]
    y = next((v for v in index.get("years", []) if str(v.get("year")) == year), None)
    if y is None:
        y = {"year": year, "months": []}
        index.setdefault("years", []).append(y)
    m = next((v for v in y.get("months", []) if str(v.get("month")) == month), None)
    if m is None:
        m = {"month": month, "cards": []}
        y.setdefault("months", []).append(m)
    m["cards"] = [x for x in m.get("cards", []) if x.get("path") != card["path"]]
    m["cards"].append({
        "date": card["date"], "title": card["title"], "subject": card["subject"],
        "slot": "monthly", "path": card["path"], "kind": "monthly_assessment",
        "kind_label": "التقدير الاستراتيجي الشهري"
    })
    index["years"] = sorted(index["years"], key=lambda x: str(x["year"]), reverse=True)
    for y0 in index["years"]:
        y0["months"] = sorted(y0.get("months", []), key=lambda x: str(x["month"]), reverse=True)
        for m0 in y0["months"]:
            m0["cards"] = sorted(m0.get("cards", []), key=lambda x: x.get("date", ""), reverse=True)
    index["updated"] = datetime.now(TZ).isoformat(timespec="seconds")
    save_json(INDEX, index)


def build_prompt(first, last, reports):
    blocks = []
    for number, report in enumerate(reports, 1):
        sections = "\\n".join(
            f"- {clean(section.get('heading'))}: {str(section.get('text') or '')[:2200]}"
            for section in report.get("sections", [])
        )
        blocks.append(
            f"التقرير الأسبوعي {number}: {report.get('title')} | "
            f"الفترة: {report.get('period_start')} إلى {report.get('period_end')}\\n"
            f"الحوصلة: {str(report.get('summary') or '')[:1800]}\\n{sections}"
        )
    source_block = "\\n\\n".join(blocks)
    return f"""
أنت رئيس تحرير قسم التقديرات الاستراتيجية في «المرصد الإفريقي للأمن والدفاع».
أعد تقديراً استراتيجياً شهرياً شاملاً باللغة العربية للفترة من {first.isoformat()} إلى {last.isoformat()}، بالاستناد حصراً إلى التقديرات الاستراتيجية الأسبوعية المرفقة أدناه.

هذه الوثيقة ليست تجميعاً ولا تلخيصاً متتابعاً للتقارير الأسبوعية. أعد تركيب المعطيات عبر الشهر، واكشف الاتجاهات المتواصلة والتحولات النوعية والعلاقات بين المسارات الإقليمية والدولية، وميّز بين الحدث العابر والتحول الاستراتيجي. لا تكرر الوقائع لمجرد ورودها في أكثر من تقرير. لا تضف أحداثاً أو أرقاماً أو صفقات أو نوايا غير واردة في المادة. إذا لم تغطّ التقارير جزءاً من الشهر أو كان الدليل محدوداً، صرّح بذلك بوضوح ولا تملأ الفراغ بالتخمين.

يجب أن يتضمن التقدير ثمانية أقسام بالضبط وبالترتيب والعناوين الحرفية التالية:
1. المقدمة
2. تونس وشمال إفريقيا
3. بقية إفريقيا
4. أوروبا والفضاء الأطلسي
5. الشرق الأوسط
6. الولايات المتحدة والقوى الدولية الأخرى
7. الاتجاهات الاستراتيجية العابرة للأقاليم
8. الحوصلة الاستراتيجية والسيناريوهات وانعكاساتها على تونس

المطلوب في كل قسم:
- المقدمة: تقديم صورة تركيبية للمشهد خلال الشهر، لا سرداً للأحداث.
- تونس وشمال إفريقيا: تحليل موقع تونس، ليبيا والجزائر والمغرب، الساحل، المتوسط، الحدود، التعاون الدفاعي والهجرة والأمن البحري عند ورودها في المعطيات.
- بقية إفريقيا: تحليل بؤر النزاع والتحولات السياسية والأمنية والتنافس الخارجي.
- أوروبا والفضاء الأطلسي: تحليل الدفاع الأوروبي والناتو وأوكرانيا وروسيا والعلاقات عبر الأطلسي، وفق المادة المتاحة.
- الشرق الأوسط: تحليل توازنات القوى والنزاعات والممرات البحرية والطاقة.
- الولايات المتحدة والقوى الدولية الأخرى: تحليل خيارات القوى الكبرى وأثرها على النظام الدولي.
- الاتجاهات العابرة للأقاليم: ربط المسارات المشتركة مثل التحالفات، التسلح، عسكرة السياسة، أمن الطاقة والملاحة، والتنافس على النفوذ.
- الحوصلة: استخلاص أهم النتائج، ثم عرض سيناريوهين أو ثلاثة للمرحلة التالية مع مؤشرات تحققها، وتحديد الانعكاسات العملية المحتملة على الأمن القومي التونسي. لا تقدم السيناريوهات كحقائق أو تنبؤات يقينية.

اكتب تحليلاً معمقاً ومتماسكاً. استهدف 130-190 كلمة لكل قسم، مع إعطاء الأولوية للدقة على الحشو. المقدمة والخلاصة نثر مترابط. ميّز بوضوح بين الوقائع المنقولة والاستنتاج التحليلي والسيناريو الاحتمالي. لا تستخدم عبارات قطعية عندما لا تسمح الأدلة بذلك. لا تضع علامات إحالة رقمية داخل النص. لا تذكر أن لديك إمكانية الاطلاع على مصادر غير موجودة في المادة. اكتب بلغة عربية صحفية رصينة، وعرّف الاختصارات عند أول ورودها.

أعد JSON فقط بالحقول التالية:
- title: عنوان عربي واضح يتضمن الشهر والسنة.
- subject: موضوع التقدير.
- summary: حوصلة تنفيذية من 120 إلى 170 كلمة.
- sections: ثمانية عناصر بالضبط، وكل عنصر يتضمن heading وtext.
- coverage_note: ملاحظة قصيرة وصريحة عن مدى تغطية التقارير الأسبوعية للفترة الشهرية، إذا كانت هناك فجوات.

المواد المستخلصة من التقديرات الأسبوعية:
{source_block}
"""


def main():
    first = target_month()
    last = month_end(first)
    reports = collect_reports(first, last)
    if len(reports) < 3:
        raise RuntimeError(
            f"Only {len(reports)} weekly assessments overlap {first:%Y-%m}; "
            "at least 3 are required for a meaningful monthly synthesis."
        )
    earliest = min(date.fromisoformat(x["period_start"]) for x in reports)
    latest = max(date.fromisoformat(x["period_end"]) for x in reports)
    if earliest > first + timedelta(days=7) or latest < last - timedelta(days=1):
        raise RuntimeError(
            f"Weekly coverage is incomplete for {first:%Y-%m}: "
            f"covered {earliest} to {latest}; refusing to publish a misleading monthly report."
        )

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is missing; no monthly assessment was published.")
    client = genai.Client(api_key=api_key)
    schema = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "subject": {"type": "string"},
            "summary": {"type": "string"},
            "coverage_note": {"type": "string"},
            "sections": {"type": "array", "items": {"type": "object",
                "properties": {"heading": {"type": "string"}, "text": {"type": "string"}},
                "required": ["heading", "text"]}}
        },
        "required": ["title", "subject", "summary", "coverage_note", "sections"]
    }
    response = client.models.generate_content(
        model=MODEL,
        contents=build_prompt(first, last, reports),
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=schema,
            temperature=0.2, max_output_tokens=14000
        )
    )
    output = json.loads(response.text or "{}")
    sections = output.get("sections", [])
    if len(sections) != 8 or [clean(x.get("heading")) for x in sections] != EXPECTED:
        raise RuntimeError("Monthly assessment failed the exact eight-section structure check.")
    counts = [len(clean(x.get("text")).split()) for x in sections]
    if any(n < 85 for n in counts):
        raise RuntimeError(f"Monthly section word-count validation failed: {counts}.")
    summary = clean(output.get("summary"))
    if len(summary.split()) < 80:
        raise RuntimeError("Monthly executive summary is too short; refusing to publish.")

    date_tag = last.isoformat()
    slug = hashlib.sha1(f"{first:%Y-%m}".encode()).hexdigest()[:10]
    relative_path = f"{first:%Y-%m}/{last:%Y%m%d}_{slug}.json"
    title = clean(output.get("title")) or f"التقدير الاستراتيجي الشهري — {MONTHS[first.month - 1]} {first.year}"
    weekly_refs = [{
        "title": x.get("title", "التقدير الاستراتيجي الأسبوعي"),
        "date": x.get("date"), "period_start": x.get("period_start"),
        "period_end": x.get("period_end"),
        "path": x.get("path", "")
    } for x in reports]
    source_map = {}
    for report in reports:
        for source in report.get("sources", []):
            url = source.get("url")
            if url and url not in source_map:
                source_map[url] = {"title": source.get("title", url), "url": url}
    card = {
        "id": slug, "kind": "monthly_assessment",
        "kind_label": "التقدير الاستراتيجي الشهري",
        "title": title,
        "subject": clean(output.get("subject")) or "التقدير الاستراتيجي الشهري للتطورات الأمنية والدفاعية والجيوسياسية",
        "date": date_tag, "period_start": first.isoformat(), "period_end": last.isoformat(),
        "region": "تقدير استراتيجي شامل — تونس وشمال إفريقيا وإفريقيا والعالم",
        "summary": summary, "coverage_note": clean(output.get("coverage_note")),
        "sections": sections, "weekly_reports": weekly_refs,
        "sources": list(source_map.values())[:60], "path": relative_path
    }
    save_json(ARCHIVE / relative_path, card)
    add_to_index(card)
    print(f"Published monthly assessment: {title}; weekly_reports={len(reports)}; sources={len(card['sources'])}; path={relative_path}")


if __name__ == "__main__":
    main()
