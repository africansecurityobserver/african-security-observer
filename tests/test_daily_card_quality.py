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

    def test_filters_routine_source_material_before_gemini(self):
        text = "خرج نشطاء بيئيون في احتجاج أمام مؤتمر الأمم المتحدة للمناخ."
        self.assertFalse(card.is_editorially_eligible(text))
        self.assertTrue(card.is_editorially_eligible(
            "نفذ الجيش عملية عسكرية لملاحقة جماعة مسلحة عبر الحدود."
        ))


if __name__ == "__main__":
    unittest.main()
