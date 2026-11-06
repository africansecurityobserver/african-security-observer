import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types


# ============================================================
# CONFIGURATION
# Refreshed after the news-ingestion recovery on 2026-11-04.
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
NEWS_FILE = BASE_DIR / "news.json"
CARDS_FILE = BASE_DIR / "daily_cards" / "cards.json"
ARCHIVE_DIR = BASE_DIR / "daily_cards" / "archive"

TIMEZONE = ZoneInfo("Africa/Tunis")

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

# Allow the full eligible daily feed through on normal-volume days.
# If volume exceeds this ceiling, select across categories instead of taking
# only the newest tail of the chronological feed.
MAX_NEWS_ITEMS = 180

# Senior officials whose concrete, current events receive priority in the
# daily card. Names are matched against Arabic and common Latin-script forms.
PRIORITY_OFFICIALS = {
    "libya": [
        "خليفة حفتر", "Khalifa Haftar", "Khalifa Hifter",
        "صدام حفتر", "Saddam Haftar", "Saddam Hifter",
        "خالد حفتر", "Khaled Haftar", "Khalid Haftar",
        "عبد الحميد الدبيبة", "عبدالحميد الدبيبة", "Abdul Hamid Dbeibeh",
        "Abdelhamid Dbeibah", "Abdulhamid Dbeibeh",
        "محمد المنفي", "Mohamed al-Menfi", "Mohammed al-Menfi", "Mohamed Menfi",
        "عبد المجيد مليقطة", "عبدالمجيد مليقطة", "Abdelmajid Mleigta",
        "Abdulmajid Mleigta",
        "الطاهر الباعور", "Taher al-Baour", "Taher Albawour",
        "عبد السلام الزوبي", "عبدالسلام الزوبي", "Abdul Salam al-Zoubi",
        "Abdel Salam Zoubi",
        "محمود حمزة", "Mahmoud Hamza"
    ],
    "algeria": [
        "عبد المجيد تبون", "عبدالمجيد تبون", "Abdelmadjid Tebboune",
        "السعيد شنقريحة", "السعيد شنقريحه", "Said Chengriha", "Saïd Chengriha",
        "محمد عرقاب", "Mohamed Arkab", "Mohammed Arkab",
        "سيفي غريب", "سيفي غريب", "Sifi Ghrieb", "Sifi Ghrib",
        "أحمد عطاف", "احمد عطاف", "Ahmed Attaf",
        "السعيد سعيود", "Saïd Sayoud", "Said Sayoud",
        "الحاج بوسلجة", "الحاج بوسلجه", "El Hadj Bousselja", "Hadj Bousselja"
    ],
    "morocco": [
        "محمد السادس", "Mohammed VI", "Mohammed the Sixth", "King Mohammed VI",
        "فاطمة الزهراء المنصوري", "Fatima Zahra Mansouri",
        "ناصر بوريطة", "Nasser Bourita", "Nasser Bourita",
        "عبد الوافي لفتيت", "عبدالوافي لفتيت", "Abdelouafi Laftit",
        "محمد بريظ", "محمد بريظ", "Mohamed Berrid", "Mohammed Berrid"
    ]
}


# ============================================================
# CARD PERIODS
# ============================================================

PERIODS = {
    # One continuously updated card per Tunisia calendar day.
    "daily": {
        "start_hour": 6,
        "start_minute": 0,
        "label": "00:00–23:59 (تُنشأ عند 06:00 وتُحدّث خلال اليوم)",
    },
}


# ============================================================
# HELPERS
# ============================================================

def load_json(path, default):
    if not path.exists():
        return default

    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"Warning: could not read {path}: {e}")
        return default


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )


def parse_datetime(value):
    """
    Convert RSS dates / ISO dates into an aware datetime.
    """
    if not value:
        return None

    value = str(value).strip()

    # RSS format example:
    # Wed, 07 Oct 2026 09:15:07 GMT
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(TIMEZONE)

    except Exception:
        pass

    # ISO format
    try:
        value2 = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(value2)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        return dt.astimezone(TIMEZONE)

    except Exception:
        return None


def determine_slot(now):
    """Return the single daily card identifier."""
    return "daily"

def get_period_dates(slot, reference_date):
    """Collect all news published on today's local calendar date, from midnight."""
    if slot != "daily":
        raise ValueError(f"Unknown card type: {slot}")

    now = datetime.now(TIMEZONE)
    start = datetime(
        reference_date.year, reference_date.month, reference_date.day,
        0, 0, tzinfo=TIMEZONE
    )
    end = max(start, now) if now.date() == reference_date else start
    return start, end


def normalize_title(title):
    """
    Basic normalization used to reduce obvious duplicate headlines.
    """

    if not title:
        return ""

    text = str(title).lower()

    removable = [
        "–",
        "—",
        "-",
        "|",
        ":",
        "arab news",
        "reuters",
        "washington post",
        "the washington post",
        "africanews",
        "al jazeera",
    ]

    for item in removable:
        text = text.replace(item, " ")

    # Remove excessive spaces
    text = " ".join(text.split())

    return text[:250]


def basic_deduplicate(items):
    """
    Remove exact / near-exact duplicate headlines before
    sending the material to Gemini.

    Gemini will perform the deeper semantic deduplication.
    """

    result = []
    seen = set()

    for item in items:
        title = (
            item.get("original_title")
            or item.get("title")
            or ""
        )

        key = normalize_title(title)

        if not key:
            continue

        # First 120 characters are enough for basic duplicate detection
        short_key = key[:120]

        if short_key in seen:
            continue

        seen.add(short_key)
        result.append(item)

    return result


# ============================================================
# NEWS SELECTION
# ============================================================

