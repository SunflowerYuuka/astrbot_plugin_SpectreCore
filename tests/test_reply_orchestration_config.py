import json
import pathlib
import unittest


SCHEMA_PATH = pathlib.Path(__file__).parents[1] / "_conf_schema.json"

ASTROBOT_DEFAULT_VALUE_MAP = {
    "int": 0,
    "float": 0.0,
    "bool": False,
    "string": "",
    "text": "",
    "list": [],
    "file": [],
    "object": {},
    "template_list": [],
    "dict": {},
}


def astrbot_schema_to_default_config(schema):
    """Mirror AstrBot v4.29.1 AstrBotConfig._config_schema_to_default_config."""
    conf = {}

    def parse_schema(current_schema, target):
        for key, value in current_schema.items():
            if value["type"] not in ASTROBOT_DEFAULT_VALUE_MAP:
                raise TypeError(f"unsupported type {value['type']}")
            default = value.get("default", ASTROBOT_DEFAULT_VALUE_MAP[value["type"]])
            if value["type"] == "object":
                target[key] = {}
                parse_schema(value["items"], target[key])
            else:
                target[key] = default

    parse_schema(schema, conf)
    return conf


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
        self.assertEqual(wake["groups"]["type"], "dict")
        self.assertEqual(wake["groups"]["default"], {})

    def test_schema_is_loadable_by_astrbot_config_parser(self):
        schema = json.loads(SCHEMA_PATH.read_text("utf-8"))
        defaults = astrbot_schema_to_default_config(schema)
        wake = defaults["reply_orchestration"]["wake"]
        self.assertEqual(wake["groups"], {})
        self.assertEqual(wake["private"]["max_messages"], 5)
        self.assertEqual(wake["admin"]["duration_seconds"], 0)
