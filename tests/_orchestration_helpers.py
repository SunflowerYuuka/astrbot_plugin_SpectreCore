import importlib.util
import logging
import pathlib
import sys
import types


ROOT = pathlib.Path(__file__).resolve().parents[1]
UTILS = ROOT / "utils"
PACKAGE = "spectrecore_orch_under_test"


class _StubMessageMember:
    def __init__(self, user_id="", nickname=None):
        self.user_id = user_id
        self.nickname = nickname


class _StubMessage:
    def __init__(self):
        self.group_id = ""
        self.sender = None
        self.message = []
        self.message_str = ""
        self.type = None
        self.self_id = "bot"
        self.session_id = ""
        self.message_id = ""
        self.timestamp = 0


class _StubMessageType:
    FRIEND_MESSAGE = "friend"
    GROUP_MESSAGE = "group"


class _StubMessageChain:
    def __init__(self, chain=None, **kwargs):
        self.chain = list(chain or [])


class _StubPlain:
    def __init__(self, text=""):
        self.text = text


class _StubProviderRequest:
    def __init__(self, **kwargs):
        self.prompt = kwargs.get("prompt")
        self.session_id = kwargs.get("session_id", "")
        self.image_urls = kwargs.get("image_urls", [])
        self.contexts = kwargs.get("contexts", [])
        self.system_prompt = kwargs.get("system_prompt", "")
        self.func_tool = kwargs.get("func_tool")


def install_astrbot_stub():
    astrbot = sys.modules.get("astrbot")
    if astrbot is None:
        astrbot = types.ModuleType("astrbot")
        astrbot.__path__ = []
        sys.modules["astrbot"] = astrbot
    if "astrbot.api" not in sys.modules:
        api = types.ModuleType("astrbot.api")
        api.__path__ = []
        sys.modules["astrbot.api"] = api
    all_module = sys.modules.get("astrbot.api.all")
    if all_module is None:
        all_module = types.ModuleType("astrbot.api.all")
        sys.modules["astrbot.api.all"] = all_module

    all_module.logger = logging.getLogger("test.astrbot")
    all_module.AstrBotConfig = dict
    all_module.Context = object
    all_module.AstrMessageEvent = object
    all_module.AstrBotMessage = _StubMessage
    all_module.MessageMember = _StubMessageMember
    all_module.MessageType = _StubMessageType
    all_module.MessageChain = _StubMessageChain
    all_module.Plain = _StubPlain
    for name in [
        "At", "BaseMessageComponent", "Forward", "Image", "MessageEventResult",
        "Node", "Nodes", "Reply",
    ]:
        if not hasattr(all_module, name):
            setattr(all_module, name, type(name, (), {}))

    required = [
        "AstrBotConfig", "AstrBotMessage", "AstrMessageEvent", "At",
        "BaseMessageComponent", "Context", "Forward", "Image", "MessageChain",
        "MessageEventResult", "MessageMember", "MessageType", "Node", "Nodes",
        "Plain", "Reply", "logger",
    ]
    exported = list(getattr(all_module, "__all__", []) or [])
    for name in required:
        if name not in exported:
            exported.append(name)
    all_module.__all__ = exported

    if "astrbot.core" not in sys.modules:
        core = types.ModuleType("astrbot.core")
        core.__path__ = []
        sys.modules["astrbot.core"] = core
    if "astrbot.core.provider" not in sys.modules:
        provider_pkg = types.ModuleType("astrbot.core.provider")
        provider_pkg.__path__ = []
        sys.modules["astrbot.core.provider"] = provider_pkg
    if "astrbot.core.provider.entites" not in sys.modules:
        entites = types.ModuleType("astrbot.core.provider.entites")
        entites.ProviderRequest = _StubProviderRequest
        sys.modules["astrbot.core.provider.entites"] = entites


def load_utils_module(name):
    install_astrbot_stub()
    if PACKAGE not in sys.modules:
        package = types.ModuleType(PACKAGE)
        package.__path__ = [str(UTILS)]
        sys.modules[PACKAGE] = package
    module_name = f"{PACKAGE}.{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, UTILS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    return module


class SimpleMessage:
    def __init__(self, text="hello", group_id="123"):
        self.group_id = group_id
        self.sender = _StubMessageMember(user_id="u1")
        self.message = []
        self.message_str = text
        self.type = None
        self.self_id = "bot"
        self.session_id = "session"
        self.message_id = text
        self.timestamp = 0
