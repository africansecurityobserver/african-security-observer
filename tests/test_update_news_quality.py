import unittest

from update_news import (
    classify_news,
    classify_region,
    deduplicate_articles,
    is_relevant_security_news,
    is_sports_related,
    normalize_article,
    normalize_url,
    parse_published_datetime,
)


class NewsQualityTests(unittest.TestCase):
    def test_rejects_arabic_sports_festival(self):
        title = "جمعية المحاربين تنظم مهرجانا رياضيا بمناسبة الاحتفال بانتصارات أكتوبر"
        description = "نظمت جمعية المحاربين القدماء مهرجاناً رياضياً لأعضائها."
        self.assertTrue(is_sports_related(title, description))
        self.assertFalse(is_relevant_security_news(title, description))

    def test_rejects_arabic_music_and_entertainment(self):
        self.assertFalse(is_relevant_security_news(
            "أغنية جديدة باللهجة اللبنانية تحتل المركز الثالث عالمياً على يوتيوب",
            "خبر فني عن مغنٍ جديد."
        ))

    def test_rejects_unrelated_technology_story_with_incidental_security_word(self):
        self.assertFalse(is_relevant_security_news(
            "شركات التكنولوجيا الكبرى ومنظمات بيئية تتعاون لوضع قواعد لمراكز البيانات",
            "تتعلق القواعد بالاستدامة وأمن البيانات في مراكز الحوسبة."
        ))

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

    def test_keeps_designated_officials_diplomatic_activities(self):
        self.assertTrue(is_relevant_security_news(
            "عبد المجيد تبون يستقبل وزير الخارجية في اجتماع رسمي",
            "بحث الطرفان العلاقات الثنائية."
        ))

    def test_arms_story_remains_in_general_news_without_dedicated_category(self):
        title = "Kenya signs an arms deal for new fighter jets"
        self.assertTrue(is_relevant_security_news(title, ""))
        self.assertEqual(classify_news(title, "", "rest"), "rest")

    def test_mauritania_is_classified_as_sahel(self):
        self.assertEqual(classify_news(
            "Mauritania announces a border security operation", "", "rest"
        ), "sahel")
        self.assertEqual(classify_region("قوات موريتانية تنفذ عملية أمنية", ""), "africa")

    def test_nigeria_is_not_misclassified_as_sahel(self):
        self.assertEqual(classify_news(
            "Nigeria announces a military operation", "", "rest"
        ), "rest")

    def test_world_story_is_not_made_african_by_generic_description_reference(self):
        self.assertEqual(classify_region(
            "European security ministers meet in Brussels",
            "The talks also considered cooperation with African partners."
        ), "world")

    def test_region_classification_uses_country_in_title(self):
        self.assertEqual(classify_region(
            "Kenya receives new maritime security equipment", ""
        ), "africa")

    def test_normalize_article_exposes_summary_and_recomputes_region(self):
        item = normalize_article({
            "title": "كينيا تتسلم معدات أمن بحري",
            "description": "تسلمت القوات معدات جديدة.",
            "original_title": "Kenya receives maritime security equipment",
            "original_description": "Kenya received new maritime security equipment.",
            "url": "https://Example.com/story/?utm_source=rss",
            "published": "2026-11-06T10:00:00Z",
            "category": "international"
        })
        self.assertEqual(item["summary"], item["description"])
        self.assertEqual(item["region"], "africa")
        self.assertEqual(item["url"], "https://example.com/story")

    def test_deduplicates_same_url_and_same_headline(self):
        base = {
            "title": "عملية عسكرية في مالي",
            "original_title": "Military operation in Mali",
            "description": "تقرير أمني.",
            "original_description": "Security report.",
            "url": "https://example.com/story?utm_source=one",
            "published": "2026-11-06T10:00:00Z"
        }
        second = dict(base, url="https://EXAMPLE.com/story#top")
        third = dict(base, url="https://other.example/story")
        self.assertEqual(len(deduplicate_articles([base, second, third])), 1)

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
