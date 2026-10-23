import json
import pathlib
import unittest


class ConfigSchemaTest(unittest.TestCase):
    def test_orchestration_defaults(self):
        schema = json.loads((pathlib.Path(__file__).parents[1] / "_conf_schema.json").read_text("utf-8"))
        items = schema["reply_orchestration"]["items"]
        self.assertFalse(items["enabled"]["default"])
        self.assertEqual(items["decision_mode"]["default"], "legacy")
        self.assertEqual(items["timeout_ms"]["default"], 2000)
        self.assertEqual(items["outbound_queue"]["items"]["ttl_ms"]["default"], 20000)