def get_news_for_period(slot, reference_date):
    news_data = load_json(NEWS_FILE, {})

    items = news_data.get("items", [])

    start, end = get_period_dates(
        slot,
        reference_date
    )

    selected = []
    today_count = 0
    sports_excluded = 0
    editorial_excluded = 0

    for item in items:
        published = item.get("published")

        dt = parse_datetime(published)

        if not dt:
            continue

        # Include only news published on the card's local calendar date
        # in Tunisia, even for the 05:00 slot that spans the previous night.
        if dt.date() != reference_date:
            continue

        # Keep the slot's time window as well as the local publication date.
        # Half-open interval: start <= dt < end
        if start <= dt < end:
            today_count += 1
            editorial_text = " ".join(str(item.get(key, "") or "") for key in (
                "title", "description", "original_title", "original_description"
            ))
            if is_sports_related(editorial_text):
                sports_excluded += 1
                continue
            if not is_editorially_eligible(editorial_text):
                editorial_excluded += 1
                continue
            selected.append({
                "title": item.get("title", ""),
                "description": item.get("description", ""),
                "original_title": item.get("original_title", ""),
                "original_description": item.get(
                    "original_description",
                    ""
                ),
                "category": item.get("category", ""),
                "source": item.get("source", ""),
                "published": published,
            })

    # Sort oldest -> newest so Gemini receives the chronology
    selected.sort(
        key=lambda x: parse_datetime(x.get("published")) or start
    )

    before_dedup_count = len(selected)
    selected = basic_deduplicate(selected)
    after_dedup_count = len(selected)

    # The old cap kept the newest non-priority items only. On busy days this
    # silently discarded earlier events. Keep the complete eligible packet
    # up to the ceiling; above it, preserve priority coverage and then
    # interleave the remaining categories chronologically.
    if len(selected) > MAX_NEWS_ITEMS:
        terrorism_terms = (
            "iswap", "islamic state west africa province", "boko haram",
            "ولاية غرب أفريقيا", "الدولة الإسلامية في غرب أفريقيا",
            "داعش", "بوكو حرام", "terroristes", "jihadistes",
            "état islamique en afrique de l’ouest"
        )
        priority_groups = [
            [
                item for item in selected
                if priority_section_for_text(news_item_search_text(item))
            ],
            [
                item for item in selected
                if str(item.get("category", "")).casefold() == "international"
            ],
            [
                item for item in selected
                if any(term in news_item_search_text(item).casefold()
                       for term in terrorism_terms)
            ],
        ]

        chosen = []
        chosen_ids = set()
        for group in priority_groups:
            for item in group:
                marker = id(item)
                if marker not in chosen_ids:
                    chosen.append(item)
                    chosen_ids.add(marker)

        def spread(items, count):
            """Choose evenly across a chronological list without favoring its tail."""
            if count <= 0:
                return []
            if len(items) <= count:
                return list(items)
            if count == 1:
                return [items[len(items) // 2]]
            indexes = [
                round(i * (len(items) - 1) / (count - 1))
                for i in range(count)
            ]
            return [items[index] for index in indexes]

        if len(chosen) >= MAX_NEWS_ITEMS:
            selected = spread(chosen, MAX_NEWS_ITEMS)
        else:
            remaining_slots = MAX_NEWS_ITEMS - len(chosen)
            by_category = {}
            for item in selected:
                if id(item) in chosen_ids:
                    continue
                category = str(item.get("category") or "other").strip().casefold()
                by_category.setdefault(category, []).append(item)

            # Sort each category chronologically, then take one item per
            # category per pass to prevent a single busy feed/category from
            # crowding out the rest.
            for category_items in by_category.values():
                category_items.sort(
                    key=lambda x: parse_datetime(x.get("published")) or start
                )

            balanced = []
            category_names = sorted(by_category)
            while len(balanced) < remaining_slots:
                added = False
                for category in category_names:
                    if by_category[category]:
                        balanced.append(by_category[category].pop(0))
                        added = True
                        if len(balanced) >= remaining_slots:
                            break
                if not added:
                    break

            selected = chosen + balanced

        selected.sort(
            key=lambda x: parse_datetime(x.get("published")) or start
        )

    print(
        "News selection audit: "
        f"raw={len(items)}, today_in_window={today_count}, "
        f"sports_excluded={sports_excluded}, "
        f"editorial_excluded={editorial_excluded}, "
        f"before_dedup={before_dedup_count}, "
        f"after_dedup={after_dedup_count}, "
        f"sent_to_gemini={len(selected)}, "
        f"omitted_by_capacity={max(0, after_dedup_count - len(selected))}"
    )
    return selected


# ============================================================
# PREVIOUS CARDS
# ============================================================

def load_previous_cards():
    data = load_json(CARDS_FILE, {})

    if isinstance(data, dict):
        cards = data.get("cards", [])

        if isinstance(cards, list):
            return cards

    return []


def extract_previous_events(cards, current_date):
    """
    Collect events from cards that occurred BEFORE the current
    card period.

    Important:
    - Do NOT use the current card itself as a previous card.
    - This allows safely rerunning the same slot without causing
      Gemini to remove the events that it generated previously.
    """

    result = []

    try:
        current_dt = datetime.strptime(
            current_date,
            "%Y-%m-%d"
        ).date()
    except Exception:
        return result

    for card in cards:

        card_date = card.get("date")
        card_slot = str(card.get("slot", "")).strip()

        if not card_date:
            continue

        try:
            card_dt = datetime.strptime(
                card_date,
                "%Y-%m-%d"
            ).date()
        except Exception:
            continue

        # Ignore cards older than one day.
        if abs((current_dt - card_dt).days) > 1:
            continue

        # ----------------------------------------------------
        # IMPORTANT:
        # Never treat the same slot on the same date as a
        # previous card.
        #
        # This prevents a manual rerun of the 06:00–16:00 card
        # from hiding the events that were already generated
        # during an earlier test.
        # ----------------------------------------------------
        if card_dt == current_dt:

            # Current 16 card:
            # ignore another 16 card from the same date.
            #
            # Current 21 card:
            # ignore another 21 card from the same date.
            #
            # Current 05 card:
            # ignore another 05 card from the same date.
            #
            # We determine the current slot separately below.
            current_slot = None

            # The current slot will be supplied through the
            # environment variable when the workflow runs.
            current_slot = os.getenv(
                "CARD_SLOT",
                ""
            ).strip()

            if not current_slot and len(sys.argv) >= 2:
                current_slot = sys.argv[1].strip()

            if current_slot == "daily" or card_slot == current_slot:
                continue

        sections = card.get(
            "sections",
            []
        )

        for section in sections:

            events = section.get(
                "events",
                []
            )

            for event in events:

                if not isinstance(event, dict):
                    continue

                text_value = event.get(
                    "text",
                    ""
                )

                if text_value:
                    result.append(
                        text_value
                    )

    # Keep the most recent 80 previous events.
    return result[-80:]


# ============================================================
# STRATEGIC CANDIDATE AUDIT
# ============================================================

STRATEGIC_REVIEW_MARKERS = (
    # Defense aid, equipment, procurement and military cooperation.
    "مكافحة الإرهاب", "مكافحة الارهاب", "معدات مكافحة", "معدات عسكرية",
    "مساعدات عسكرية", "مساعدات دفاعية", "معدات الأمن البحري",
    "معدات الامن البحري", "تعاون دفاعي", "تعاون عسكري", "اتفاق دفاعي",
    "صفقة دفاعية", "صفقة تسلح", "سفن حربية", "تحديث السفن",
    "أنظمة مضادة للطائرات", "مكافحة الطائرات المسيّرة",
    "مكافحة الطائرات المسيرة", "الامن البحري",
    "counterterrorism equipment", "military aid", "defense assistance",
    "defence assistance", "maritime security equipment",
    "military cooperation", "defense cooperation", "defence cooperation",
    "warship", "naval modernization", "counter-drone", "defense deal",
    # Field developments and military operations.
    "اشتباكات مسلحة", "اشتباك مسلح", "معارك", "معركة", "هجوم مسلح",
    "قصف", "تقدم الجيش", "توسيع السيطرة", "يوسع سيطرته", "يوسع الجيش سيطرته",
    "استعادة مناطق", "استعاد مناطق", "انتزاع مناطق", "انتزع مناطق",
    "تدمير آليات", "السيطرة على مناطق",
    "clashes", "battle", "military operation", "territorial control",
    "seized areas", "recaptured areas", "destroyed military vehicles",
    # Terrorism, intelligence, aviation threats and weapons trafficking.
    "بوكو حرام", "داعش", "تنظيم القاعدة", "تهريب صواريخ", "تهريب أسلحة",
    "تهريب اسلحة", "تفجير سيارة", "سيارة مفخخة", "تهديدات بالقنابل", "تهديدات جديدة بالقنابل",
    "تهديدات بوجود قنابل", "تهديد بوجود قنبلة", "تجسس", "عميل روسي", "مخبر لجماعة",
    "مخبر لتنظيم", "bomb threat", "espionage", "missile smuggling",
    "arms smuggling", "car bombing", "boko haram", "islamic state",
    "al-qaeda", "informant", "spy",
    # Senior military leadership, foreign deployments and major exercises.
    "قائد الجيش", "رئيس الأركان", "رئيس الاركان", "تنحي قائد",
    "تنحى", "استقالة قائد", "استقال قائد", "إقالة قائد",
    "تعيين قائد", "نشر قوات أجنبية", "نشر قوات", "مناورات عسكرية",
    "مناورات الناتو", "تدريبات عسكرية", "military chief", "army chief",
    "chief of staff", "foreign troop deployment", "military exercises",
    "nato exercise", "resigns as army chief"
)


def identify_strategic_review_candidates(news_items):
    """
    Build a recall-oriented review list from source titles and descriptions.
    This is not an automatic publication filter: Gemini must assess each
    candidate's factuality, strategic relevance and duplication.
    """
    candidates = []
    for index, item in enumerate(news_items, start=1):
        searchable = " ".join(str(item.get(key, "") or "") for key in (
            "title", "description", "original_title", "original_description"
        )).casefold()
        if any(marker.casefold() in searchable for marker in STRATEGIC_REVIEW_MARKERS):
            candidates.append((index, item))
    return candidates


# ============================================================
# GEMINI PROMPT
# ============================================================

def build_prompt(
    slot,
    reference_date,
    start,
    end,
    news_items,
    previous_events,
    current_events=None
):

    date_text = reference_date.strftime("%d/%m/%Y")

    period_label = f"00:00–{end.strftime('%H:%M')} (أخبار اليوم بالتوقيت المحلي)"

    news_material = []

    for index, item in enumerate(news_items, start=1):

        news_material.append(
            f"""
مادة رقم {index}
العنوان: {item.get("title", "")}
العنوان الأصلي: {item.get("original_title", "")}
الوصف: {item.get("description", "")}
الوصف الأصلي: {item.get("original_description", "")}
التصنيف الحالي: {item.get("category", "")}
التوقيت: {item.get("published", "")}
المصدر الداخلي: {item.get("source", "")}
""".strip()
        )

    news_text = "\n\n".join(news_material)

    previous_text = "\n".join(
        f"- {event}"
        for event in previous_events
    )

    if not previous_text:
        previous_text = "لا توجد بطاقات سابقة متاحة."

    current_text = "\n".join(f"- {event}" for event in (current_events or []))
    if not current_text:
        current_text = "لا توجد أحداث محفوظة في نسخة اليوم الحالية."

    strategic_candidates = identify_strategic_review_candidates(news_items)
    strategic_candidate_material = []
    for item_number, item in strategic_candidates:
        strategic_candidate_material.append(
            f"مادة رقم {item_number}: {item.get('title', '')} | "
            f"{item.get('original_title', '')} | "
            f"{item.get('description', '')} | "
            f"{item.get('original_description', '')}"
        )
    strategic_candidate_text = "\n".join(strategic_candidate_material)
    if not strategic_candidate_text:
        strategic_candidate_text = "لم تُرصد مواد مرشحة بهذه المؤشرات."

    print(f"Strategic candidate audit list: {len(strategic_candidates)} items")

    prompt = f"""
أنت محرر رئيسي في صحيفة استخباراتية/استراتيجية عربية متخصصة
في الشؤون الأمنية والعسكرية والسياسية في إفريقيا والعالم.

مهمتك إعداد بطاقة أحداث يومية باللغة العربية اعتماداً حصراً على
المواد الإخبارية الموجودة في هذه الرسالة.

التاريخ المحلي:
{date_text}

الفترة:
{period_label}

المنطقة الزمنية:
Africa/Tunis

============================================================
القواعد التحريرية الأساسية
============================================================

1. استخدم اللغة العربية فقط.

2. لا تخترع أي حدث أو معلومة أو اسم أو رقم غير موجود في المواد
   المقدمة.

3. لا تعتمد على معرفتك السابقة بالأحداث لملء الفراغات.

4. إذا لم توجد مادة كافية لقسم معين، اترك قائمة الأحداث فارغة.

5. لا تذكر أسماء المصادر داخل النص النهائي.

6. لا تضع روابط.

7. لا تضع مراجع أو حواشي أو عبارات مثل:
   "وفقاً لرويترز"
   "بحسب واشنطن بوست"
   "أفادت الصحيفة..."

8. اجمع التقارير المتعددة التي تتحدث عن الحدث نفسه في حدث واحد.
   لا تكرر الحدث بسبب اختلاف المصادر.

8-أ. قبل إخراج البطاقة، راجع أحداث كل دولة داخل القسم نفسه وقارن
     مضمونها لا صياغتها فقط. إذا كان خبران يصفان الواقعة نفسها
     بعبارات مختلفة، فادمجهما في حدث واحد مع الاحتفاظ بالتفاصيل
     المؤكدة الإضافية. لا تُبقِ نسختين من الخبر نفسه لمجرد اختلاف
     العنوان أو المصدر أو الترجمة.

8-ب. لا تدمج الأخبار لمجرد أنها تخص الدولة نفسها أو المسؤول نفسه
     أو الموضوع العام نفسه. يجوز أن يظهر أكثر من خبر للدولة نفسها
     إذا كان كل خبر يصف واقعة مستقلة فعلاً، مثل اجتماع منفصل عن
     هجوم عسكري أو صفقة تسلح منفصلة عن تعيين مسؤول.

8-ج. عند الشك، قارن الفاعل/الأطراف، والفعل أو الإجراء، والمكان،
     والتوقيت، والأرقام أو المعدات المذكورة. ادمج الخبرين عندما
     تتطابق مجموعة كافية من هذه العلامات على الواقعة نفسها؛
     أما إذا اختلفت الواقعة أو المكان أو التوقيت اختلافاً جوهرياً
     فأبقِ الخبرين منفصلين.

9. إذا كانت عدة مواد تتحدث عن نفس التطور، صغ الحدث في صياغة
   واحدة أكثر دقة واكتمالاً.

10. حافظ على أسماء الرؤساء والقادة العسكريين والوحدات العسكرية
    والقواعد والطائرات والسفن والصواريخ والمنظمات كما هي في المادة
    متى كان ذلك مهماً.

11. لا تجعل البطاقة مجرد قائمة بعناوين الصحف.
    يجب أن تكون الصياغة مهنية ومكثفة وتوضح:
    من؟ ماذا حدث؟ أين؟ وما أهمية التطور عندما تكون المعلومة متاحة.

12. لا تستخدم لغة مثيرة أو دعائية.

13. لا تضف أحداثاً قديمة لمجرد الوصول إلى عدد معين.

14. لا يوجد سقف تحريري لعدد الأحداث، ولا تختصر البطاقة للوصول
    إلى رقم مستهدف. أدرج جميع التطورات المستقلة التي تستوفي المعايير.
    قد تكون البطاقة 15 إلى 20 حدثاً في الأيام الهادئة، وقد تتجاوز
    20 حدثاً في الأيام المزدحمة إذا توفرت وقائع مهمة ومستقلة.
    لا تسقط خبراً مؤهلاً لمجرد أن البطاقة تضم عدداً كافياً من الأخبار.

15. ليبيا يجب أن تحتوي عادةً على أكبر عدد من الأحداث عندما تكون
    هناك مادة كافية وذات صلة، لأن القسم الليبي هو قسم محوري في الصحيفة.

============================================================
أسلوب صياغة الأحداث
============================================================

16. يجب أن تبدأ صياغة كل حدث بفعل، وليس باسم شخص أو دولة أو مؤسسة،
    متى كان ذلك ممكناً وطبيعياً من الناحية اللغوية.

17. استخدم فعلاً واضحاً في بداية الجملة يعبّر مباشرة عن الحدث،
    مثل:
    "دعا..."
    "أعلن..."
    "أعلنت..."
    "استقبل..."
    "التقى..."
    "وقّعت..."
    "وقّع..."
    "نفذت..."
    "شنّت..."
    "أطلقت..."
    "اعترضت..."
    "وصلت..."
    "أرسلت..."
    "عيّن..."
    "عيّنت..."
    "أقال..."
    "أقرت..."
    "وافقت..."
    "بدأت..."
    "استأنفت..."
    "أوقفت..."
    "كشفت..."
    "أكدت..."
    "أفادت..."
    "حذرت..."
    "طالب..."
    "أعلن..."
    وغيرها من الأفعال المناسبة للسياق.

18. لا تبدأ الحدث بصياغة اسمية من النوع:
    "الرئيس فلان..."
    "وزارة الدفاع..."
    "القوات المسلحة..."
    "الحكومة..."
    "الجيش..."
    "الجزائر..."
    "المغرب..."
    ثم يأتي الفعل بعد ذلك.

19. بدلاً من ذلك، حوّل الجملة إلى صياغة فعلية طبيعية.

    مثال:

    صياغة غير مرغوبة:
    "الرئيس الإريتري إسياس أفورقي دعا إلى عدم التدخل الخارجي
    في الشأن السوداني."

    الصياغة المطلوبة:
    "دعا الرئيس الإريتري إسياس أفورقي إلى عدم التدخل الخارجي
    في الشأن السوداني."

    مثال آخر:

    صياغة غير مرغوبة:
    "وزارة الدفاع الجزائرية أعلنت وصول معدات عسكرية جديدة."

    الصياغة المطلوبة:
    "أعلنت وزارة الدفاع الجزائرية وصول معدات عسكرية جديدة."

20. يجب أن تكون الصياغة الفعلية طبيعية وسليمة باللغة العربية.
    لا تستخدم فعلاً مصطنعاً أو غريباً فقط من أجل الالتزام بهذه القاعدة.

21. استخدم الزمن المناسب للحدث:
    - الماضي للأحداث التي وقعت وانتهت أو بدأت بالفعل.
    - المضارع للأحداث المستمرة أو المتواصلة.
    - المستقبل فقط عندما تكون المادة نفسها تتحدث بوضوح عن إجراء مستقبلي.

22. إذا احتوى الحدث على أكثر من معلومة مترابطة، ابدأ بالفعل الذي
    يمثل التطور الرئيسي، ثم أضف التفاصيل المهمة في بقية الجملة.

23. حافظ على المعنى الأصلي للمادة ولا تغير درجة اليقين.
    لا تحول "قال" إلى "أكد" أو "أعلن" إذا كانت المادة لا تدعم ذلك.

24. لا تجعل البحث عن فعل في بداية الجملة يؤدي إلى إضافة معلومات
    غير موجودة في المادة.

============================================================
قوالب تحريرية إلزامية بحسب نوع الخبر
============================================================

25. أخبار الاجتماعات واللقاءات والمباحثات والمشاورات:
عندما يكون جوهر الخبر لقاءً أو اجتماعاً أو مباحثات أو مناقشات بين
طرفين، اتبع البنية التحريرية التالية بدقة، مع تكييف الفعل بحسب
الواقعة والمعلومات الواردة في المادة الأصلية:

أ) ابدأ بفعل ماضٍ واضح يعبّر عن الحدث، مثل: «بحث»، «ناقش»،
   «استعرض»، «التقى»، «اجتمع» أو «أجرى مباحثات». إذا كانت المادة
   تقول إن الطرف الأول بحث موضوعاً مع الطرف الثاني، فالأولوية
   لصياغة «بحث» متى كانت طبيعية ودقيقة.

ب) اذكر الطرف الأول مباشرة بعد الفعل، بصفته أو لقبه الوظيفي كاملاً
   ثم اسمه العلم بين علامتي تنصيص عربيتين أو مزدوجتين عند ملاءمة
   التنسيق، مثل: رئيس حكومة الوحدة الوطنية "عبد الحميد الدبيبة".

ج) اذكر المكان بعد الطرف الأول إذا ورد في المادة، بصيغة مثل
   «في طرابلس»، ثم اذكر الطرف الثاني مسبوقاً بـ«مع»، مع صفته أو
   لقبه الوظيفي واسمه العلم، مثل: «مع رئيس جهاز الاستخبارات التركي
   "إبراهيم قالن"».

د) اذكر موضوع اللقاء أو البحث مباشرة بعد الطرف الثاني، ثم أضف
   الموضوعات الإضافية عند ورودها بصيغة «فضلًا عن...» أو ما يلائم
   المعنى. وإذا ذكرت المادة مجالات محددة للتعاون أو النقاش، فحدّدها
   في ختام الجملة بصيغة «خاصةً في مجالات...».

القالب المرجعي المفضل:
«بحث [الصفة الوظيفية الكاملة للطرف الأول] "[اسم الطرف الأول]" في
[المكان]، مع [الصفة الوظيفية الكاملة للطرف الثاني] "[اسم الطرف
الثاني]" [موضوع البحث]، فضلًا عن [الموضوع الإضافي]، خاصةً في
مجالات [المجالات المحددة الواردة في المادة].»

مثال توضيحي للشكل:
«بحث رئيس حكومة الوحدة الوطنية "عبد الحميد الدبيبة" في طرابلس،
مع رئيس جهاز الاستخبارات التركي "إبراهيم قالن" مستجدات الأوضاع في
ليبيا، فضلًا عن سبل تعزيز التعاون الأمني، خاصةً في مجالات تأمين
الحدود ومكافحة الهجرة غير النظامية وتبادل الخبرات.»

قواعد إلزامية:
- حافظ على الصفة الوظيفية كاملةً كما تؤكدها المادة، ولا تستبدلها
  باسم المؤسسة وحده إذا كانت الصفة والاسم متاحين.
- اذكر المكان فقط إذا ورد في المادة؛ لا تخترعه.
- اذكر الطرفين وصفتيهما واسميهما إذا كانت المعلومات متاحة، ولا
  تستنتج منصباً أو صفة غير مثبتة.
- اجعل موضوع البحث مباشراً وواضحاً، ثم أضف الموضوعات الثانوية
  ومجالات التعاون إذا ذكرتها المادة أو دعمتها بوضوح.
- لا تضف «فضلًا عن» أو «خاصةً في مجالات» آلياً إذا لم تتضمن المادة
  موضوعاً إضافياً أو مجالات محددة؛ لا تكرر المعنى ولا تخترع تفاصيل.
- إذا كان الخبر عن لقاء دون بيان موضوع النقاش، فاكتفِ بما تثبته
  المادة، ولا تنسب إلى الطرفين مباحثات في ملفات غير مذكورة.
- إذا كان الخبر عن استقبال أو زيارة بروتوكولية ولم تذكر المادة
  بحث موضوع معين، فصغ الحدث وفق الواقعة الفعلية ولا تحوّله إلى
  مباحثات من عندك.
- لا تفرض هذا القالب على الأخبار التي لا تتناول لقاءً أو اجتماعاً
  أو مباحثات بين طرفين.
26. أخبار توقيع المعاهدات والاتفاقيات ومذكرات التفاهم والعقود:
ابدأ بالفعل "وقّع" أو "وقّعت" أو "أبرم" أو "أبرمت" بحسب الفاعل،
ثم اذكر الطرف الأول بصفته واسمه، والطرف الثاني بصفته واسمه عندما
تكون هذه المعلومات متاحة، ثم نوع الاتفاق ومضمونه أو أهدافه.

القالب المفضل:
"وقّع [صفة الطرف الأول واسمه] مع [صفة الطرف الثاني واسمه]
[اتفاقاً/معاهدة/عقد شراكة/مذكرة تفاهم] يقضي بـ[المضمون أو الهدف]."

- استخدم "يقضي بـ" أو "ينص على" أو "يهدف إلى" بحسب ما توضحه المادة.
- إذا كان الاتفاق بين دولتين أو مؤسستين وليس بين شخصين، اذكر
  الطرفين المؤسسيين كما وردا، ولا تنسب التوقيع إلى شخص غير مسمى.
- لا تخترع قيمة العقد أو مدته أو تفاصيله أو أهدافه.
- ميّز بين توقيع الاتفاق والتصديق عليه أو الإعلان عنه أو بدء تنفيذه؛
  لا تستخدم "وقّع" إلا إذا كانت المادة تؤكد حصول التوقيع.

27. أخبار العمليات الإرهابية والعمليات العسكرية ضد الجماعات المسلحة والقتال:
اختر الصياغة وفق طبيعة الحدث وما تثبته المادة الأصلية. ميّز بدقة بين
هجوم تنفذه جماعة مسلحة، وعملية عسكرية تنفذها القوات ضد عناصر مسلحة،
واشتباك أو قتال بين طرفين أو دولتين. لا تستخدم قالباً واحداً لهذه
الحالات المختلفة.

أ) عند وقوع هجوم تنفذه جماعة إرهابية أو مسلحة، وكانت حصيلة القتلى
أو المصابين معلومة:
ابدأ بالحصيلة، ثم حدّد صفة الضحايا وجنسيتهم إن توفرتا، وبعد ذلك اذكر
الجهة المنفذة والهدف أو المكان والدولة.

القالب المفضل:
«قُتل [العدد] من [صفة الضحايا وجنسيتهم إن توفرتا] وأُصيب [العدد]
آخرون، في هجوم نفذته عناصر تابعة لـ[اسم التنظيم أو الجماعة]،
استهدف [الهدف] في [المنطقة] ([الموقع الجغرافي والدولة عند توافرهما]).»

مثال:
«قُتل 5 عسكريين نيجيريين وأُصيب 10 آخرون بجروح، في هجوم نفذته
عناصر تابعة لتنظيم "الدولة الإسلامية - ولاية غرب إفريقيا"، استهدف
نقطة تفتيش عسكرية بولاية "Yobe" (شمال البلاد).»

ب) إذا كان الهجوم مؤكداً، لكن المادة لا تذكر حصيلة القتلى أو
المصابين أو لا تتضمن تفاصيل عن الخسائر:
ابدأ بالجهة المنفذة، ثم فعل الهجوم والهدف ومكانه، ولا تفتتح الجملة
بخسائر غير معروفة أو تفترض سقوط ضحايا.

القالب المفضل:
«شنّت عناصر تابعة لـ[اسم التنظيم أو الجماعة] هجوماً استهدف
[الهدف] في [المنطقة] ([الموقع الجغرافي والدولة عند توافرهما])،
دون تفاصيل عن حجم الخسائر»؛ وتُذكر العبارة الأخيرة فقط إذا كانت
المادة تشير إلى غياب تفاصيل الخسائر أو كان ذلك مهماً في الخبر.

مثال:
«شنّت عناصر تابعة لتنظيم "الدولة الإسلامية - ولاية غرب إفريقيا"
هجوماً استهدف نقطة تفتيش عسكرية بولاية "Yobe" (شمال نيجيريا)،
دون تفاصيل عن حجم الخسائر.»

ج) عند تنفيذ الجيش أو قوات الأمن عملية ضد تنظيم إرهابي أو جماعة
مسلحة، وكانت النتيجة المعلنة القضاء على عناصر أو قتلهم أو اعتقالهم:
ابدأ بالجهة التي أعلنت العملية، ثم النتيجة وعدد العناصر المستهدفين
إن كان معلوماً، ثم اسم التنظيم ومكان العملية. حافظ على نسبة المعلومة
إلى الجهة التي أعلنتها إذا كانت الحصيلة صادرة عنها ولم يتم التحقق
منها بصورة مستقلة.

القالب المفضل:
«أعلن [الجيش أو الجهة الأمنية] القضاء على [العدد] من عناصر
[اسم التنظيم أو الجماعة] خلال عملية عسكرية في [المنطقة]
([الموقع الجغرافي والدولة عند توافرهما]).»

مثال:
«أعلن الجيش النيجيري القضاء على 20 عنصراً تابعاً لتنظيم
"الدولة الإسلامية - ولاية غرب إفريقيا"، خلال عملية عسكرية بولاية
"Yobe" (شمال البلاد).»

د) عند اندلاع اشتباكات أو قتال مسلح بين جهتين أو دولتين:
ابدأ بحصيلة القتلى أو المصابين إذا كانت معلومة ومركزية في الخبر،
ثم اذكر الطرفين المتقاتلين ومكان الاشتباك. وإذا لم تتوفر حصيلة،
ابدأ بالفعل الذي يصف تطور القتال، وحدّد الطرف الذي شن الهجوم
إن كانت المادة تؤكد ذلك، أو استخدم «اندلعت اشتباكات بين...» عندما
لا تحدد المادة من بدأ القتال.

قوالب إرشادية:
«قُتل [العدد] وأُصيب [العدد] في اشتباكات بين [الطرف الأول]
و[الطرف الثاني] في [المكان].»
«شنّت قوات [الطرف الأول] هجوماً على مواقع [الطرف الثاني]
في [المكان]، ما أسفر عن [الحصيلة المؤكدة إن وجدت].»
«اندلعت اشتباكات مسلحة بين قوات [الطرف الأول] وقوات
[الطرف الثاني] في [المكان]، دون ورود حصيلة مؤكدة للخسائر»
ولا تذكر عدم ورود الحصيلة إلا إذا كان ذلك مدعوماً بالمادة ومفيداً.

قواعد إلزامية لجميع هذه الحالات:
- اختر القالب الذي يطابق الحدث فعلاً: هجوم نفذته جماعة، أو عملية
  نفذتها القوات ضد جماعة، أو قتال بين طرفين. لا تخلط بين المنفذ
  والمستهدف ولا تعكس اتجاه العملية.
- ابدأ بالخسائر المؤكدة عندما تكون معلومة وذات صلة مباشرة بالخبر؛
  وإذا لم تُذكر الحصيلة، فلا تخترعها ولا توحِ بوقوع خسائر.
- لا تستبدل «قُتل» بـ«قُضي عليه» أو العكس إلا بما يتفق مع دلالة
  المصدر، ولا تستخدم «القضاء على» باعتبارها تأكيداً مستقلاً إذا
  كانت مجرد حصيلة أعلنتها جهة مشاركة في العملية.
- اذكر العدد والصفة والجنسية واسم التنظيم والطرفين والهدف والمكان
  فقط بقدر ما توفره المادة. لا تخمّن هوية المهاجم أو الهدف أو نوع
  السلاح أو الخسائر أو نتيجة الاشتباك.
- انسب الادعاءات والنتائج إلى مصدرها عند الحاجة، وميّز بين وقوع
  الحدث المؤكد وبين تبنّي جهة المسؤولية عنه أو اتهامها به.
- لا تصف كل اشتباك أو هجوم بأنه إرهابي؛ استخدم وصف «إرهابي» فقط
  عندما تدعمه المادة أو يكون منسوباً بوضوح إلى جهة محددة.
- اضبط صيغة العدد والمعدود نحوياً، واستخدم صياغة عربية مباشرة
  وموجزة، مع الإبقاء على أسماء الأماكن الأجنبية كما وردت عند الحاجة.
- لا تفرض هذه القوالب على الأخبار التي لا تتعلق بهجوم مسلح أو عملية
  أمنية أو عسكرية أو قتال بين أطراف.
 
28. ترتيب الأولويات التحريرية:
- حدّد نوع الخبر أولاً: مباحثات/اجتماع، توقيع اتفاق، هجوم أو
  حصيلة ضحايا، أو تطور آخر.
- طبّق القالب الأقرب إلى نوع الخبر، مع الحفاظ على المعلومات
  الأساسية واللغة العربية السليمة.
- لا تجمع في جملة واحدة وقائع مستقلة لمجرد اختصار النص.
- هذه القوالب توجّه الصياغة ولا تبرر أبداً استكمال المعلومات
  الناقصة بالتخمين.

============================================================
ترتيب الأخبار داخل كل قسم — قاعدة إلزامية
============================================================

============================================================
معايير الإدراج الاستراتيجي الموسّعة
============================================================

لا تحصر مفهوم الخبر الأمني أو العسكري المهم في الاشتباكات والحروب
والعمليات القتالية. أدرج كل واقعة حديثة ومحددة في المواد تحقق
أهمية دفاعية أو أمنية أو جيوسياسية واضحة، مع مراعاة الضوابط التالية:

1. المساعدات والتجهيزات الدفاعية والأمنية:
- تسليم أو تقديم معدات عسكرية أو أمنية إلى دولة أو قواتها، بما في ذلك
  معدات مكافحة الإرهاب، ومراقبة الحدود، والأمن البحري، والاستطلاع،
  والاتصالات، والحماية من الطائرات المسيّرة.
- المساعدات العسكرية أو الأمنية ذات القيمة أو الكمية أو الأثر المحدد.
- لا يشترط أن تكون المساعدة صفقة شراء؛ فالتبرع أو التسليم الفعلي
  تطور مهم أيضاً. لا تدرج إعلان نوايا بلا معدات أو قرار محدد.

2. التعاون العسكري والأمني:
- الاجتماعات والزيارات والتدريبات وبرامج التكوين والتبادل العسكري
  والتعاون الاستخباراتي والحدودي والبحري.
- أدرج التطور عندما تتضمن المادة اجتماعاً أو زيارة أو نشاطاً أو
  اتفاقاً محدداً ومعلوماً في المجال الدفاعي أو الأمني.
- لا تدرج عبارات المجاملة أو التصريحات العامة عن أهمية التعاون
  إذا لم تتضمن فعلاً أو إجراءً أو موضوعاً محدداً.

3. التطورات الميدانية:
- السيطرة على المدن والمناطق أو استعادتها، وتغير خطوط السيطرة،
  والتقدم أو الانسحاب العسكري، وتدمير الآليات والمعدات،
  والمعارك والاشتباكات والعمليات المؤثرة.
- لا تشترط أن يكون التطور انقلاباً أو معركة على مستوى الدولة.
  التغير الميداني المحدد في جبهة مهمة يستحق الإدراج.

4. التهديدات والعمليات الأمنية:
- التهديدات المؤكدة التي تستهدف الطيران المدني أو المطارات أو
  المنشآت الحيوية، والتفجيرات ومحاولات الاغتيال.
- التجسس والعمليات الاستخباراتية واعتقال المشتبه بارتباطهم
  بتنظيمات إرهابية أو جماعات مسلحة أو شبكات تهريب أسلحة وصواريخ.
- لا تساوِ بين هذه الوقائع والجرائم المحلية الاعتيادية أو التوقيفات
  الروتينية التي لا صلة لها بالأمن القومي أو التنظيمات المسلحة.

5. القيادات العسكرية:
- الاستقالة أو التنحي أو الإقالة أو التعيين أو إعادة تشكيل القيادة
  العليا للجيش أو الأجهزة الأمنية والاستخباراتية.
- أدرج الإعلان الرسمي أو الإجراء المؤكد، لا التكهنات بشأن الخلافة
  أو مستقبل السلطة.

6. الانتشار والمناورات:
- نشر قوات أجنبية أو طلب نشرها أو الاتفاق على ذلك، إذا كانت الخطة
  رسمية ومحددة من حيث الأطراف أو العدد أو المهمة.
- المناورات والتدريبات الكبرى، خصوصاً متعددة الجنسيات أو المرتبطة
  بالناتو أو التحالفات العسكرية، مع ذكر حجم المشاركة ومكانها متى وردا.

7. التسلح والصناعات الدفاعية:
- صفقات شراء أو تحديث السفن والطائرات وأنظمة الدفاع الجوي
  ومكافحة المسيّرات والصواريخ والرادارات وغيرها من المنظومات.
- تطوير أو إطلاق آليات ومبادرات مؤسسية لتسريع اقتناء أنظمة دفاعية.
- لا يشترط حد مالي ثابت؛ قيّم أهمية الصفقة وفق نوع المنظومة
  والجهة المستفيدة والأثر على القدرات العسكرية.

لا تجعل وجود كلمة «أمني» أو «عسكري» وحده سبباً للإدراج.
اشترط واقعة محددة وحديثة، واحتفظ بنسبة المعلومات إلى الجهة
التي أعلنتها عندما تكون الحصيلة أو الرواية غير مستقلة.

============================================================
مراجعة ثانية إلزامية قبل إخراج البطاقة
============================================================

بعد إعداد مسودة الأحداث، نفّذ داخلياً جولة تدقيق ثانية على جميع
المواد الإخبارية المقدمة، لا على الأخبار التي اخترتها فقط:

أ) افحص كل مادة لم تظهر في المسودة، وقارن عنوانها ووصفها الأصلي
   والمترجم، ولا تعتبر عدم اختيارها أول مرة دليلاً على عدم أهميتها.

ب) اسأل عن كل مادة: هل تتضمن مساعدة أو تسليماً لمعدات دفاعية،
   أو تعاوناً عسكرياً محدداً، أو تغيراً ميدانياً، أو تهديداً أمنياً
   عابراً للحدود، أو عملية استخباراتية، أو تغييراً في قيادة عسكرية،
   أو انتشار قوات أو مناورة كبرى، أو صفقة/تحديثاً دفاعياً؟

ج) أضف كل واقعة مستقلة تستوفي المعايير أعلاه إلى القسم الصحيح،
   حتى لو لم تتضمن قتالاً أو لم تكن مرتبطة بدولة ذات أولوية خاصة.

د) لا تسقط خبراً مهماً لأن عنوانه اقتصادي أو دبلوماسي إذا كان
   مضمونه الفعلي تسليم معدات أمنية أو عقداً دفاعياً أو تعاوناً عسكرياً.
   اقرأ الوصف الأصلي والمترجم معاً لتحديد جوهر الخبر.

هـ) ميّز بين الخبر المستقل والتقرير المكرر عن الحدث نفسه. ادمج
   التكرار فقط، ولا تدمج واقعتين مختلفتين لمجرد أنهما تتعلقان
   بالدولة أو المؤسسة أو الموضوع نفسه.

و) لا تخفّض معيار الدقة: استبعد الشائعات والتوقعات والبيانات العامة
   غير المصحوبة بواقعة. لا تستنتج صحة الادعاءات العسكرية المتنازع
   عليها، وانسبها إلى الجهة التي أعلنتها.

ز) إذا كان الخبر مهماً لكنه لا يطابق قسماً بوضوح، ضعه في القسم
   الجغرافي أو الموضوعي الأنسب وفق جوهره، ولا تسقطه لمجرد صعوبة
   التصنيف.

هذه المراجعة جزء إلزامي من عملية الاختيار وليست دعوة لزيادة العدد
بشكل آلي. الجودة والدقة وعدم التكرار تظل شروطاً حاكمة.

بعد جمع الأخبار الخاصة بكل قسم، رتّبها من الأعلى إلى الأسفل وفق الأولويات التالية، وينطبق ذلك على جميع الأقسام دون استثناء، وليس على ليبيا وحدها:

1. أولاً: الأخبار السياسية والرسمية؛ مثل اجتماعات ولقاءات واستقبالات وزيارات المسؤولين السياسيين، ورؤساء الدول والحكومات والمجالس الرئاسية، والوزراء، والمباحثات والاتفاقات والقرارات والتعيينات الحكومية ذات الأهمية.
2. ثانياً: أخبار القيادات العسكرية والأمنية والاستخباراتية؛ مثل اجتماعات قادة الجيوش ورؤساء الأركان وقادة التشكيلات العسكرية ورؤساء الأجهزة الأمنية والاستخباراتية، وتعييناتهم وقراراتهم وتحركاتهم الرسمية المهمة.
3. ثالثاً: الأخبار الميدانية والعسكرية؛ مثل الاشتباكات والمعارك والهجمات والعمليات العسكرية والتدريبات والمناورات وانتشار القوات ووصول المعدات العسكرية.
4. رابعاً: بقية الأخبار المرتبطة بالأمن والاستقرار، ومنها الهجرة غير النظامية والتهريب واعتراض المهاجرين وتقارير تتبع الرحلات الجوية المشبوهة أو الطائرات العسكرية عندما لا تكون جزءاً من تطور سياسي أو عسكري أعلى أولوية، ثم بقية المستجدات الأقل أولوية.

إذا احتوى القسم على أخبار من أكثر من فئة، فلا ترتبها بحسب حداثتها وحدها؛ طبّق ترتيب الأولوية أعلاه أولاً، ثم قدّم الأحدث والأكثر أهمية داخل الفئة نفسها. إذا انتمى الخبر إلى أكثر من فئة، صنّفه وفق جوهر التطور الرئيسي. لا تحذف أي خبر مهم لمجرد أن مرتبته متأخرة؛ المطلوب تغيير الترتيب لا إسقاط الأخبار. طبّق القاعدة على ليبيا والجزائر والمغرب وسائر الأقسام.

============================================================
أقسام البطاقة
============================================================

القسم الأول:
"ليبيا"

قائمة أسماء رئيسية إلزامية للمتابعة في المواد الإخبارية:
خليفة حفتر، صدام حفتر، خالد حفتر، عبد الحميد الدبيبة، محمد المنفي،
عبد المجيد مليقطة، الطاهر الباعور، عبد السلام الزوبي، محمود حمزة.

قاعدة إلزامية: افحص عناوين وأوصاف جميع المواد بحثاً عن هذه الأسماء
وصيغ كتابتها اللاتينية الشائعة. إذا تضمنت مادة خبراً ملموساً وحديثاً
عن اجتماع أو استقبال أو زيارة أو قرار أو تعيين أو إقالة أو تصريح رسمي
مهم أو تحرك عسكري/أمني يتعلق بأحدهم، فأدرج الحدث في قسم ليبيا.
لا تسقطه لمجرد أن أخباراً أخرى أكثر عدداً أو لأن الحدث سياسي/دبلوماسي.
مجرد ورود الاسم في تحليل أو خبر قديم أو ذكر عابر لا يكفي للإدراج.

ضع فيه الأحداث المتعلقة بليبيا، مع إعطاء أولوية واضحة لنوعين من الأخبار الإضافية التالية:

أولوية خاصة: متابعة القيادات السياسية والعسكرية والأمنية في طرفي الانقسام الليبي
- في الشرق الليبي: المشير خليفة حفتر، وابناه خالد حفتر وصدام حفتر، وأهم قادة القيادة العامة للقوات المسلحة والقيادات العسكرية والأمنية التابعة لها.
- في غرب ليبيا: رئيس حكومة الوحدة الوطنية، ورئيس المجلس الرئاسي، ووزير الداخلية، وقائد الأركان العامة، ورئيس جهاز الاستخبارات، وأهم القيادات العسكرية والأمنية التابعة للمؤسسات الرسمية.
- راجع جميع المواد المقدمة بحثاً عن أخبار هؤلاء المسؤولين، بما فيها الاجتماعات والاستقبالات والزيارات والقرارات والتعيينات والإقالات والتحركات العسكرية والأمنية والاتفاقات والتصريحات المهمة.
- أعطِ الأولوية للخبر الذي يوثق فعلاً أو قراراً أو اجتماعاً أو تحركاً محدداً، ولا تدرج مجرد ذكر الاسم أو تحليلاً عاماً أو خبراً قديماً بلا تطور جديد.
- لا تفترض أن أي مسؤول يشغل منصباً معيناً لمجرد ورود اسمه؛ استخدم الصفة التي تؤكدها المادة نفسها. لا تخترع أسماء أو مناصب أو صلات عائلية غير مذكورة بوضوح في المادة.
- إذا ظهرت أخبار مهمة عن هذه القيادات في المواد، فلا تسقطها لمجرد أنها سياسية أو دبلوماسية وليست اشتباكات أو عمليات عسكرية. ادمج التكرار، وحافظ على درجة اليقين، ولا تملأ القسم بأخبار هامشية.

أولاً: الاشتباكات المسلحة داخل ليبيا
- أدرج الاشتباكات المسلحة المؤكدة أو المنسوبة بوضوح إلى أطراف محددة، بما في ذلك الاشتباكات بين التشكيلات المسلحة أو الجماعات المحلية أو القوات الأمنية والعسكرية.
- صغ الخبر بصيغة مباشرة على النحو التالي، مع تعديل النحو بحسب الحالة:
  "اندلعت في منطقة [اسم المنطقة] اشتباكات مسلحة بين [الجهة/المجموعة الأولى] و[الجهة/المجموعة الثانية]، على خلفية [سبب الاشتباك أو هدفه إن كان معروفاً]، وأسفرت عن [عدد القتلى أو الجرحى إن ورد]."
- إذا لم يُعرف سبب الاشتباك، فلا تخمّنه؛ اكتب "في اشتباكات لم تُعرف أسبابها بعد" أو احذف الإشارة إلى السبب.
- اذكر أعداد القتلى والجرحى فقط إذا أكدتها المادة، وميّز بين المدنيين والعسكريين عند توفر المعلومة. إذا لم ترد حصيلة، لا تكتب حصيلة مفترضة.
- لا تصف الحادثة بأنها اشتباك إذا كانت المادة لا تثبت تبادلًا مسلحًا، ولا تعرض رواية طرف واحد على أنها حقيقة مؤكدة.

ثانياً: رصد حركة الطائرات العسكرية ووصولها إلى ليبيا
- أدرج التقارير الموثوقة التي تنشرها مواقع أو جهات متخصصة في تتبع الطيران أو رصد الرحلات العسكرية، بشأن هبوط طائرات شحن عسكري أو مقاتلات أو طائرات عسكرية أخرى في مطارات أو قواعد ليبية، وكذلك الرحلات العسكرية المتجهة إلى ليبيا.
- استخدم الصياغة التالية بحسب المعلومات المتاحة:
  "كشفت [الجهة أو الموقع المتخصص] عن هبوط طائرة [شحن عسكري/مقاتلة/نوع معروف] تابعة لـ[الدولة] في مطار [الاسم] بليبيا، في مهمة [طبيعة المهمة إن كانت معلومة]، أو محمّلة بـ[الحمولة إن كانت معلومة]."
- إذا لم تُكشف طبيعة المهمة أو الحمولة، قل بوضوح: "دون الكشف عن طبيعة مهمتها أو حمولتها".
- اذكر اسم موقع الرصد وتاريخ الرحلة ومطار الهبوط ونوع الطائرة أو تسجيلها متى كانت متاحة. ميّز بين ما رصدته بيانات التتبع وما استنتجه المصدر؛ لا تستنتج أن الطائرة تحمل أسلحة أو تنفذ مهمة قتالية دون دليل.
- لا تعتمد على منشورات مجهولة أو صور قديمة أو ادعاءات غير قابلة للتحقق. إذا كان التقرير يقول إن الطائرة يُرجح أنها هبطت، حافظ على درجة الشك ولا تصغه كحقيقة قطعية.
- لا تجعل هذين النوعين يزيحان أخبار ليبيا العسكرية والسياسية المهمة الأخرى؛ أعطِ الأولوية للوقائع المؤكدة والأكثر أهمية، وادمج التقارير المتعددة عن الحادثة نفسها في خبر واحد.

القسم الثاني:
"الجزائر"

قائمة أسماء رئيسية إلزامية للمتابعة في المواد الإخبارية:
عبد المجيد تبون، السعيد شنقريحة، محمد عرقاب، سيفي غريب،
أحمد عطاف، السعيد سعيود، الحاج بوسلجة.

افحص جميع المواد بحثاً عن هذه الأسماء وصيغ كتابتها اللاتينية الشائعة.
أدرج كل واقعة حديثة ومحددة تتعلق بأحدهم، مثل الاجتماعات والزيارات
والقرارات والتعيينات والإقالات والاتفاقات والتحركات العسكرية أو
الدبلوماسية المهمة. لا تدرج مجرد ذكر الاسم أو تحليلاً عاماً أو خبراً
قديماً بلا تطور جديد.

ضع فيه الأحداث المتعلقة بالجزائر.

القسم الثالث:
"المغرب"

قائمة أسماء رئيسية إلزامية للمتابعة في المواد الإخبارية:
محمد السادس، فاطمة الزهراء المنصوري، ناصر بوريطة،
عبد الوافي لفتيت، محمد بريظ.

افحص جميع المواد بحثاً عن هذه الأسماء وصيغ كتابتها اللاتينية الشائعة.
أدرج كل واقعة حديثة ومحددة تتعلق بأحدهم، مثل الاجتماعات والزيارات
والقرارات والتعيينات والإقالات والاتفاقات والتحركات العسكرية أو
الدبلوماسية المهمة. لا تدرج مجرد ذكر الاسم أو تحليلاً عاماً أو خبراً
قديماً بلا تطور جديد.

ضع فيه الأحداث المتعلقة بالمغرب.

في أقسام ليبيا والجزائر والمغرب، لا تقتصر على العمليات العسكرية
والأمنية. أدرج أيضاً التطورات السياسية والدبلوماسية المهمة عندما
تكون موثقة في المواد، ومنها:
- اجتماعات الرؤساء ورؤساء الحكومات والوزراء وكبار المسؤولين.
- الزيارات الرسمية والمحادثات الثنائية والقمم.
- توقيع الاتفاقيات والمعاهدات ومذكرات التفاهم أو التصديق عليها.
- التعاون الدفاعي والأمني والاستخباراتي والحدودي.
- الشراكات الاستراتيجية واللجان المشتركة والقرارات الحكومية ذات
  الأثر الأمني أو الإقليمي.

طبّق هذا المعيار أيضاً على بقية الدول الإفريقية والعالم عندما يكون
للتطور السياسي أو الدبلوماسي أثر استراتيجي واضح. لا تعتبر كل لقاء
سياسي خبراً مهماً تلقائياً؛ اختر ما له صلة فعلية بالأمن أو الدفاع
أو العلاقات الإقليمية أو التوازنات الاستراتيجية.

إذا كان القسم يفتقر إلى أخبار عسكرية، فابحث ضمن المواد المتاحة عن
تطورات سياسية ودبلوماسية ذات صلة قبل تركه فارغاً. لا تملأه بخبر
هامشي أو قديم، ولا تخترع أي تفاصيل.

القسم الرابع:
"الأحداث الكبرى في إفريقيا"

هنا لا نريد كل الأخبار الإفريقية.

أدرج فقط الأحداث الكبرى، مثل:
- انقلاب أو محاولة انقلاب
- وفاة رئيس أو شخصية سياسية/عسكرية عليا
- تعيين قائد عسكري أو مسؤول سياسي كبير
- عقود تسلح كبرى ومؤكدة
- عمليات عسكرية استراتيجية كبرى
- تطورات أمنية ذات تأثير إقليمي كبير
- تطورات سياسية ذات تأثير مباشر على الأمن والاستقرار

رتب الدول الإفريقية بحسب القارة الفرعية ثم أهمية الحدث.

الأولوية التنظيمية:
شمال إفريقيا
غرب إفريقيا
وسط إفريقيا
شرق إفريقيا
جنوب إفريقيا

مع تقديم الحدث الأهم داخل كل مجموعة.

القسم الخامس:
"مستجدات الإرهاب في القارة الإفريقية والعالم"

أدرج فقط التطورات المهمة المرتبطة بالإرهاب أو التنظيمات
الإرهابية أو العمليات الإرهابية أو مكافحة الإرهاب.

لا تستخدم الأخبار العامة غير المرتبطة بالإرهاب لمجرد ملء القسم.

القسم السادس:
"مستجدات الأوضاع في منطقة الشرق الأوسط"

أدرج التطورات المهمة في الشرق الأوسط فقط.

ركز على:
- الحروب
- العمليات العسكرية
- الضربات
- التطورات الأمنية الكبرى
- التحركات العسكرية
- الاتفاقات أو التصعيدات الاستراتيجية
- التطورات السياسية ذات الانعكاس الأمني الكبير

لا تملأ القسم بأخبار ثانوية.

القسم السابع:
"التطورات الكبرى للمنظمات الدولية"

خصص هذا القسم للتطورات المؤكدة والمهمة الصادرة عن أو المتعلقة
بالمنظمات الدولية الكبرى التالية:
- الأمم المتحدة، ولا سيما مجلس الأمن والأمين العام وعمليات حفظ السلام.
- حلف شمال الأطلسي (الناتو)، بما يشمل قرارات الدفاع الجماعي والانتشار
  العسكري والتمارين الكبرى والشراكات الأمنية.
- الاتحاد الأوروبي، بما يشمل القرارات السياسية والأمنية والدفاعية
  والعقوبات والبعثات والمساعدات العسكرية.
- مجموعة بريكس، بما يشمل القمم والقرارات المشتركة والتوسّع أو المبادرات
  ذات الأثر الجيوسياسي والاستراتيجي.
- الاتحاد الإفريقي، بما يشمل مجلس السلم والأمن والوساطات والبعثات
  وقرارات النزاعات والأزمات الإفريقية.

أدرج فقط التطورات ذات الأهمية الاستراتيجية أو الأمنية أو السياسية
الكبرى، مثل القرارات الرسمية، والقمم المهمة، والاتفاقيات، والعقوبات،
والعمليات أو البعثات، ومواقف المنظمات من الحروب والأزمات، وإصلاح
المؤسسات الدولية. لا تدرج البيانات الروتينية أو الأخبار الثانوية.
إذا كان الخبر متعلقاً مباشرة بدولة إفريقية أو بأزمة إقليمية إفريقية،
فيمكن إدراجه في هذا القسم إذا كان جوهره قراراً أو تحركاً للمنظمة،
ولا تكرره في قسم آخر إلا إذا كان هناك تطور منفصل مهم.

القسم الثامن:
"الأحداث الكبرى في بقية دول العالم"

كما في القسم الإفريقي، لا نريد كل الأخبار.

أدرج فقط الأحداث العالمية الكبرى:
- حروب وتصعيدات عسكرية كبرى
- تغييرات سياسية كبرى ذات أثر أمني
- تعيينات عسكرية عليا
- صفقات تسلح كبرى
- أحداث استراتيجية كبرى

رتب الدول بحسب القارة ثم أهمية الحدث.

============================================================
نماذج مرجعية لتطبيق معايير الإدراج على الأخبار المشابهة
============================================================

اعتبر الحالات التالية أمثلة مرجعية على أنواع التطورات التي ينبغي
ألا تسقطها عملية الاختيار إذا وردت في المواد بصيغة حديثة ومحددة
ومدعومة بمعلومات واضحة:

- تسليم دولة معدات مكافحة الإرهاب إلى تونس أو تقديم مساعدة أمنية
  محددة القيمة أو المعدات: يُدرج في «الأحداث الكبرى في إفريقيا»
  باعتباره تطوراً في قدرات دولة إفريقية، حتى إن لم يكن صفقة شراء.
- تقديم معدات أمن بحري أو مراقبة حدود إلى دولة إفريقية، ولا سيما
  إذا كانت قيمة المساعدة أو طبيعة المعدات معلومة: يُدرج في
  «الأحداث الكبرى في إفريقيا».
- تقدم جيش في جبهة قتال واستعادة أو انتزاع عدة مناطق وتدمير
  آليات عسكرية: يُدرج في القسم الجغرافي المعني، ولا يُختزل إلى
  خبر محلي أو متابعة ميدانية ثانوية.
- إعلان رسمي عن خطة لنشر آلاف الجنود الأجانب أو الاتفاق على
  إرسال قوات من دولة أخرى: يُدرج إذا كانت الأطراف أو الأعداد
  أو المهمة محددة، مع توضيح أنه إعلان أو خطة مستقبلية لا انتشار
  منجز.
- تهديدات قنابل متعددة تستهدف رحلات جوية تجارية أو تؤدي إلى
  تفتيش طائرات في أكثر من دولة: تُدرج كتطور أمني عابر للحدود
  يمس سلامة الطيران المدني، مع نسبة البلاغات إلى السلطات.
- صفقة لتحديث سفن حربية أو أسطول بحري لدولة إفريقية: تُدرج
  باعتبارها تطوراً في القدرات البحرية والصناعات الدفاعية، حتى
  إن لم تتضمن شراء سفن جديدة.
- إعلان قائد جيش أو رئيس أركان عن تنحيه أو استقالته أو تعيين
  خلف له: يُدرج كتطور سياسي-عسكري، مع الفصل بين القرار الرسمي
  والتكهنات حول الخلافة السياسية.
- مناورات للناتو أو تحالف عسكري بمشاركة آلاف الجنود: تُدرج
  ضمن تطورات المنظمة أو التحالف إذا كان حجمها ومكانها ومشاركوها
  محددين؛ لا تُعامل كتدريب روتيني صغير.
- ضبط مجموعة مرتبطة بتهريب صواريخ أو أسلحة عسكرية: يُدرج
  في القسم الجغرافي المعني، لأنه يمس انتشار السلاح والقدرات
  العسكرية، ولا يعامل كتوقيف جنائي اعتيادي.
- قصف سوق أو منطقة مدنية وسقوط عدد كبير من القتلى المدنيين
  في سياق حرب أو تصعيد عسكري: يُدرج في قسم النزاع المعني،
  مع توضيح تضارب الروايات إن وجد وعدم الجزم بالمسؤولية دون دليل.
- اعتقال مشتبه به بدور استخباراتي أو لوجستي لتنظيم إرهابي
  مثل مخبر أو وسيط: يُدرج عندما يكون الدور المزعوم محدداً
  ومرتبطاً بعملية مكافحة الإرهاب، لا لمجرد اعتقال شخص مجهول الصلة.

هذه أمثلة لتوسيع نطاق الرصد وليست أوامر بتصديق الادعاءات الواردة
في العناوين. تحقّق من الوصف والتفاصيل المتاحة، وانسب المزاعم
إلى مصدرها، واستبعد الخبر إذا كان مبهماً أو غير مؤكد أو مكرراً.

============================================================
============================================================
البطاقات السابقة
============================================================

هذه أحداث من بطاقات سابقة لأيام قريبة، وليست جزءاً من النسخة الحالية لليوم:

{previous_text}

لا تعِد نشر هذه الأحداث إلا إذا طرأ تطور جديد مهم.

============================================================
الأحداث الموجودة بالفعل في نسخة بطاقة اليوم المنشورة

{current_text}

هذه القائمة هي المحتوى الحالي الذي يجب تحديثه تراكمياً، وليست قائمة أخبار يجب إعادة إنشائها من الصفر:
- احتفظ بكل حدث قائم ما لم يثبت أنه مكرر أو خاطئ بوضوح.
- إذا لم تحمل المواد الجديدة تطوراً جديداً للحدث، احتفظ بصياغته وتفاصيله كما هي.
- إذا وردت تفاصيل موثوقة إضافية عن الحدث نفسه، حسّن صياغته وأضف التفاصيل الجديدة دون إسقاط التفاصيل السابقة.
- أضف كل حدث جديد مستقل من المواد الحالية.
- لا تحذف حدثاً لمجرد أنه لم يظهر في نتائج التحديث الحالي أو لأن النموذج لم يختره.
- دمج التكرارات مسموح، أما حذف الأحداث السابقة أو استبدال القائمة كاملة فممنوع.

============================================================
قائمة المراجعة الثانية ذات الأولوية
============================================================

رصدت آلية أولية المواد التالية لأنها تتضمن مؤشرات محتملة على تطورات
دفاعية أو ميدانية أو استخباراتية أو أمنية أو قيادية. هذه القائمة
ليست قراراً آلياً بالنشر، لكنها قائمة تدقيق إلزامية:

{strategic_candidate_text}

افحص كل مادة في هذه القائمة على حدة، ثم ارجع إلى نصها الكامل ضمن
المواد الإخبارية أدناه. لا تتجاوز أي مادة لمجرد أن خبرها لا يشبه
الأنماط التي اعتدت اختيارها. إذا كانت الواقعة محددة ومهمة فأدرجها،
وإذا استبعدتها فليكن ذلك لسبب تحريري حقيقي مثل ضعف التأكيد أو
التكرار أو محدودية الأهمية، لا بسبب نوع عنوانها أو صيغته.

============================================================
المواد الإخبارية للفترة الحالية
============================================================

{news_text}

============================================================
مهم جداً
============================================================

المواد أعلاه هي المصدر الوحيد المسموح باستخدامه.

إذا وجدت خبراً مكرراً من عدة مصادر، ادمجه في حدث واحد.

============================================================
قاعدة إلزامية لدمج الأخبار التي تتناول الحدث نفسه
============================================================

- قارن الأخبار داخل كل قسم قبل إخراج البطاقة، حتى لو اختلفت الصياغة أو المصدر.
- إذا تناول خبران الهجوم أو إطلاق الصواريخ أو الغارة أو الاقتحام أو القرار نفسه،
  فادمجهما في فقرة واحدة تحفظ التفاصيل المؤكدة غير المتعارضة.
- مثال: خبر عن اعتراض ثلاثة صواريخ باتجاه الرياض وخبر عن اعتراض الصواريخ
  الثلاثة نفسها واستمرار إغلاق مطار الملك خالد يجب أن يصبحا خبراً واحداً.
- لا تدمج الأخبار لمجرد أنها تخص الدولة نفسها أو تتناول التصعيد نفسه؛ أبقِ
  الوقائع المنفصلة جغرافياً أو زمنياً منفصلة.
- في أخبار فلسطين، ميّز بين غزة والضفة الغربية/رام الله. ادمج التكرار الذي
  يصف الواقعة نفسها، لكن لا تخلط حصائل أو مواقع منفصلة ولا تجمع أعداد الضحايا
  إلا إذا أكدت المواد أنها تخص الواقعة نفسها.
- عند التعارض في الأرقام، لا تخمّن؛ احتفظ بالمعلومة المؤكدة أو وضّح التعارض.
- المطلوب حدث واحد لكل واقعة، لا فقرة مستقلة لكل مصدر.

إذا وجدت عنواناً مثيراً لكنه لا يحتوي على معلومات كافية،
لا تستنتج تفاصيل غير موجودة.

راجع جميع المواد الإخبارية المقدمة واحدةً واحدة قبل إعداد البطاقة.
لا تتوقف بعد العثور على خبر واحد أو خبرين. أدرج كل تطور مهم ومختلف
وموثق يندرج في أحد الأقسام، مع دمج التكرار فقط. لا تُسقط خبراً
لمجرد أن موضوعه دبلوماسي أو سياسي وليس عملية عسكرية.

عند وجود مادة ذات صلة بليبيا أو الجزائر أو المغرب، ضعها في القسم
المخصص للدولة حتى لو كانت تتعلق باجتماع أو زيارة أو اتفاقية أو
تعاون إقليمي. وإذا لم توجد مادة مناسبة لدولة معينة ضمن الفترة،
اترك قسمها فارغاً ولا تستعن بأخبار من خارج الفترة.

لا تجعل عدد الأحداث هدفاً بحد ذاته، لكن لا تختصر البطاقة إلى حدث
واحد إذا كانت المواد تحتوي على تطورات أخرى مستقلة ومهمة.

راجع بصورة خاصة جميع المواد المتعلقة بالأمم المتحدة والناتو والاتحاد
الأوروبي وبريكس والاتحاد الإفريقي. لا تُسقط قراراً أو قمة أو تحركاً
استراتيجياً مهماً لهذه المنظمات لمجرد أن الخبر ليس عملية عسكرية. إذا
وجدت مادة مهمة وموثقة عن إحدى هذه المنظمات، أدرجها في قسم
"التطورات الكبرى للمنظمات الدولية" أو القسم الإقليمي الأنسب، مع عدم
تكرار الحدث نفسه.

تأكد قبل إخراج كل حدث من أن صياغته تبدأ بفعل واضح وطبيعي
متى كان ذلك ممكناً، وأنها لا تبدأ باسم شخص أو مؤسسة أو دولة.

============================================================
معيار إلزامي: الوقائع الملموسة لا التوقعات العامة
============================================================

قبل إدراج أي مادة، تحقّق من أنها تتناول واقعة محددة حدثت فعلاً، أو إجراءً
بدأ تنفيذه، أو قراراً رسمياً اتُّخذ، أو حدثاً مستقبلياً محدداً أُعلن عنه
رسمياً مع موعد أو مكان أو تفاصيل قابلة للتحقق.

============================================================
بوابة استبعاد إلزامية: لا تعليق عام ولا جريمة حق عام
============================================================

- لا تُدرج تعليقاً منسوباً إلى باحثين أو محللين أو خبراء عن أهمية لقاء
  أو احتمال أن يؤدي إلى التقريب بين أطراف أو تغيير موازين القوى؛ فهذا
  تحليل أو توقع وليس واقعة مستقلة. إذا كان اللقاء نفسه حدثاً موثقاً ومهماً،
  فصغ خبر اللقاء مباشرة من المادة الأصلية، ولا تنقل رأي المعلقين عنه.
- استبعد الصيغ من نوع: «أكد باحثون سياسيون أهمية المحادثات... مشيرين إلى
  إمكانية...» ما لم تتضمن المادة نفسها قراراً أو إجراءً جديداً ملموساً
  يمكن التحقق منه. لا تجعل مجرد ذكر مسؤولين أو محادثات في سياق رأي تحليلي
  سبباً كافياً لإدراج الخبر.
- استبعد أخبار الجرائم المحلية الفردية والجنح والسرقة والاعتداء والضرب
  والجرح والتوقيفات المرتبطة بها، بما فيها حوادث الاستدراج والسرقة بسلاح
  أبيض، ما لم تكشف المادة عن صلة واضحة بالإرهاب أو شبكة إجرامية منظمة
  عابرة للحدود أو تهديد أمني واسع أو تداعيات مباشرة على الأمن القومي.
- لا تعتبر كل خبر صادر عن الشرطة أو المصالح الأمنية خبراً أمنياً
  استراتيجياً. معيار الإدراج هو حجم التهديد والأثر الأمني، لا مجرد أن
  الجهة التي أعلنت الخبر هي الشرطة.
- أبقِ أخبار الإرهاب، والشبكات الإجرامية المنظمة، والاتجار بالبشر،
  والتهريب العابر للحدود، والأسلحة النارية أو المتفجرات، والعمليات
  الواسعة ذات الأثر الأمني؛ بشرط أن تكون الصلة موثقة في المادة.
- إذا شككت في أن الخبر يحقق هذا المعيار، فاستبعده بدلاً من خفض معيار
  البطاقة وإدراج خبر هامشي.
- استبعد المطالب النقابية والاحتجاجات المطلبية المتعلقة بالنقل والأسعار
  والدعم والوقود، والأخبار البيئية أو فعاليات المناخ الروتينية، ما لم
  تتضمن الواقعة نفسها أثراً أمنياً أو دفاعياً أو قراراً استراتيجياً واضحاً.
- لا تجعل ورود اسم رئيس حكومة أو منظمة دولية في خبر مدني أو اقتصادي
  سبباً كافياً لإدراجه؛ العبرة بموضوع الواقعة وأثرها، لا بالأسماء الواردة
  عرضاً فيها.

أعطِ الأولوية لما يمكن للقارئ أن يعرف منه بوضوح: من فعل ماذا؟ وأين؟ ومتى؟
وما النتيجة أو التفاصيل الملموسة؟ اذكر الأرقام والأسماء والمواقع والتواريخ
ونوع المعدات أو الوحدة أو الاتفاق كلما وردت في المادة.

استبعد أو لا تحوّل إلى خبر مستقل:
- التحليلات العامة والتقديرات والتوقعات بشأن ما قد تفعله دولة أو قوة عسكرية.
- عبارات النوايا والأهداف العامة مثل «تعزيز القدرات» أو «رفع الجاهزية» أو
  «توسيع النفوذ» إذا لم تقترن بإجراء محدد يمكن إثباته.
- الحديث عن تكثيف الانتشار أو التدريبات بصورة عامة من دون تحديد تمرين معروف،
  أو موعد أو موقع واضح أو وحدات مشاركة أو نشاط فعلي محدد.
- سيناريوهات محتملة أو تحذيرات افتراضية أو خطط غير مؤكدة لم تُعتمد رسمياً.
- المواد التي تعيد شرح استراتيجية قديمة من دون إعلان جديد أو تطور ملموس.

لا تستبعد حدثاً مستقبلياً لمجرد أنه لم يقع بعد إذا كان إجراءً محدداً ومؤكداً،
مثل مناورة معلنة رسمياً بموعد ومكان، أو توقيع اتفاق مقرر، أو نشر قوات أُعلن
عن تفاصيله. في هذه الحالة، وضّح أنه مقرر أو مرتقب ولا تصغه كأنه وقع بالفعل.

إذا كانت المادة تتضمن حقيقة ملموسة وتحليلاً عاماً معاً، احتفظ بالواقعة
المؤكدة فقط واحذف الاستنتاجات والتوقعات. وإذا لم تتضمن واقعة محددة أو
تفاصيل كافية، اتركها خارج البطاقة بدلاً من ملء الفراغ بصياغة استراتيجية فضفاضة.
لا تخترع تاريخاً أو مكاناً أو رقماً لتعويض نقص المادة.

مثال على صياغة تُستبعد إذا لم تتبعها تفاصيل قابلة للتحقق:
«تكثف القوات تدريباتها وانتشارها لتعزيز قدراتها على التدخل السريع ومراقبة
المجال البحري»؛ فهذه عبارة عامة ما لم تحدد المادة التدريب أو الانتشار الفعلي
وموعده ومكانه أو الوحدات المشاركة.

ممنوع منعاً باتاً إدراج أخبار كرة القدم أو أي رياضة أخرى، حتى لو وردت فيها
كلمات مثل «هجوم» أو «مهاجم» أو «دفاع». لا تعتبر المصطلحات الرياضية
أخباراً أمنية أو عسكرية، واستبعد الخبر الرياضي بالكامل.

النتيجة النهائية يجب أن تكون بطاقة استراتيجية مختصرة،
مهنية، واضحة، وقابلة للنشر مباشرة.

لا تكتب أي مقدمة خارج JSON.
لا تكتب أي تعليق خارج JSON.
"""

    return prompt


# ============================================================
# GEMINI GENERATION
# ============================================================

def generate_with_gemini(prompt):
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set.")

    response_schema = {
        "type": "object",
        "properties": {
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "events": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "country": {"type": "string"},
                                    "text": {"type": "string"}
                                },
                                "required": ["country", "text"]
                            }
                        }
                    },
                    "required": ["title", "events"]
                }
            }
        },
        "required": ["sections"]
    }

    # Use the same stable, high-throughput model as the other editorial
    # workflows. The former gemini-3.7-flash fallback is no longer a
    # dependable independent fallback and could resolve to the primary model.
    models = [MODEL_NAME]

    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=45000)
    )
    last_error = None

    for model_name in models:
        started = time.monotonic()
        print("=" * 70)
        print(f"Trying Gemini model: {model_name}")
        print("Per-model request timeout: 45 seconds")
        print("=" * 70)

        try:
            interaction = client.interactions.create(
                model=model_name,
                input=prompt,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": response_schema
                },
                generation_config={
                    "thinking_level": "low"
                }
            )

            output_text = interaction.output_text
            if not output_text:
                raise RuntimeError(
                    f"Gemini returned an empty response from {model_name}."
                )

            result = json.loads(output_text)
            if not isinstance(result, dict) or not isinstance(result.get("sections"), list):
                raise RuntimeError(
                    f"Gemini returned an invalid card structure from {model_name}."
                )

            print("=" * 70)
            print(
                f"Gemini generation succeeded with {model_name} "
                f"in {time.monotonic() - started:.1f}s"
            )
            print("=" * 70)
            return result

        except Exception as e:
            last_error = e
            print("=" * 70)
            print(
                f"Gemini model failed: {model_name} "
                f"after {time.monotonic() - started:.1f}s"
            )
            print(f"Error: {type(e).__name__}: {e}")
            print("=" * 70)
            error_text = f"{type(e).__name__}: {e}".lower()
            quota_error = any(marker in error_text for marker in (
                "429", "resource_exhausted", "quota", "rate limit",
                "rate_limit", "too many requests", "requests per day"
            ))
            if quota_error:
                raise RuntimeError(
                    f"Gemini quota/rate limit reached on {model_name}; "
                    "stopping without fallback to protect the other workflows."
                ) from e
            # For non-quota failures only, try the next configured model once.

    raise RuntimeError(
        "All configured Gemini models failed. Last error: "
        f"{type(last_error).__name__}: {last_error}"
    )

