import importlib
import sys
import types
import unittest


def load_daily_card_module():
    try:
        return importlib.import_module("daily_cards.generate_daily_card")
    except ModuleNotFoundError as error:
        if error.name not in ("google", "google.genai"):
            raise
        google_module = types.ModuleType("google")
        genai_module = types.ModuleType("google.genai")
        genai_types_module = types.ModuleType("google.genai.types")
        genai_module.types = genai_types_module
        google_module.genai = genai_module
        sys.modules["google"] = google_module
        sys.modules["google.genai"] = genai_module
        sys.modules["google.genai.types"] = genai_types_module
        return importlib.import_module("daily_cards.generate_daily_card")


card = load_daily_card_module()


class DailyCardEditorialQualityTests(unittest.TestCase):
    def test_security_targeting_is_not_misclassified_as_sport(self):
        text = "هجوم مسلح يستهدف قوات الأمن في باكستان"
        self.assertFalse(card.is_sports_related(text))
        self.assertTrue(card.is_editorially_eligible(text))

    def test_actual_football_story_is_still_rejected(self):
        self.assertTrue(card.is_sports_related(
            "سجل المنتخب هدفاً في المباراة وحقق الفوز بالدوري"
        ))

    def test_rejects_routine_transport_union_and_fuel_subsidy_story(self):
        text = (
            "وجهت تنسيقية النقابات الوطنية لقطاع النقل الطرقي للبضائع "
            "مراسلة إلى رئيسة الحكومة للمطالبة بالدعم الاستثنائي "
            "في ظل ارتفاع أسعار المحروقات."
        )
        self.assertFalse(card.is_editorially_eligible(text))

    def test_rejects_routine_climate_activist_protest(self):
        text = (
            "خرج نشطاء بيئيون من حركة أيام الجمعة من أجل المستقبل "
            "في مسيرة احتجاجية أمام المباني الحكومية بسبب مؤتمر الأمم المتحدة للمناخ."
        )
        self.assertFalse(card.is_editorially_eligible(text))

    def test_keeps_military_or_security_development_despite_civilian_context_word(self):
        self.assertTrue(card.is_editorially_eligible(
            "أعلنت وزارة الدفاع تنفيذ عملية عسكرية لتأمين الحدود."
        ))
        self.assertTrue(card.is_editorially_eligible(
            "أعلنت السلطات إحباط مخطط إرهابي عابر للحدود."
        ))

    def test_keeps_concrete_strategic_diplomatic_development(self):
        self.assertTrue(card.is_editorially_eligible(
            "وقّع وزير الدفاع اتفاقاً للتعاون العسكري وتبادل المعلومات الاستخباراتية."
        ))

    def test_routes_egyptian_major_event_out_of_libya_section(self):
        sections = [
            {"id": section_id, "title": title, "events": []}
            for section_id, title in card.EXPECTED_SECTIONS
        ]
        sections[0]["events"].append({
            "country": "مصر",
            "text": "أعلن الجيش المصري مقتل رئيس أركان حرس الحدود خلال عملية عسكرية."
        })
        routed = card.route_geographic_events(sections)
        by_id = {section["id"]: section for section in routed}
        self.assertEqual(by_id["libya"]["events"], [])
        self.assertEqual(len(by_id["africa_major"]["events"]), 1)

    def test_routes_middle_east_event_to_its_section(self):
        sections = [
            {"id": section_id, "title": title, "events": []}
            for section_id, title in card.EXPECTED_SECTIONS
        ]
        sections[-1]["events"].append({
            "country": "إيران",
            "text": "أعلن الحرس الثوري الإيراني إجراء مناورات بحرية في مضيق هرمز."
        })
        routed = card.route_geographic_events(sections)
        by_id = {section["id"]: section for section in routed}
        self.assertEqual(by_id["world_major"]["events"], [])
        self.assertEqual(len(by_id["middle_east"]["events"]), 1)

    def test_strategic_review_candidate_scan_catches_diverse_defense_and_security_items(self):
        # Include verb forms and modifiers commonly used in translated headlines.
        items = [
            {"title": "تونس تتلقى معدات مكافحة الإرهاب", "description": "معدات بقيمة محددة"},
            {"title": "الجيش السوداني يوسع سيطرته في كردفان", "description": "انتزع مناطق جديدة"},
            {"title": "تهديدات جديدة بالقنابل تستهدف الرحلات الجوية", "description": "بلاغات أمنية"},
            {"title": "مناورات الناتو في ليتوانيا", "description": "مشاركة آلاف الجنود"},
            {"title": "نتيجة مباراة كرة قدم", "description": "الدوري المحلي"},
        ]
        candidates = card.identify_strategic_review_candidates(items)
        candidate_numbers = [number for number, _ in candidates]
        self.assertEqual(candidate_numbers, [1, 2, 3, 4])

    def test_prompt_contains_candidate_audit_and_has_no_event_count_ceiling(self):
        from datetime import date, datetime
        from zoneinfo import ZoneInfo

        reference_date = date(2026, 11, 6)
        start = datetime(2026, 11, 6, 0, 0, tzinfo=ZoneInfo("Africa/Tunis"))
        end = datetime(2026, 11, 6, 20, 0, tzinfo=ZoneInfo("Africa/Tunis"))
        prompt = card.build_prompt(
            "daily", reference_date, start, end,
            [{"title": "تونس تتلقى معدات مكافحة الإرهاب", "description": "معدات محددة"}],
            [], []
        )
        self.assertIn("قائمة المراجعة الثانية ذات الأولوية", prompt)
        self.assertIn("تونس تتلقى معدات مكافحة الإرهاب", prompt)
        self.assertIn("لا يوجد سقف تحريري لعدد الأحداث", prompt)

    def test_keeps_concrete_defense_aid_and_equipment_deliveries(self):
        examples = [
            "تسلمت تونس معدات مكافحة الإرهاب بقيمة تتجاوز مليون دولار.",
            "قدم الاتحاد الأوروبي معدات للأمن البحري إلى قوات الدفاع الكينية.",
            "أعلنت دولة تقديم مساعدات عسكرية ومعدات مراقبة حدود إلى الجيش."
        ]
        for text in examples:
            with self.subTest(text=text):
                self.assertTrue(card.is_editorially_eligible(text))

    def test_keeps_major_field_developments_and_military_leadership_changes(self):
        examples = [
            "وسع الجيش السوداني سيطرته في كردفان وانتزع أربع مناطق ودمر آليات عسكرية.",
            "أعلن قائد الجيش الأوغندي خططه الرسمية للتنحي عن منصبه.",
            "أعلنت الحكومة الاتفاق على نشر قوات أجنبية محددة العدد والمهمة."
        ]
        for text in examples:
            with self.subTest(text=text):
                self.assertTrue(card.is_editorially_eligible(text))

    def test_keeps_air_security_threats_espionage_and_arms_smuggling(self):
        examples = [
            "أعلنت السلطات تسجيل تهديدات جديدة بالقنابل استهدفت رحلات جوية تجارية.",
            "اعتقلت أجهزة الأمن عميلاً روسياً للاشتباه في تورطه بتفجير سيارة مفخخة.",
            "اعتقلت السلطات مجموعة مرتبطة بتهريب صواريخ."
        ]
        for text in examples:
            with self.subTest(text=text):
                self.assertTrue(card.is_editorially_eligible(text))

    def test_keeps_large_military_exercises_and_defense_industry_deals(self):
        examples = [
            "شارك ستة آلاف جندي من الناتو في مناورات عسكرية واسعة في ليتوانيا.",
            "وقعت شركة بناء سفن صفقة لتحديث سفن حربية تابعة لقوات بحرية إفريقية.",
            "أطلق الناتو منصة لتسريع شراء الأنظمة المضادة للطائرات المسيّرة."
        ]
        for text in examples:
            with self.subTest(text=text):
                self.assertTrue(card.is_editorially_eligible(text))

    def test_does_not_turn_generic_cooperation_statements_into_events(self):
        self.assertFalse(card.is_editorially_eligible(
            "أكد وزير الدفاع أهمية التعاون الثلاثي في تعزيز الأمن والتنمية، دون الإعلان عن إجراء محدد."
        ))

    def test_does_not_admit_ordinary_local_crime_without_security_context(self):
        self.assertFalse(card.is_editorially_eligible(
            "اعتقلت الشرطة شاباً للاشتباه في سرقة متجر محلي."
        ))

    def test_news_fingerprint_is_independent_of_feed_order(self):
        first = [
            {"url": "https://example.com/a", "title": "خبر أ"},
            {"url": "https://example.com/b", "title": "خبر ب"},
        ]
        second = list(reversed(first))
        self.assertEqual(
            card.fingerprint_news_items(first),
            card.fingerprint_news_items(second)
        )

    def test_news_fingerprint_changes_when_source_material_changes(self):
        original = [{"url": "https://example.com/a", "title": "خبر أ"}]
        updated = [{"url": "https://example.com/a", "title": "خبر أ جديد"}]
        self.assertNotEqual(
            card.fingerprint_news_items(original),
            card.fingerprint_news_items(updated)
        )

    def test_filters_routine_source_material_before_gemini(self):
        text = "خرج نشطاء بيئيون في احتجاج أمام مؤتمر الأمم المتحدة للمناخ."
        self.assertFalse(card.is_editorially_eligible(text))
        self.assertFalse(card.is_editorially_eligible(
            "أكد وزير الدفاع أهمية تعزيز التعاون التونسي الجزائري والليبي في مجالات الأمن والتنمية."
        ))
        self.assertTrue(card.is_editorially_eligible(
            "بحث وزير الدفاع مع نظيره اتفاقاً للتعاون العسكري وتبادل المعلومات."
        ))
        self.assertTrue(card.is_editorially_eligible(
            "نفذ الجيش عملية عسكرية لملاحقة جماعة مسلحة عبر الحدود."
        ))


if __name__ == "__main__":
    unittest.main()
