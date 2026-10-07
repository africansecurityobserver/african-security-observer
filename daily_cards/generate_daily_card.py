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

    16:00 -> 16
    21:00 -> 21
    05:00 -> 05
    """

    hour = now.hour
    minute = now.minute

    # Exact scheduled hours, with a small tolerance window.
    if hour == 16:
        return "16"

    if hour == 21:
        return "21"

    if hour == 5:
        return "05"

    # For manual execution, allow choosing slot through argument.
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

        # Half-open interval:
        # start <= event < end
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

    # Keep only the latest/relevant number of candidates
    if len(selected) > MAX_NEWS_ITEMS:
        selected = selected[-MAX_NEWS_ITEMS:]

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
    Collect events from recent cards so Gemini can avoid
    repeating the same event.

    We keep the last 24 hours only.
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

        if not card_date:
            continue

        try:
            card_dt = datetime.strptime(
                card_date,
                "%Y-%m-%d"
            ).date()
        except Exception:
            continue

        if abs((current_dt - card_dt).days) > 1:
            continue

        sections = card.get("sections", [])

        for section in sections:
            events = section.get("events", [])

            for event in events:
                if isinstance(event, dict):
                    text_value = event.get("text", "")

                    if text_value:
                        result.append(text_value)

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
أقسام البطاقة
============================================================

القسم الأول:
"ليبيا"

ضع فيه الأحداث المتعلقة بليبيا.

القسم الثاني:
"الجزائر"

ضع فيه الأحداث المتعلقة بالجزائر.

القسم الثالث:
"المغرب"

ضع فيه الأحداث المتعلقة بالمغرب.

بالنسبة للجزائر والمغرب، يمكن عند الحاجة استعمال أحداث أقل
أهمية نسبياً لتأثيث القسم، مثل اللقاءات الرسمية أو التحركات
الدبلوماسية، لكن بشرط أن تكون موجودة فعلاً في المواد.

لا تفعل ذلك بصورة واسعة في الأقسام الأخرى.

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

إذا كان الخبر لا يندرج بوضوح في أحد الأقسام، تجاهله.

لا تجعل عدد الأحداث هدفاً بحد ذاته.

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
        "gemini-3.8-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
    ]

    last_error = None

    for model_name in models:

        print("=" * 70)
        print(f"Trying Gemini model: {model_name}")
        print("Timeout: 90 seconds")
        print("=" * 70)

        try:

            client = genai.Client(
                api_key=api_key,
                http_options=types.HttpOptions(timeout=90000)
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
