import unittest

from update_news import (
    classify_news,
    classify_region,
    is_relevant_security_news,
    is_sports_related,
    normalize_url,
    parse_published_datetime,
)


class NewsQualityTests(unittest.TestCase):
    def test_rejects_arabic_sports_festival(self):
        title = "جمعية المحاربين تنظم مهرجانا رياضيا بمناسبة الاحتفال بانتصارات أكتوبر"
        description = "نظمت جمعية المحاربين القدماء مهرجاناً رياضياً لأعضائها."
        self.assertTrue(is_sports_related(title, description))
        self.assertFalse(is_relevant_security_news(title, description))

    def test_rejects_routine_consumer_digital_fraud(self):
        title = "ميتا: الاحتيال الرقمي يعبر المنصات ويتطلب تعاوناً أوسع لمواجهته"
        description = "تواجه المنصات عمليات احتيال تجارية وتعمل على توعية المستخدمين."
        self.assertFalse(is_relevant_security_news(title, description))

    def test_keeps_armed_clashes_in_libya(self):
        title = "اشتباكات مسلحة بين مجموعات في ليبيا"
        self.assertTrue(is_relevant_security_news(title, ""))

    def test_keeps_relevant_sahel_security_news(self):
        title = "Mali army clashes with armed militants near the border"
        self.assertTrue(is_relevant_security_news(title, "Security forces reported fighting."))

    def test_arms_story_stays_in_defense_category(self):
        title = "Nigeria signs an arms deal for new fighter jets"
        self.assertEqual(classify_news(title, "", "defense"), "defense")

    def test_mauritania_is_classified_as_sahel(self):
        self.assertEqual(classify_news("Mauritania announces a border security operation", "", "rest"), "sahel")
        self.assertEqual(classify_region("قوات موريتانية تنفذ عملية أمنية", ""), "africa")

    def test_tracking_parameters_and_fragments_do_not_duplicate_links(self):
        self.assertEqual(
            normalize_url("https://Example.com/story/?utm_source=feed&id=12&fbclid=abc#section"),
            "https://example.com/story?id=12",
        )

    def test_iso_dates_are_timezone_aware(self):
        parsed = parse_published_datetime("2026-10-20T08:30:00Z")
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.utcoffset().total_seconds(), 0)


if __name__ == "__main__":
    unittest.main()