# ============================================================
# VALIDATION
# ============================================================

EXPECTED_SECTIONS = [
    ("libya", "ليبيا"),
    ("algeria", "الجزائر"),
    ("morocco", "المغرب"),
    ("africa_major", "الأحداث الكبرى في إفريقيا"),
    ("terrorism", "مستجدات الإرهاب في القارة الإفريقية والعالم"),
    ("middle_east", "مستجدات الأوضاع في منطقة الشرق الأوسط"),
    ("international_orgs", "التطورات الكبرى للمنظمات الدولية"),
    ("world_major", "الأحداث الكبرى في بقية دول العالم"),
]


def is_sports_related(text):
    """Final safety filter: do not publish sports stories in a security card."""
    value = str(text).lower()
    sports_terms = [
        "football", "soccer", "كرة القدم", "كروي", "كروية", "الدوري",
        "مباراة", "مباريات", "لاعب", "لاعبين", "لاعبو", "نادي", "أندية",
        "مدرب", "سجل هدفاً", "سجل هدفا", "أحرز هدفاً", "أحرز هدفا",
        "أهداف المباراة", "هدف المباراة", "هداف الدوري", "مهاجم كرة القدم",
        "ركلة جزاء", "champions league", "premier league", "la liga",
        "serie a", "bundesliga", "footballer", "striker", "goalkeeper",
        "scored", "league table", "transfer window", "match de football",
        "club de football", "championnat", "buteur", "attaquant"
    ]
    return any(term in value for term in sports_terms)


