import unittest

from tests._orchestration_helpers import load_utils_module


class DecisionProtocolTest(unittest.TestCase):
    def setUp(self):
        self.mod = load_utils_module("decision_protocol")

    def test_parse_reply_with_code_fence(self):
        text = '```json\n{"action":"reply","reason":"ok","messages":["hi"],"confidence":0.9}\n```'
        result = self.mod.parse_decision(text, 3, 0.55)
        self.assertEqual(result.action, "reply")
        self.assertEqual(result.messages, ["hi"])

    def test_low_confidence_becomes_ignore(self):
        text = '{"action":"reply","reason":"ok","messages":["hi"],"confidence":0.1}'
        self.assertEqual(self.mod.parse_decision(text, 3, 0.55).action, "ignore")

    def test_empty_reply_becomes_ignore(self):
        text = '{"action":"reply","reason":"ok","messages":[],"confidence":0.9}'
        self.assertEqual(self.mod.parse_decision(text, 3, 0.55).action, "ignore")

    def test_invalid_action_becomes_ignore(self):
        text = '{"action":"send","reason":"bad","messages":["hi"],"confidence":0.9}'
        self.assertEqual(self.mod.parse_decision(text, 3, 0.55).action, "ignore")

    def test_oversized_messages_are_truncated(self):
        text = '{"action":"reply","reason":"ok","messages":["a","b","c"],"confidence":0.9}'
        self.assertEqual(self.mod.parse_decision(text, 2, 0.55).messages, ["a", "b"])

    def test_instruction_requires_json(self):
        self.assertIn("JSON", self.mod.build_decision_instruction())
        self.assertIn("ignore", self.mod.build_decision_instruction())

    def test_confidence_must_be_finite_and_within_range(self):
        for confidence in ["NaN", "Infinity", "-0.1", "1.1"]:
            with self.subTest(confidence=confidence):
                text = '{"action":"reply","messages":["hi"],"confidence":' + confidence + '}'
                self.assertEqual(self.mod.parse_decision(text, 3, 0.55).action, "ignore")
