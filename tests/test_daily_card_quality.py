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


if __name__ == "__main__":
    unittest.main()