def is_editorially_eligible(text_value):
    """
    Hard editorial gate for daily strategic cards.
    Reject commentary/speculation presented as news and routine common crime.
    The same gate is applied to newly generated events and to older events
    during cumulative merging, so rejected items do not persist forever.
    """
    value = str(text_value or "").lower()

    # 1) Commentary about what an event might mean is not itself an event.
    commentary_sources = [
        "باحثون سياسيون", "باحثون", "محللون سياسيون", "محللون",
        "خبراء سياسيون", "خبراء", "مراقبون", "يرى محللون",
        "قال محللون", "يقول محللون", "analysts", "researchers",
        "political experts", "commentators", "experts say"
    ]
    speculation_markers = [
        "إمكانية", "امكانية", "قد يسهم", "قد يساهم", "قد تلعب",
        "قد يلعب", "يمكن أن", "يمكن ان", "من الممكن", "يرجح أن",
        "يرجح ان", "يتوقع أن", "يتوقع ان", "قد يؤدي", "قد تؤدي",
        "من شأنه أن", "من شأنها أن", "أهمية المحادثات",
        "أهمية اللقاء", "التقريب بين الأطراف", "التقريب بين الاطراف",
        "may help", "could help", "could play a role", "might",
        "potentially", "analysts say", "experts believe"
    ]
    if (any(term in value for term in commentary_sources)
            and any(term in value for term in speculation_markers)):
        return False

    # 2) Routine local/common-law crimes are not national-security events.
    ordinary_crime_markers = [
        "السرقة", "سرقة", "النشل", "الاعتداء بالضرب", "الضرب والجرح",
        "الضرب والجرح والسرقة", "استدراج شخص", "استدراج شخصين",
        "سرقة تحت التهديد", "السلاح الأبيض", "سلاح أبيض",
        "اعتداء جسدي", "جريمة حق عام", "جنحة", "جرائم حق عام",
        "robbery", "petty theft", "theft", "assault and theft",
        "common-law crime", "vol à l'arraché", "vol avec violence",
        "coups et blessures"
    ]
    routine_police_markers = [
        "أوقفت المصالح الأمنية", "أوقفت المصالح الامنية",
        "أوقفت الشرطة", "أوقفت عناصر الأمن", "أوقفت عناصر الامن",
        "أوقفته الشرطة", "أوقفته المصالح الأمنية", "توقيف فتاة",
        "توقيف شخص", "اعتقلت الشرطة", "أوقفت شخصا", "أوقفت شخصين",
        "arrested by police", "police arrested", "interpellé par la police"
    ]
    national_security_markers = [
        "إرهاب", "إرهابي", "خلية إرهابية", "خلية ارهابية",
        "تنظيم داعش", "تنظيم القاعدة", "جماعة مسلحة", "جماعات مسلحة",
        "هجوم مسلح", "هجوما مسلحا", "تفجير", "عبوة ناسفة",
        "أسلحة نارية", "اسلحة نارية", "ترسانة أسلحة", "اتجار بالبشر",
        "شبكة إجرامية منظمة", "شبكة اجرامية منظمة", "تهريب دولي",
        "تهريب عبر الحدود", "مخطط إرهابي", "مخطط ارهابي",
        "national security", "terrorist cell", "terrorism",
        "armed group", "organized criminal network", "human trafficking",
        "cross-border smuggling", "explosives", "firearms",
        "تهريب صواريخ", "تهريب أسلحة", "تهريب اسلحة",
        "تفجير سيارة مفخخة", "سيارة مفخخة", "محاولة اغتيال",
        "عميل روسي", "عميل استخباراتي", "جاسوس", "تجسس",
        "مخبر لجماعة", "مخبر لتنظيم", "بوكو حرام",
        "تهديدات بالقنابل", "تهديد بوجود قنبلة",
        "missile smuggling", "arms smuggling", "car bombing",
        "assassination attempt", "espionage", "spy", "informant",
        "bomb threat", "bomb threats", "terrorist organization"
    ]

    is_ordinary_crime = any(term in value for term in ordinary_crime_markers)
    is_routine_police_case = any(term in value for term in routine_police_markers)
    # Explicitly exclude routine civilian/economic/environmental stories that
    # may mention a government official or an international institution but
    # do not describe a security, defense, or strategic-policy development.
    routine_civilian_markers = [
        "تنسيقية النقابات", "النقابات الوطنية", "مطلب نقابي",
        "قطاع النقل الطرقي", "النقل الطرقي للبضائع",
        "الدعم الاستثنائي المباشر", "الدعم الاستثنائي",
        "ارتفاع أسعار المحروقات", "أسعار المحروقات",
        "احتجاجات بيئية", "نشطاء بيئيون", "ناشطون بيئيون",
        "حركة أيام الجمعة من أجل المستقبل", "مؤتمر الأمم المتحدة للمناخ",
        "مؤتمر المناخ", "فعاليات مؤتمر المناخ",
        "labor union", "union demands", "freight transport",
        "road freight", "fuel prices", "fuel price increase",
        "climate activists", "environmental activists",
        "climate conference", "climate summit",
        "friday for future", "fridays for future"
    ]
    # Routine health campaigns, local candidacies and ceremonial
    # anniversary messages are not strategic/security developments.
    routine_health_markers = [
        "حملة نوفمبر الأزرق", "حملة نوفمبر الازرق",
        "التوعية بسرطان البروستات", "سرطان البروستات",
        "الكشف المبكر عن سرطان البروستات",
        "prostate cancer awareness", "prostate cancer screening campaign"
    ]
    routine_local_election_markers = [
        "انتخابات أعضاء المجالس الشعبية البلدية",
        "انتخابات اعضاء المجالس الشعبية البلدية",
        "المجالس الشعبية البلدية والولائية",
        "ترشح لانتخابات أعضاء المجالس الشعبية",
        "municipal council elections", "candidacy for municipal councils"
    ]
    ceremonial_markers = [
        "رسائل تحيات بمناسبة إحياء الذكرى",
        "رسائل تهنئة بمناسبة إحياء الذكرى",
        "رسائل تحيات بمناسبة الذكرى",
        "congratulatory messages on the anniversary",
        "greetings on the anniversary",
        "مثل الملك في القمة الفرانكوفونية",
        "مثل الملك في القمة الفرنكوفونية",
        "represented the king at the francophonie summit"
    ]
    political_rumor_markers = [
        "نفى اعتزامه مغادرة إدارة",
        "نفى اعتزامه مغادرة الادارة",
        "denied plans to leave the administration",
        "denied he planned to leave the administration"
    ]
    generic_security_statement_markers = [
        "جزء لا يتجزأ من أمن واستقرار",
        "جزء لا يتجزأ من امن واستقرار",
        "an integral part of the security and stability"
    ]

    security_context_markers = [
        "إرهاب", "إرهابي", "تنظيم مسلح", "جماعة مسلحة",
        "هجوم مسلح", "اشتباك مسلح", "عملية عسكرية", "قوات الجيش",
        "القوات المسلحة", "وزارة الدفاع", "وزير الدفاع",
        "الأمن القومي", "التعاون الأمني", "أمن الحدود",
        "مكافحة الإرهاب", "استخبارات", "مخابرات",
        "military operation", "armed forces", "defense ministry",
        "defence ministry", "national security", "border security",
        "counter-terrorism", "military cooperation", "intelligence",
        "armed attack", "armed clashes",
        "معدات مكافحة الإرهاب", "معدات مكافحة الارهاب",
        "معدات الأمن البحري", "معدات الامن البحري",
        "معدات عسكرية", "مساعدات عسكرية", "مساعدات دفاعية",
        "تسليم معدات", "تسليم أسلحة", "تسليم اسلحة",
        "تعاون دفاعي", "تعاون عسكري", "اتفاق دفاعي",
        "تحديث السفن الحربية", "تحديث الأسطول", "تحديث الاسطول",
        "مكافحة الطائرات المسيّرة", "مكافحة الطائرات المسيرة",
        "مضادات الطائرات المسيّرة", "أنظمة الدفاع الجوي",
        "تهديدات بالقنابل", "تهديد بوجود قنبلة", "تهديد أمني للطيران",
        "تهريب صواريخ", "تهريب أسلحة", "تهريب اسلحة",
        "تجسس", "عميل استخباراتي", "مخبر", "بوكو حرام",
        "military aid", "defense assistance", "defence assistance",
        "security equipment", "maritime security equipment",
        "counterterrorism equipment", "military cooperation",
        "defense cooperation", "defence cooperation",
        "warship modernization", "naval modernization",
        "counter-drone", "air defense systems", "bomb threats",
        "aviation security threat", "missile smuggling",
        "arms smuggling", "espionage", "spy", "informant",
        "foreign troop deployment", "military exercises"
    ]

    is_security_exception = any(term in value for term in national_security_markers)
    has_security_context = is_security_exception or any(
        term in value for term in security_context_markers
    )

    if is_ordinary_crime and (is_routine_police_case or "سلاح أبيض" in value or "السلاح الأبيض" in value):
        if not has_security_context:
            return False

    if any(term in value for term in routine_civilian_markers) and not has_security_context:
        return False

    if any(term in value for term in routine_health_markers + routine_local_election_markers):
        return False

    # These conjunctions avoid excluding substantive summit decisions or
    # other genuine developments that merely mention the same words.
    if "رسائل تحيات" in value and "الذكرى" in value:
        return False
    if "مثل" in value and ("القمة الفرانكوفونية" in value or "القمة الفرنكوفونية" in value):
        return False
    if "نفى" in value and "اعتزامه مغادرة" in value and "إدارة" in value:
        return False
    if any(term in value for term in political_rumor_markers):
        return False

    # Reject generic statements about the importance of cooperation when
    # they do not report a concrete meeting, decision, agreement or action.
    vague_cooperation_markers = [
        "اهمية تعزيز التعاون", "أهمية تعزيز التعاون",
        "ضرورة تعزيز التعاون", "تعزيز التعاون في مجالات الامن والتنمية",
        "تعزيز التعاون في مجالات الأمن والتنمية",
        "أهمية التعاون الثلاثي في تعزيز الأمن والتنمية",
        "اهمية التعاون الثلاثي في تعزيز الامن والتنمية",
        "أهمية التعاون الثلاثي في مجالات الأمن والتنمية",
        "importance of trilateral cooperation in security and development"
    ]
    concrete_action_markers = [
        "بحث", "ناقش", "استقبل", "التقى", "اجتمع", "أجرى",
        "وقع", "وقّع", "اتفاق", "اتفاقية", "مذكرة تفاهم",
        "قرر", "قررت", "اعتمد", "أقر", "أقرت", "أطلق", "أطلقت",
        "أنشأ", "تأسيس", "دشن", "دشّن", "بدأ تنفيذ"
    ]

    # Generic declarations about security are not separate events unless
    # accompanied by a concrete decision, agreement, meeting or operation.
    if (any(term in value for term in generic_security_statement_markers)
            and not any(term in value for term in concrete_action_markers)):
        return False
    if (any(term in value for term in vague_cooperation_markers)
            and not any(term in value for term in concrete_action_markers)):
        return False

    return True


