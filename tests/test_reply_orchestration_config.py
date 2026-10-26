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

    def test_wake_schema_defaults_and_scopes(self):
        schema = json.loads((pathlib.Path(__file__).parents[1] / "_conf_schema.json").read_text("utf-8"))
        items = schema["reply_orchestration"]["items"]
        wake = items["wake"]["items"]
        self.assertFalse(wake["enabled"]["default"])
        self.assertEqual(wake["duration_seconds"]["default"], 300)
        self.assertEqual(wake["max_messages"]["default"], 5)
        self.assertEqual(wake["idle_timeout_seconds"]["default"], 120)
        for field, default in (("duration_seconds", 300), ("max_messages", 5), ("idle_timeout_seconds", 120)):
            self.assertEqual(wake["private"]["items"][field]["default"], default)
            self.assertEqual(wake["admin"]["items"][field]["default"], 0)
            self.assertNotIn("default", wake["groups"]["items"]["items"][field])
        self.assertEqual(wake["groups"]["type"], "object")
        self.assertEqual(wake["groups"]["items"]["type"], "object")
