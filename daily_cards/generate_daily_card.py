import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from google import genai
from google.genai import types


# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = Path(__file__).resolve().parent.parent
NEWS_FILE = BASE_DIR / "news.json"
CARDS_FILE = BASE_DIR / "daily_cards" / "cards.json"
ARCHIVE_DIR = BASE_DIR / "daily_cards" / "archive"

TIMEZONE = ZoneInfo("Africa/Tunis")

MODEL_NAME = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")

# Number of news items sent to Gemini at most
MAX_NEWS_ITEMS = 100


# ============================================================
# CARD PERIODS
# ============================================================

PERIODS = {
    "16": {
        "start_hour": 6,
        "start_minute": 0,
        "end_hour": 16,
        "end_minute": 0,
        "label": "06:00–16:00",
    },
    "21": {
        "start_hour": 16,
        "start_minute": 0,
        "end_hour": 21,
        "end_minute": 0,
        "label": "16:00–21:00",
    },
    "05": {
        "start_hour": 21,
        "start_minute": 0,
        "end_hour": 5,
        "end_minute": 0,
        "label": "21:00–05:00",
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
    """
    Determine the card slot automatically.

    The slot key identifies the period ending at the named hour:
    - "16": 06:00–16:00
    - "21": 16:00–21:00
    - "05": 21:00–05:00

    During 16:00–21:00, always refresh the "21" card so it can
    appear shortly after 16:00 and be updated throughout the period.
    """

    hour = now.hour

    if hour == 16:
        return "21"

    if 16 < hour < 21:
        return "21"

    if hour == 21:
        return "21"

    if hour == 5:
        return "05"

    return None


def get_period_dates(slot, reference_date):
    """
    Return start/end datetimes in Africa/Tunis.

    16:
        same day 06:00 -> same day 16:00

    21:
        same day 16:00 -> same day 21:00

    05:
        previous day 21:00 -> current day 05:00
    """

    if slot == "16":
        start = datetime(
            reference_date.year,
            reference_date.month,
            reference_date.day,
            6,
            0,
            tzinfo=TIMEZONE
        )

        end = datetime(
            reference_date.year,
            reference_date.month,
            reference_date.day,
            16,
            0,
            tzinfo=TIMEZONE
        )

    elif slot == "21":
        start = datetime(
            reference_date.year,
            reference_date.month,
            reference_date.day,
            16,
            0,
            tzinfo=TIMEZONE
        )

        end = datetime(
            reference_date.year,
            reference_date.month,
            reference_date.day,
            21,
            0,
            tzinfo=TIMEZONE
        )

    elif slot == "05":
        previous_day = reference_date - timedelta(days=1)

        start = datetime(
            previous_day.year,
            previous_day.month,
            previous_day.day,
            21,
            0,
            tzinfo=TIMEZONE
        )

        end = datetime(
            reference_date.year,
            reference_date.month,
            reference_date.day,
            5,
            0,
            tzinfo=TIMEZONE
        )

    else:
        raise ValueError(f"Unknown slot: {slot}")

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

    selected = basic_deduplicate(selected)

    # Preserve dedicated international-organization stories when the feed
    # volume is high, then fill the remaining slots with the latest news.
    if len(selected) > MAX_NEWS_ITEMS:
        priority_org_items = [
            item for item in selected
            if item.get("category") == "international"
        ]
        other_items = [
            item for item in selected
            if item.get("category") != "international"
        ]
        priority_org_items = priority_org_items[-20:]
        remaining = max(0, MAX_NEWS_ITEMS - len(priority_org_items))
        selected = priority_org_items + other_items[-remaining:]
        selected.sort(
            key=lambda x: parse_datetime(x.get("published")) or start
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

            if card_slot == current_slot:
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
# GEMINI PROMPT
# ============================================================

def build_prompt(
    slot,
    reference_date,
    start,
    end,
    news_items,
    previous_events
):

    date_text = reference_date.strftime("%d/%m/%Y")

    period_label = PERIODS[slot]["label"]

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

14. العدد المستهدف عموماً هو 15 إلى 20 حدثاً في البطاقة،
    لكن هذا ليس حداً إلزامياً.
    إذا كانت الفترة هادئة، يمكن أن تكون البطاقة أقل.

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

25. أخبار الاجتماعات والمباحثات والمشاورات:
عندما يكون جوهر الخبر اجتماعاً أو مباحثات أو مشاورات بين عسكريين
أو دبلوماسيين أو رؤساء أو وزراء أو مسؤولين أو أصحاب قرار، ابدأ
بفعل يوضح ما جرى، واذكر صفة الشخص الأول واسمه، ثم المكان إذا كان
مذكوراً، ثم الطرف الآخر بصفته واسمه، ثم موضوع البحث أو هدف اللقاء.

القالب المفضل:
"بحث [صفة الشخص الأول واسمه] في [المكان] مع [صفة الشخص الثاني
واسمه] [موضوع المباحثات أو السبل الكفيلة أو إمكانات التعاون]."

ويجوز استخدام "ناقش" أو "استعرض" أو "تشاور" أو "أجرى مباحثات"
إذا كان ذلك أدق وفق المادة الأصلية.

مثال توضيحي للشكل فقط:
"بحث رئيس الأركان العامة بحكومة الوحدة الوطنية صلاح الدين النمروش
في طرابلس مع الرئيس التونسي قيس سعيّد سبل تعزيز التعاون الأمني
ومستجدات التنسيق بين البلدين."

هذا المثال يوضح القالب ولا يُعد معلومة يجوز نقلها إلى خبر ما لم
ترد تفاصيله في المادة الإخبارية نفسها.

- لا تذكر مكان الاجتماع إلا إذا ورد في المادة.
- لا تفترض أن الاجتماع تناول التعاون الأمني أو أي ملف بعينه؛ اذكر
  الموضوع الذي توضحه المادة فقط.
- إذا لم تذكر المادة صفة أحد المشاركين، فلا تخترعها أو تستنتجها
  من الاسم. استخدم المعلومات المتاحة فقط.
- لا تصف لقاءً بأنه "بحث" موضوعاً معيناً ما لم تذكر المادة هذا
  الموضوع أو تدعمه بوضوح.

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

27. أخبار الهجمات الإرهابية أو هجمات الجماعات المسلحة:
ابدأ بعدد القتلى أو المصابين إذا كان العدد مؤكداً ومذكوراً في المادة،
ثم اذكر صفتهم وجنسيتهم إذا كانت معلومة، والجهة المنفذة والمنطقة
والدولة، بصياغة مباشرة وموجزة.

القوالب المفضلة:
"قُتل [العدد] عسكريين [الجنسية] في هجوم نفذته عناصر تابعة
لـ[اسم الجماعة] في [المنطقة] بـ[الدولة]."

"أُصيب [العدد] من أفراد الأمن [الجنسية] في هجوم نفذته عناصر تابعة
لـ[اسم الجماعة] في [المنطقة] بـ[الدولة]."

- اضبط صيغة العدد والمعدود نحوياً، ولا تلتزم حرفياً بالقالب إذا
  كان العدد يتطلب صياغة عربية مختلفة.
- إذا كان الضحايا مدنيين، فلا تصفهم بالعسكريين أو الأمنيين.
- إذا لم تذكر المادة الجنسية، أو عدد الضحايا، أو الجهة المنفذة،
  أو المنطقة، فلا تخمّنها؛ احذف التفصيل غير المتاح وصغ الجملة
  بأكبر قدر من الدقة الممكنة.
- لا تنسب الهجوم إلى جماعة محددة إلا إذا ورد ذلك في المادة أو
  نُسب إليها بوضوح مع الحفاظ على درجة اليقين؛ فإذا كانت المسؤولية
  غير مؤكدة، استخدم صياغة مثل "نُسب الهجوم إلى..." أو "أعلنت
  [الجهة] مسؤوليتها عن..." وفق ما تقوله المادة.
- لا تصف كل هجوم بأنه إرهابي. استخدم هذا القالب في أخبار الإرهاب
  أو الهجمات المنسوبة بوضوح إلى جماعات مسلحة؛ أما الحوادث الأخرى
  فصغها بحسب طبيعتها الفعلية.

28. ترتيب الأولويات التحريرية:
- حدّد نوع الخبر أولاً: مباحثات/اجتماع، توقيع اتفاق، هجوم أو
  حصيلة ضحايا، أو تطور آخر.
- طبّق القالب الأقرب إلى نوع الخبر، مع الحفاظ على المعلومات
  الأساسية واللغة العربية السليمة.
- لا تجمع في جملة واحدة وقائع مستقلة لمجرد اختصار النص.
- هذه القوالب توجّه الصياغة ولا تبرر أبداً استكمال المعلومات
  الناقصة بالتخمين.

============================================================
أقسام البطاقة
============================================================

القسم الأول:
"ليبيا"

ضع فيه الأحداث المتعلقة بليبيا، مع إعطاء أولوية واضحة لنوعين من الأخبار الإضافية التالية:

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

ضع فيه الأحداث المتعلقة بالجزائر.

القسم الثالث:
"المغرب"

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
البطاقات السابقة
============================================================

هذه أحداث ظهرت في بطاقات سابقة قريبة زمنياً:

{previous_text}

لا تعيد هذه الأحداث في البطاقة الحالية إذا لم يحدث تطور جديد.

إذا حدث تطور كبير جديد بشأن حدث سابق، يمكن إدراجه، لكن ركز على
التطور الجديد ولا تعيد صياغة الخبر القديم بالكامل.

============================================================
المواد الإخبارية للفترة الحالية
============================================================

{news_text}

============================================================
مهم جداً
============================================================

المواد أعلاه هي المصدر الوحيد المسموح باستخدامه.

إذا وجدت خبراً مكرراً من عدة مصادر، ادمجه في حدث واحد.

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
                        "title": {
                            "type": "string"
                        },
                        "events": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "country": {
                                        "type": "string"
                                    },
                                    "text": {
                                        "type": "string"
                                    }
                                },
                                "required": [
                                    "country",
                                    "text"
                                ]
                            }
                        }
                    },
                    "required": [
                        "title",
                        "events"
                    ]
                }
            }
        },
        "required": [
            "sections"
        ]
    }

    # Try several current Gemini models.
    # If one is temporarily overloaded, automatically try the next one.
    models = [
    "gemini-3.5-flash-lite"
    ]

    last_error = None

    for model_name in models:

        print("=" * 70)
        print(f"Trying Gemini model: {model_name}")
        print("Timeout: 180 seconds")
        print("=" * 70)

        try:

            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=180000)
            )

            interaction = client.interactions.create(
                model=model_name,
                input=prompt,
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": response_schema
                },
                generation_config={
                    "temperature": 0.2,
                    "thinking_level": "low"
                }
            )

            output_text = interaction.output_text

            if not output_text:
                raise RuntimeError(
                    f"Gemini returned an empty response from {model_name}."
                )

            result = json.loads(output_text)

            print("=" * 70)
            print(f"Gemini generation succeeded with: {model_name}")
            print("=" * 70)

            return result

        except Exception as e:

            last_error = e

            print("=" * 70)
            print(f"Gemini model failed: {model_name}")
            print(f"Error: {type(e).__name__}: {e}")
            print("=" * 70)

            # Continue automatically with the next model.
            continue

    raise RuntimeError(
        "All Gemini models failed. Last error: "
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

        final_sections.append({
            "id": section_id,
            "title": title,
            "events": clean_events
        })

    return final_sections

# ============================================================
# SAVE CARD
# ============================================================

def update_cards_file(card):

    existing = load_json(
        CARDS_FILE,
        {
            "cards": []
        }
    )

    if not isinstance(existing, dict):
        existing = {
            "cards": []
        }

    cards = existing.get(
        "cards",
        []
    )

    if not isinstance(cards, list):
        cards = []

    # Remove an existing card for exactly the same
    # date + slot, if the workflow is manually rerun.
    cards = [
        c for c in cards
        if not (
            c.get("date") == card.get("date")
            and c.get("slot") == card.get("slot")
        )
    ]

    cards.append(card)

    # Sort chronologically
    cards.sort(
        key=lambda c: (
            c.get("date", ""),
            c.get("slot", "")
        )
    )

    # Keep a reasonable archive size in cards.json
    # while individual archives remain available.
    cards = cards[-90:]

    output = {
        "updated": datetime.now(
            timezone.utc
        ).isoformat(),

        "cards": cards
    }

    save_json(
        CARDS_FILE,
        output
    )


def save_archive(card):

    archive_date = card["date"]

    archive_path = (
        ARCHIVE_DIR
        / archive_date
        / f"{card['slot']}.json"
    )

    save_json(
        archive_path,
        card
    )

    # Rebuild the year/month archive index automatically after every card.
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
                "Could not determine card slot. "
                "Use 16, 21, or 05."
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

    print("=" * 70)
    print("AFRICAN SECURITY OBSERVER - DAILY CARD")
    print("=" * 70)

    print(
        f"Date: {reference_date.isoformat()}"
    )

    print(
        f"Slot: {slot}"
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

    if not news_items:

        print(
            "No news found for this period."
        )

        # Still create an empty card rather than inventing news.
        empty_sections = []

        for section_id, title in EXPECTED_SECTIONS:
            empty_sections.append({
                "id": section_id,
                "title": title,
                "events": []
            })

        card = {
            "date": reference_date.isoformat(),
            "slot": slot,
            "period": PERIODS[slot]["label"],
            "title": (
                f"بطاقة الأحداث اليومية "
                f"ليوم {reference_date.strftime('%d/%m/%Y')} "
                f"— {PERIODS[slot]['label']}"
            ),
            "generated_at": now.isoformat(),
            "sections": empty_sections
        }

        update_cards_file(card)
        save_archive(card)

        print(
            "Empty card saved because there were no candidates."
        )

        return

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

    prompt = build_prompt(
        slot=slot,
        reference_date=reference_date,
        start=start,
        end=end,
        news_items=news_items,
        previous_events=previous_events
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

        "title": (
            f"بطاقة الأحداث اليومية "
            f"ليوم {reference_date.strftime('%d/%m/%Y')} "
            f"— {PERIODS[slot]['label']}"
        ),

        "generated_at": now.isoformat(),

        "sections": sections
    }

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    update_cards_file(
        card
    )

    save_archive(
        card
    )

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