def event_priority(text_value):
    """Lower numbers are shown first within each section."""
    value = str(text_value).lower()

    political_terms = [
        "رئيس حكومة", "رئيس الحكومة", "رئيس المجلس الرئاسي", "رئيس الدولة",
        "رئيس الجمهورية", "رئيس الوزراء", "رئيس البرلمان", "رئيس مجلس النواب",
        "رئيس مجلس الوزراء", "وزير الخارجية", "وزير الداخلية", "وزير الدفاع",
        "وزير", "الحكومة", "المجلس الرئاسي", "الرئاسة", "البرلمان",
        "اجتماع", "اجتمع", "استقبل", "التقى", "لقاء", "مباحثات", "محادثات",
        "مشاورات", "زيارة رسمية", "اتفاق", "اتفاقية", "مذكرة تفاهم", "وقّع",
        "وقع", "أعلن اتفاق", "قرار حكومي", "مرسوم", "دبلوماسي", "دبلوماسية",
        "president", "prime minister", "head of government", "presidential council",
        "foreign minister", "interior minister", "cabinet", "government", "parliament",
        "official visit", "bilateral talks", "meeting with", "agreement", "signed",
        "président", "premier ministre", "chef du gouvernement", "conseil présidentiel",
        "ministre", "gouvernement", "parlement", "visite officielle", "accord", "entretiens"
    ]
    military_leader_terms = [
        "خليفة حفتر", "صدام حفتر", "خالد حفتر", "قائد الأركان", "رئيس الأركان",
        "قائد الجيش", "القائد العام", "رئيس جهاز الاستخبارات", "رئيس المخابرات",
        "قائد القوات", "قائد المنطقة العسكرية", "قيادة الأركان", "قيادات عسكرية",
        "chief of staff", "army chief", "military commander", "intelligence chief",
        "commander-in-chief", "chef d’état-major", "chef d'etat-major", "commandant en chef"
    ]
    field_terms = [
        "اشتباك", "اشتباكات", "معركة", "معارك", "هجوم", "هجمات", "غارة", "قصف",
        "عملية عسكرية", "عمليات عسكرية", "مناورة", "مناورات", "تدريب عسكري",
        "تدريبات عسكرية", "انتشار القوات", "تعزيزات عسكرية", "اعتراض صاروخ",
        "clashes", "fighting", "battle", "attack", "airstrike", "military operation",
        "military exercise", "drill", "deployment", "affrontements", "combats",
        "attaque", "frappe aérienne", "exercice militaire", "déploiement",
        "تقدم الجيش", "توسيع السيطرة", "استعادة مناطق", "انتزاع مناطق",
        "تدمير آليات", "تدمير مركبات عسكرية", "السيطرة على مناطق",
        "مناورات الناتو", "مناورات عسكرية", "نشر قوات أجنبية",
        "تنحي قائد الجيش", "استقالة قائد الجيش", "إقالة قائد الجيش",
        "تعيين قائد الجيش", "معدات عسكرية", "مساعدات عسكرية",
        "تسليم معدات", "تحديث السفن الحربية", "صفقة دفاعية",
        "أنظمة مكافحة المسيّرات", "معدات الأمن البحري",
        "military aid", "defense assistance", "security equipment",
        "territorial control", "seized areas", "recaptured areas",
        "destroyed military vehicles", "foreign troop deployment",
        "nato exercise", "military chief resigns", "army chief steps down",
        "warship modernization", "defense deal", "counter-drone systems"
    ]
    lower_priority_terms = [
        "هجرة", "مهاجرين", "الهجرة غير النظامية", "تهريب المهاجرين", "اعتراض مهاجرين",
        "رحلة جوية", "تتبع الطيران", "رصد الطيران", "هبوط طائرة", "طائرة شحن",
        "flight tracking", "aircraft tracking", "landed at", "migrant", "migration",
        "smuggling", "suivi des vols", "avion cargo", "migrants"
    ]

    priority_official_terms = [
        "خليفة حفتر", "صدام حفتر", "خالد حفتر", "عبد الحميد الدبيبة",
        "عبدالحميد الدبيبة", "محمد المنفي", "عبد المجيد مليقطة",
        "عبدالمجيد مليقطة", "الطاهر الباعور", "عبد السلام الزوبي",
        "عبدالسلام الزوبي", "محمود حمزة",
        "عبد المجيد تبون", "عبدالمجيد تبون", "السعيد شنقريحة", "محمد عرقاب",
        "سيفي غريب", "أحمد عطاف", "احمد عطاف", "السعيد سعيود", "الحاج بوسلجة",
        "محمد السادس", "فاطمة الزهراء المنصوري", "ناصر بوريطة",
        "عبد الوافي لفتيت", "عبدالوافي لفتيت", "محمد بريظ",
        "khalifa haftar", "saddam haftar", "khaled haftar",
        "abdelmadjid tebboune", "said chengriha", "mohamed arkab",
        "sifi ghrieb", "ahmed attaf", "said sayoud", "nasser bourita",
        "abdelouafi laftit", "mohamed berrid"
    ]

    if any(term in value for term in priority_official_terms):
        return 0
    if any(term in value for term in political_terms):
        return 0
    if any(term in value for term in military_leader_terms):
        return 1
    if any(term in value for term in field_terms):
        return 2
    if any(term in value for term in lower_priority_terms):
        return 3
    return 4


def normalize_sections(generated):

    incoming = generated.get("sections", [])

    by_id = {}

    # Map section titles to their expected IDs.
    title_to_id = {
        "ليبيا": "libya",
        "الجزائر": "algeria",
        "المغرب": "morocco",
        "الأحداث الكبرى في إفريقيا": "africa_major",
        "مستجدات الإرهاب في القارة الإفريقية والعالم": "terrorism",
        "مستجدات الأوضاع في منطقة الشرق الأوسط": "middle_east",
        "التطورات الكبرى للمنظمات الدولية": "international_orgs",
        "الأحداث الكبرى في بقية دول العالم": "world_major"
    }

    for section in incoming:

        if not isinstance(section, dict):
            continue

        # First try to use the ID returned by Gemini.
        section_id = str(
            section.get("id", "")
        ).strip()

        # If Gemini did not return an ID,
        # determine it from the section title.
        if not section_id:

            section_title = str(
                section.get("title", "")
            ).strip()

            section_id = title_to_id.get(
                section_title,
                ""
            )

        if section_id:
            by_id[section_id] = section

    final_sections = []

    for section_id, title in EXPECTED_SECTIONS:

        source = by_id.get(
            section_id,
            {}
        )

        events = source.get(
            "events",
            []
        )

        clean_events = []

        if isinstance(events, list):

            for event in events:

                if not isinstance(event, dict):
                    continue

                country = str(
                    event.get("country", "")
                ).strip()

                text_value = str(
                    event.get("text", "")
                ).strip()

                if not text_value:
                    continue

                # Never allow sports news into a strategic/security daily card.
                if is_sports_related(country + " " + text_value):
                    print(f"Filtered sports item from daily card: {text_value[:120]}")
                    continue

                # Enforce hard editorial eligibility after model generation.
                if not is_editorially_eligible(country + " " + text_value):
                    print(f"Filtered low-value/speculative or routine-crime item: {text_value[:160]}")
                    continue

                # Basic protection against accidental URLs
                text_value = text_value.replace(
                    "http://",
                    ""
                ).replace(
                    "https://",
                    ""
                )

                clean_events.append({
                    "country": country,
                    "text": text_value
                })

        # Deduplicate within the newly generated section as well as when
        # merging with an already-published card. This matters on the first
        # generation of a day, when there is no previous card to merge against.
        clean_events = merge_event_lists([], clean_events)

        # Keep relevant events ordered by editorial priority, then original order.
        clean_events = [
            event for _, event in sorted(
                enumerate(clean_events),
                key=lambda pair: (event_priority(pair[1].get("text", "")), pair[0])
            )
        ]

        final_sections.append({
            "id": section_id,
            "title": title,
            "events": clean_events
        })

    return route_geographic_events(route_priority_events(final_sections))

# ============================================================
# ACCUMULATIVE DAILY-CARD MERGING
# ============================================================

def normalize_event_for_match(value):
    """Normalize Arabic/Latin text for conservative event matching."""
    import re
    import unicodedata

    text = unicodedata.normalize("NFKD", str(value or "").lower())
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[\u064B-\u065F\u0670\u0640]", "", text)
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    text = text.replace("ى", "ي").replace("ة", "ه")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def news_item_search_text(item):
    """Combine translated and original headlines/descriptions for name matching."""
    fields = (
        "title", "description", "original_title", "original_description",
        "source"
    )
    return " ".join(str(item.get(field, "") or "") for field in fields)


def priority_section_for_text(value):
    """Return the country section associated with a listed senior official."""
    normalized = normalize_event_for_match(value)
    if not normalized:
        return None

    # Match full names (not isolated first or family names) to avoid false positives.
    for section_id, aliases in PRIORITY_OFFICIALS.items():
        for alias in aliases:
            normalized_alias = normalize_event_for_match(alias)
            if normalized_alias and normalized_alias in normalized:
                return section_id
    return None


def route_priority_events(sections):
    """Move listed officials' events into their corresponding country section."""
    if not isinstance(sections, list):
        return sections

    by_id = {
        section.get("id"): section
        for section in sections
        if isinstance(section, dict)
    }

    for section in sections:
        if not isinstance(section, dict):
            continue
        source_id = section.get("id")
        retained = []
        for event in section.get("events", []):
            if not isinstance(event, dict):
                continue
            destination_id = priority_section_for_text(
                str(event.get("country", "")) + " " + str(event.get("text", ""))
            )
            if destination_id and destination_id != source_id and destination_id in by_id:
                target = by_id[destination_id]
                target["events"] = merge_event_lists(
                    target.get("events", []),
                    [event]
                )
                print(
                    f"Routed named-official event from {source_id} "
                    f"to {destination_id}: {str(event.get('text', ''))[:100]}"
                )
            else:
                retained.append(event)
        section["events"] = retained

    # Final card-wide deduplication: the same concrete incident should not
    # appear in multiple sections just because it was classified differently.
    # Keep the first occurrence in the configured section order; retain other
    # events from the same country when they describe distinct incidents.
    seen = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        kept = []
        for event in section.get("events", []):
            if not isinstance(event, dict):
                continue
            country = normalize_country_for_match(event.get("country", ""))
            text_value = str(event.get("text", "")).strip()
            duplicate = False
            for prior_country, prior_text in seen:
                # Exact normalized text is always a duplicate, even when
                # the same cross-border event was tagged with two countries.
                if normalize_event_for_match(prior_text) == normalize_event_for_match(text_value):
                    duplicate = True
                    print(
                        f"Removed exact cross-section duplicate event: "
                        f"{text_value[:120]}"
                    )
                    break
                if country and prior_country and country != prior_country:
                    continue
                if same_event_text(prior_text, text_value):
                    duplicate = True
                    print(
                        f"Removed cross-section duplicate event: "
                        f"{text_value[:120]}"
                    )
                    break
            if not duplicate:
                kept.append(event)
                seen.append((country, text_value))
        section["events"] = kept

    return sections


def same_event_text(left, right):
    """
    Match duplicate reports of the same concrete event, not merely the
    same country or broad topic. Use text similarity plus event-specific
    action, entity, place, and incident-detail anchors.
    """
    from difflib import SequenceMatcher

    a = normalize_event_for_match(left)
    b = normalize_event_for_match(right)
    if not a or not b:
        return False
    if a == b:
        return True

    tokens_a = {token for token in a.split() if len(token) > 2}
    tokens_b = {token for token in b.split() if len(token) > 2}
    common = tokens_a & tokens_b
    union = tokens_a | tokens_b
    jaccard = len(common) / max(1, len(union))
    ratio = SequenceMatcher(None, a, b).ratio()

    # Very similar full descriptions are usually alternate versions of
    # the same report. Avoid matching just because a short phrase is contained.
    if ratio >= 0.76 or (len(common) >= 7 and jaccard >= 0.52):
        return True

    # Event anchors are deliberately specific. A country name alone, or
    # a generic word such as "meeting", "attack", or "agreement", is not enough.
    anchor_groups = [
        ["صواريخ باليستية", "صاروخ باليستي", "صواريخ بالستية", "صاروخ بالستي",
         "صواريخ", "missile", "missiles"],
        ["الحوثيون", "الحوثي", "مليشيا الحوثي", "جماعة الحوثي", "houthis", "houthi"],
        ["مطار الملك خالد", "مطار الرياض", "king khalid airport", "riyadh airport"],
        ["قصف", "غارة جوية", "غارات جوية", "ضربة جوية", "ضربات جوية",
         "airstrike", "air strikes", "bombed"],
        ["اشتباكات", "اشتباك مسلح", "معارك", "قتال", "clashes", "fighting"],
        ["اعتراض", "أسقط", "أسقطت", "دمر", "دمرت", "intercepted", "shot down"],
        ["طائرة شحن", "طائرة عسكرية", "مقاتلة", "cargo aircraft", "military aircraft", "fighter jet"],
        ["استقبل", "التقى", "بحث مع", "مباحثات", "اجتمع مع", "استعرض مع",
         "received", "met with", "held talks", "meeting with"],
        ["وقّع", "وقع", "أبرم", "مذكرة تفاهم", "اتفاقية", "عقد دفاعي",
         "signed", "agreement", "memorandum of understanding", "defense contract"],
        ["عيّن", "عين", "تعيين", "أقال", "إقالة", "appointed", "dismissed", "named as"],
        ["أعلن", "أعلنت", "قرر", "قررت", "وافق", "وافقت",
         "announced", "decided", "approved"],
        ["اعتقال", "أوقف", "أوقفت", "احتجز", "arrested", "detained"],
        ["هدم", "نسف", "تدمير مبان", "demolished", "demolition"],
        ["إطلاق نار", "برصاص", "نيران الجيش", "gunfire", "shot by"],
        ["وقف إطلاق النار", "هدنة", "تمديد الهدنة", "ceasefire", "truce"],
        ["ضحايا", "قتلى", "مصابين", "جرحى", "killed", "wounded", "injured", "casualties"],
        ["ناقلات النفط", "ناقلات نفط", "ناقلة نفط", "ناقلة النفط", "oil tankers", "oil tanker"],
        ["حريق", "حرائق", "حرايق", "اندلاع حرائق", "اندلاع حرايق",
         "اشتعلت النيران", "caught fire", "fires broke out"],
    ]

    shared_groups = sum(
        1 for group in anchor_groups
        if any(term in a for term in group) and any(term in b for term in group)
    )

    place_groups = [
        ["الرياض", "riyadh"], ["مطار الملك خالد", "king khalid"],
        ["غزة", "gaza"], ["رام الله", "ramallah"],
        ["الضفة الغربية", "west bank"], ["جنوب لبنان", "southern lebanon"],
        ["بيروت", "beirut"], ["البحر الأحمر", "red sea"],
        ["طرابلس", "tripoli"], ["بنغازي", "benghazi"],
        ["الجزائر العاصمة", "algiers"], ["الرباط", "rabat"],
        ["الساحل", "sahel"], ["بحيرة تشاد", "lake chad"],
        ["مضيق هرمز", "strait of hormuz"], ["باب المندب", "bab al mandab"],
    ]
    shared_place = any(
        any(place in a for place in group) and any(place in b for place in group)
        for group in place_groups
    )

    specifics = [
        "خليفة حفتر", "صدام حفتر", "خالد حفتر", "عبد الحميد الدبيبة",
        "محمد المنفي", "عبد المجيد تبون", "السعيد شنقريحة", "محمد السادس",
        "ناصر بوريطة", "مطار الملك خالد", "f 35", "f 16", "jf 17",
        "iris t", "s 400", "باتريوت", "tobruk", "misrata",
    ]
    shared_named_specific = any(term in a and term in b for term in specifics)
    number_tokens = set(
        token for token in tokens_a & tokens_b
        if any(ch.isdigit() for ch in token)
    )
    shared_numbers = bool(number_tokens)

    # Require action overlap plus a location, named entity, or shared details.
    # This keeps distinct events in one country apart.
    return (
        shared_groups >= 2 and
        (shared_place or shared_named_specific or shared_numbers)
    )


def normalize_country_for_match(value):
    """Normalize common country-name variants before comparing event records."""
    value = normalize_event_for_match(value)
    aliases = {
        "saudi arabia": "السعوديه",
        "kingdom of saudi arabia": "السعوديه",
        "المملكه العربيه السعوديه": "السعوديه",
        "السعوديه العربيه": "السعوديه",
        "ksa": "السعوديه",
        "united states": "الولايات المتحده",
        "usa": "الولايات المتحده",
        "us": "الولايات المتحده",
        "united states of america": "الولايات المتحده",
        "u s": "الولايات المتحده",
        "israel": "اسرائيل",
        "state of israel": "اسرائيل",
        "palestine": "فلسطين",
        "state of palestine": "فلسطين",
        "libya": "ليبيا",
        "algeria": "الجزائر",
        "morocco": "المغرب",
        "tunisia": "تونس",
    }
    return aliases.get(value, value)


def route_geographic_events(sections):
    """
    Correct obvious section mismatches using the event's stated country or
    institution. Terrorism remains a dedicated global section; named
    Libya/Algeria/Morocco coverage keeps its country-specific priority.
    """
    if not isinstance(sections, list):
        return sections

    by_id = {section.get("id"): section for section in sections if isinstance(section, dict)}
    africa = {
        "مصر", "egypt", "السودان", "sudan", "جنوب السودان", "south sudan",
        "تونس", "tunisia", "موريتانيا", "mauritania", "السنغال", "senegal",
        "غامبيا", "gambia", "غينيا", "guinea", "غينيا بيساو", "guinea bissau",
        "سيراليون", "sierra leone", "ليبيريا", "liberia", "ساحل العاج",
        "cote divoire", "ivory coast", "غانا", "ghana", "توغو", "togo",
        "بنين", "benin", "بوركينا فاسو", "burkina faso", "مالي", "mali",
        "النيجر", "niger", "نيجيريا", "nigeria", "الكاميرون", "cameroon",
        "تشاد", "chad", "افريقيا الوسطى", "جمهورية افريقيا الوسطى",
        "central african republic", "الغابون", "gabon", "غينيا الاستوائية",
        "equatorial guinea", "الكونغو", "congo", "جمهورية الكونغو الديمقراطية",
        "democratic republic of the congo", "الكونغو الديمقراطية",
        "اوغندا", "uganda", "كينيا", "kenya", "تنزانيا", "tanzania",
        "رواندا", "rwanda", "بوروندي", "burundi", "اثيوبيا", "ethiopia",
        "اريتريا", "eritrea", "الصومال", "somalia", "جيبوتي", "djibouti",
        "جنوب افريقيا", "south africa", "زامبيا", "zambia", "زيمبابوي",
        "zimbabwe", "موزمبيق", "mozambique", "انغولا", "angola",
        "ناميبيا", "namibia", "بوتسوانا", "botswana", "مدغشقر", "madagascar",
        "مالاوي", "malawi", "ليسوتو", "lesotho", "اسواتيني", "eswatini"
    }
    middle_east = {
        "فلسطين", "palestine", "اسرائيل", "israel", "الاردن", "jordan",
        "لبنان", "lebanon", "سوريا", "syria", "العراق", "iraq",
        "ايران", "iran", "السعوديه", "saudi arabia", "اليمن", "yemen",
        "الامارات", "united arab emirates", "uae", "قطر", "qatar",
        "الكويت", "kuwait", "البحرين", "bahrain", "عمان", "oman",
        "تركيا", "turkey"
    }
    organizations = {
        "الامم المتحده", "united nations", "مجلس الامن", "un security council",
        "حلف الناتو", "حلف شمال الاطلسي", "nato",
        "الاتحاد الاوروبي", "european union", "eu",
        "الاتحاد الافريقي", "african union", "au",
        "بريكس", "brics"
    }
    terrorism_terms = (
        "ارهاب", "ارهابي", "ارهابيه", "تنظيم داعش", "تنظيم القاعده",
        "بوكو حرام", "داعش", "جماعه مسلحه", "هجوم ارهابي",
        "عمليه ارهابيه", "terrorism", "terrorist", "isis", "islamic state",
        "al qaeda", "boko haram", "iswap", "jihadist"
    )
    major_africa_terms = (
        "انقلاب", "محاوله انقلاب", "رئيس الاركان", "قائد الجيش",
        "قائد القوات المسلحه", "مقتل قائد", "مقتل رئيس الاركان",
        "عقد تسلح", "صفقه تسلح", "صفقه عسكريه", "اتفاق دفاعي",
        "عمليه عسكريه واسعه", "هجوم واسع", "حاله الطوارئ",
        "تمرد عسكري", "اغتيال مسؤول", "وفاه الرئيس", "استقاله الرئيس",
        "military coup", "coup attempt", "chief of staff", "arms deal",
        "defense agreement", "major military operation"
    )

    for section in sections:
        if not isinstance(section, dict):
            continue
        source_id = section.get("id")
        retained = []
        for event in section.get("events", []):
            if not isinstance(event, dict):
                continue
            country = normalize_country_for_match(event.get("country", ""))
            text_value = str(event.get("text", "") or "")
            normalized_text = normalize_event_for_match(text_value)
            target_id = None

            if country in organizations:
                target_id = "international_orgs"
            elif source_id != "terrorism" and source_id not in ("libya", "algeria", "morocco") and any(
                term in normalized_text for term in terrorism_terms
            ):
                target_id = "terrorism"
            elif country in middle_east and source_id != "terrorism":
                target_id = "middle_east"
            elif country in ("ليبيا", "libya"):
                target_id = "libya"
            elif country in ("الجزائر", "algeria"):
                target_id = "algeria"
            elif country in ("المغرب", "morocco"):
                target_id = "morocco"
            elif country in africa and source_id in ("libya", "algeria", "morocco"):
                target_id = "africa_major"
            elif country in africa and source_id == "world_major" and any(
                term in normalized_text for term in major_africa_terms
            ):
                target_id = "africa_major"

            if target_id and target_id != source_id and target_id in by_id:
                by_id[target_id]["events"] = merge_event_lists(
                    by_id[target_id].get("events", []), [event]
                )
                print(f"Corrected section for {country or 'unidentified country'}: {source_id} -> {target_id}")
            else:
                retained.append(event)
        section["events"] = retained

    return sections


def merge_event_lists(existing_events, incoming_events):
    """
    Keep every previously published event. New items are appended; likely
    updates to an existing event replace it only when the new wording is richer.
    """
    merged = []
    for event in existing_events if isinstance(existing_events, list) else []:
        if isinstance(event, dict) and str(event.get("text", "")).strip():
            old_country = str(event.get("country", "")).strip()
            old_text = str(event.get("text", "")).strip()
            if is_sports_related(old_country + " " + old_text):
                continue
            if not is_editorially_eligible(old_country + " " + old_text):
                continue
            merged.append({
                "country": old_country,
                "text": old_text
            })

    for event in incoming_events if isinstance(incoming_events, list) else []:
        if not isinstance(event, dict):
            continue
        incoming_text = str(event.get("text", "")).strip()
        if not incoming_text:
            continue
        incoming_country = str(event.get("country", "")).strip()

        match_index = None
        for index, old_event in enumerate(merged):
            old_text_for_match = str(old_event.get("text", ""))
            # Exact descriptions identify the same incident regardless of
            # whether sources tagged a cross-border event under two countries.
            if normalize_event_for_match(old_text_for_match) == normalize_event_for_match(incoming_text):
                match_index = index
                break

            old_country = normalize_country_for_match(old_event.get("country", ""))
            new_country = normalize_country_for_match(incoming_country)
            if old_country and new_country and old_country != new_country:
                continue
            if same_event_text(old_text_for_match, incoming_text):
                match_index = index
                break

        if match_index is None:
            merged.append({"country": incoming_country, "text": incoming_text})
            continue

        old_event = merged[match_index]
        old_text = str(old_event.get("text", "")).strip()
        old_tokens = set(normalize_event_for_match(old_text).split())
        incoming_tokens = set(normalize_event_for_match(incoming_text).split())

        # Prefer a genuinely enriched update even when its wording is not
        # substantially longer. This lets new dates, figures, actors and
        # operational details update the existing event instead of creating
        # a second copy or leaving stale wording in place.
        incoming_is_richer = (
            len(incoming_text) > len(old_text) * 1.08
            or (
                len(incoming_text) >= len(old_text) * 0.85
                and len(incoming_tokens) > len(old_tokens)
                and len(incoming_tokens - old_tokens) >= 2
            )
        )

        if incoming_is_richer and incoming_text != old_text:
            old_event["text"] = incoming_text
            if incoming_country:
                old_event["country"] = incoming_country
            print(f"Enriched existing event with new details: {incoming_text[:120]}")
        elif not old_event.get("country") and incoming_country:
            old_event["country"] = incoming_country

    # Sort after merging, but do not drop lower-ranked items.
    return [
        event for _, event in sorted(
            enumerate(merged),
            key=lambda pair: (event_priority(pair[1].get("text", "")), pair[0])
        )
    ]


def merge_card_sections(existing_sections, incoming_sections):
    """Union existing and newly generated sections without deleting old events."""
    old_by_id = {
        section.get("id"): section
        for section in existing_sections if isinstance(section, dict)
    } if isinstance(existing_sections, list) else {}
    new_by_id = {
        section.get("id"): section
        for section in incoming_sections if isinstance(section, dict)
    } if isinstance(incoming_sections, list) else {}

    merged_sections = []
    for section_id, title in EXPECTED_SECTIONS:
        old_section = old_by_id.get(section_id, {})
        new_section = new_by_id.get(section_id, {})
        events = merge_event_lists(
            old_section.get("events", []),
            new_section.get("events", [])
        )
        merged_sections.append({
            "id": section_id,
            "title": title,
            "events": events
        })
    return route_geographic_events(route_priority_events(merged_sections))


def load_current_day_events(reference_date):
    """Return the existing version of today's card to guide enrichment."""
    cards = load_previous_cards()
    for card in reversed(cards):
        if card.get("date") == reference_date.isoformat() and card.get("slot") == "daily":
            result = []
            for section in card.get("sections", []):
                for event in section.get("events", []):
                    if isinstance(event, dict) and event.get("text"):
                        result.append(
                            f"[{section.get('title', '')}] {event.get('country', '')}: {event.get('text', '')}"
                        )
            return result
    return []


# ============================================================
# SAVE CARD
# ============================================================

def update_cards_file(card):

    existing = load_json(CARDS_FILE, {"cards": []})
    if not isinstance(existing, dict):
        existing = {"cards": []}

    cards = existing.get("cards", [])
    if not isinstance(cards, list):
        cards = []

    # Merge against the currently saved card before replacing its JSON record.
    # This is the main safeguard against a sparse model response erasing events.
    prior = next((
        item for item in reversed(cards)
        if item.get("date") == card.get("date")
        and item.get("slot") == card.get("slot")
    ), None)
    if prior:
        card["sections"] = merge_card_sections(
            prior.get("sections", []),
            card.get("sections", [])
        )

    cards = [
        item for item in cards
        if not (
            item.get("date") == card.get("date")
            and item.get("slot") == card.get("slot")
        )
    ]
    cards.append(card)
    cards.sort(key=lambda item: (item.get("date", ""), item.get("slot", "")))
    cards = cards[-90:]

    save_json(CARDS_FILE, {
        "updated": datetime.now(timezone.utc).isoformat(),
        "cards": cards
    })
    return card


def save_archive(card):

    archive_date = card["date"]
    archive_path = ARCHIVE_DIR / archive_date / f"{card['slot']}.json"

    # The archive is also cumulative, even if cards.json was incomplete.
    archived_card = load_json(archive_path, None)
    if isinstance(archived_card, dict):
        card["sections"] = merge_card_sections(
            archived_card.get("sections", []),
            card.get("sections", [])
        )

    save_json(archive_path, card)

    # Never delete other files from the date directory. Older period cards
    # and prior formats are historical records, not disposable duplicates.
    save_archive_index()


def save_archive_index():

    years = {}

    if ARCHIVE_DIR.exists():

        for date_dir in sorted(ARCHIVE_DIR.iterdir()):

            if not date_dir.is_dir():
                continue

            try:
                parsed_date = datetime.strptime(
                    date_dir.name,
                    "%Y-%m-%d"
                )
            except ValueError:
                continue

            year = str(parsed_date.year)
            month = f"{parsed_date.month:02d}"

            for card_file in sorted(date_dir.glob("*.json")):

                try:
                    with open(card_file, "r", encoding="utf-8") as f:
                        card = json.load(f)
                except Exception as e:
                    print(f"Warning: could not index {card_file}: {e}")
                    continue

                slot = str(card.get("slot", card_file.stem))
                period = str(card.get("period", ""))
                title = str(
                    card.get("title")
                    or f"بطاقة يوم {parsed_date.strftime('%d/%m/%Y')}"
                )

                year_entry = years.setdefault(
                    year,
                    {"year": year, "months": {}}
                )

                month_entry = year_entry["months"].setdefault(
                    month,
                    {
                        "month": month,
                        "label": parsed_date.strftime("%m"),
                        "cards": []
                    }
                )

                month_entry["cards"].append({
                    "date": date_dir.name,
                    "slot": slot,
                    "period": period,
                    "title": title,
                    "path": f"{date_dir.name}/{card_file.name}"
                })

    output_years = []

    for year in sorted(years.keys(), reverse=True):

        year_data = years[year]
        months = []

        for month in sorted(year_data["months"].keys(), reverse=True):

            month_data = year_data["months"][month]
            month_data["cards"].sort(
                key=lambda c: (c.get("date", ""), c.get("slot", "")),
                reverse=True
            )
            months.append(month_data)

        output_years.append({
            "year": year,
            "months": months
        })

    save_json(
        ARCHIVE_DIR / "index.json",
        {"updated": datetime.now(TIMEZONE).isoformat(), "years": output_years}
    )


def fingerprint_news_items(items):
    """Stable digest of the exact source packet; unchanged feeds need no Gemini call."""
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(json.dumps(
            item, ensure_ascii=False, sort_keys=True, default=str,
            separators=(",", ":")
        ))
    normalized.sort()
    payload = "\n".join(normalized).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


# ============================================================
# MAIN
# ============================================================

def main():

    # --------------------------------------------------------
    # Current local time
    # --------------------------------------------------------

    now = datetime.now(
        TIMEZONE
    )

    # --------------------------------------------------------
    # Slot
    # --------------------------------------------------------

    slot = os.getenv(
        "CARD_SLOT",
        ""
    ).strip()

    # Manual command:
    # python generate_daily_card.py 16
    if len(sys.argv) >= 2:
        slot = sys.argv[1].strip()

    if slot not in PERIODS:

        detected = determine_slot(now)

        if detected:
            slot = detected

        else:
            raise SystemExit(
                "Could not determine the daily card. Use CARD_SLOT=daily."
            )

    # --------------------------------------------------------
    # Date
    # --------------------------------------------------------

    date_argument = os.getenv(
        "CARD_DATE",
        ""
    ).strip()

    if len(sys.argv) >= 3:
        date_argument = sys.argv[2].strip()

    if date_argument:

        try:
            reference_date = datetime.strptime(
                date_argument,
                "%Y-%m-%d"
            ).date()

        except ValueError:
            raise SystemExit(
                "CARD_DATE must be YYYY-MM-DD."
            )

    else:
        reference_date = now.date()

    # --------------------------------------------------------
    # Period
    # --------------------------------------------------------

    start, end = get_period_dates(
        slot,
        reference_date
    )

    # The card is first generated at 06:00, but it must include all
    # news published on the same Tunisia calendar date, including 00:00–06:00.
    generation_start = datetime(
        reference_date.year, reference_date.month, reference_date.day,
        6, 0, tzinfo=TIMEZONE
    )
    if now < generation_start:
        print(
            f"Daily card window has not started yet ({generation_start.isoformat()}); "
            "skipping without writing an empty card."
        )
        return

    print("=" * 70)
    print("AFRICAN SECURITY OBSERVER - DAILY CARD")
    print("=" * 70)

    print(
        f"Date: {reference_date.isoformat()}"
    )

    print(
        f"Card: {slot}"
    )

    print(
        f"Period: {PERIODS[slot]['label']}"
    )

    print(
        f"Start: {start.isoformat()}"
    )

    print(
        f"End:   {end.isoformat()}"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # Load news
    # --------------------------------------------------------

    news_items = get_news_for_period(
        slot,
        reference_date
    )

    print(
        f"News candidates: {len(news_items)}"
    )

    existing_card = next((
        item for item in reversed(load_previous_cards())
        if item.get("date") == reference_date.isoformat()
        and item.get("slot") == slot
    ), None)

    if not news_items:
        print("No news found for this period.")
        if existing_card:
            print("Keeping the existing daily card unchanged; no news candidates in this run.")
            return
        print("No card written: today's feed has no news candidates yet.")
        return

    source_fingerprint = fingerprint_news_items(news_items)
    force_refresh = os.getenv("FORCE_CARD_REFRESH", "false").strip().lower() in ("1", "true", "yes")
    if (existing_card
            and existing_card.get("source_fingerprint") == source_fingerprint
            and not force_refresh):
        print("The source-news packet is unchanged since the last successful update; skipping Gemini to preserve quota.")
        return
    if force_refresh:
        print("Manual force-refresh requested; processing the current source packet.")

    # --------------------------------------------------------
    # Previous events
    # --------------------------------------------------------

    previous_cards = load_previous_cards()

    previous_events = extract_previous_events(
        previous_cards,
        reference_date.isoformat()
    )

    print(
        f"Previous events supplied to Gemini: "
        f"{len(previous_events)}"
    )

    # --------------------------------------------------------
    # Build prompt
    # --------------------------------------------------------

    current_events = load_current_day_events(reference_date)
    print(f"Existing events supplied for cumulative update: {len(current_events)}")

    prompt = build_prompt(
        slot=slot,
        reference_date=reference_date,
        start=start,
        end=end,
        news_items=news_items,
        previous_events=previous_events,
        current_events=current_events
    )

    # --------------------------------------------------------
    # Gemini
    # --------------------------------------------------------

    print(
        f"Generating card with Gemini model: {MODEL_NAME}"
    )

    generated = generate_with_gemini(
        prompt
    )

    # --------------------------------------------------------
    # Normalize
    # --------------------------------------------------------

    sections = normalize_sections(
        generated
    )

    # --------------------------------------------------------
    # Final card
    # --------------------------------------------------------

    card = {
        "date": reference_date.isoformat(),

        "slot": slot,

        "period": PERIODS[slot]["label"],

        "title": f"الحوصلة الإخبارية ليوم {reference_date.strftime('%d/%m/%Y')}",

        "generated_at": now.isoformat(),

        "source_fingerprint": source_fingerprint,

        "source_news_count": len(news_items),

        "sections": sections
    }

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    card = update_cards_file(card)

    save_archive(card)

    # save_archive may recover events that existed only in the archive;
    # write the final merged version back to the current-card dataset too.
    update_cards_file(card)

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    total_events = sum(
        len(section["events"])
        for section in sections
    )

    print("=" * 70)
    print(
        f"Generated events: {total_events}"
    )

    for section in sections:
        print(
            f"- {section['title']}: "
            f"{len(section['events'])}"
        )

    print("=" * 70)
    print(
        "Daily card generated successfully."
    )


if __name__ == "__main__":
    main()
