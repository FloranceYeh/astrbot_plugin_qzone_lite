import sys
import types
import unittest
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


def _install_test_stubs() -> None:
    logger = SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        info=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )

    astrbot_pkg = sys.modules.setdefault("astrbot", types.ModuleType("astrbot"))
    api_mod = types.ModuleType("astrbot.api")
    api_mod.logger = logger
    sys.modules["astrbot.api"] = api_mod
    setattr(astrbot_pkg, "api", api_mod)

    aiocqhttp_mod = types.ModuleType("aiocqhttp")
    aiocqhttp_mod.CQHttp = object
    sys.modules["aiocqhttp"] = aiocqhttp_mod

    aiohttp_mod = types.ModuleType("aiohttp")

    class DummyClientTimeout:
        def __init__(self, total=None):
            self.total = total

    class DummyClientSession:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("ClientSession should be patched in tests")

    aiohttp_mod.ClientTimeout = DummyClientTimeout
    aiohttp_mod.ClientSession = DummyClientSession
    sys.modules["aiohttp"] = aiohttp_mod

    bs4_mod = types.ModuleType("bs4")

    class DummyBeautifulSoup:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("BeautifulSoup is not used in these tests")

    bs4_mod.BeautifulSoup = DummyBeautifulSoup
    sys.modules["bs4"] = bs4_mod

    json5_mod = types.ModuleType("json5")
    json5_mod.loads = json.loads
    sys.modules["json5"] = json5_mod

    pydantic_mod = types.ModuleType("pydantic")

    class DummyBaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        def copy(self, deep: bool = False):
            return self.__class__(**self.__dict__)

        def model_copy(self, deep: bool = False):
            return self.copy(deep=deep)

        def model_dump(self):
            return dict(self.__dict__)

    def dummy_field(*, default=None, default_factory=None):
        if default_factory is not None:
            return default_factory()
        return default

    pydantic_mod.BaseModel = DummyBaseModel
    pydantic_mod.Field = dummy_field
    sys.modules["pydantic"] = pydantic_mod

    astrbot_core_mod = sys.modules.setdefault(
        "astrbot.core", types.ModuleType("astrbot.core")
    )
    astrbot_core_config_mod = sys.modules.setdefault(
        "astrbot.core.config", types.ModuleType("astrbot.core.config")
    )
    astrbot_config_mod = types.ModuleType("astrbot.core.config.astrbot_config")

    class DummyAstrBotConfig(dict):
        def save_config(self):
            return None

    astrbot_config_mod.AstrBotConfig = DummyAstrBotConfig
    sys.modules["astrbot.core.config.astrbot_config"] = astrbot_config_mod
    setattr(astrbot_core_config_mod, "astrbot_config", astrbot_config_mod)

    astrbot_core_star_mod = sys.modules.setdefault(
        "astrbot.core.star", types.ModuleType("astrbot.core.star")
    )
    astrbot_context_mod = types.ModuleType("astrbot.core.star.context")

    class DummyContext:
        pass

    astrbot_context_mod.Context = DummyContext
    sys.modules["astrbot.core.star.context"] = astrbot_context_mod
    setattr(astrbot_core_star_mod, "context", astrbot_context_mod)

    setattr(astrbot_core_mod, "config", astrbot_core_config_mod)
    setattr(astrbot_core_mod, "star", astrbot_core_star_mod)


_install_test_stubs()

from core.qzone.api import QzoneAPI
from core.qzone.constants import QZONE_CODE_UNKNOWN, QZONE_MSG_JSON_PARSE_ERROR
from core.qzone.parser import QzoneParser
from core.qzone.model import QzoneContext
from core.model import Post


class FakeResponse:
    def __init__(self, status: int, text: str):
        self.status = status
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def text(self) -> str:
        return self._text


class FakeClientSession:
    def __init__(self, responses: list[tuple[int, str]]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method: str, url: str, **kwargs):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "params": kwargs.get("params"),
                "data": kwargs.get("data"),
                "headers": kwargs.get("headers"),
                "cookies": kwargs.get("cookies"),
                "timeout": kwargs.get("timeout"),
            }
        )
        status, text = self._responses.pop(0)
        return FakeResponse(status, text)

    async def close(self):
        return None


class FakeLoginSession:
    def __init__(self, contexts: list[QzoneContext]):
        self._contexts = list(contexts)
        self._index = 0
        self.reset_calls: list[bool] = []
        self.login_calls = 0

    async def get_ctx(self) -> QzoneContext:
        return self._contexts[self._index]

    async def reset_login_state(self, *, clear_cookies: bool = False) -> None:
        self.reset_calls.append(clear_cookies)

    async def login(self, cookies_str: str | None = None) -> QzoneContext:
        self.login_calls += 1
        if self._index < len(self._contexts) - 1:
            self._index += 1
        return self._contexts[self._index]


class AutoReloginTests(unittest.IsolatedAsyncioTestCase):
    def test_parse_response_uses_first_balanced_json_object(self):
        raw = 'QZFL.callback({"code":0,"data":{"text":"a } brace"}}); var extra = {"code":-1}'

        parsed = QzoneParser.parse_response(raw)

        self.assertEqual(parsed["code"], 0)
        self.assertEqual(parsed["data"]["text"], "a } brace")

    def test_parse_response_skips_outer_jsonp_try_block(self):
        raw = 'try{_preloadCallback({"code":0,"data":{"msglist":[]}});}catch(e){}'

        parsed = QzoneParser.parse_response(raw)

        self.assertEqual(parsed["code"], 0)
        self.assertEqual(parsed["data"]["msglist"], [])

    async def test_get_feeds_rebuilds_params_after_relogin(self):
        old_ctx = QzoneContext(
            uin=10001,
            skey="old_skey",
            p_skey="old_p_skey",
            raw_cookies={"uin": "o10001"},
        )
        new_ctx = QzoneContext(
            uin=10001,
            skey="new_skey",
            p_skey="new_p_skey",
            raw_cookies={"uin": "o10001"},
        )
        fake_http = FakeClientSession(
            [
                (200, '{"code":-3000,"message":"expired","data":{}}'),
                (200, '{"code":0,"message":"","data":{"msglist":[]}}'),
            ]
        )
        fake_session = FakeLoginSession([old_ctx, new_ctx])
        config = SimpleNamespace(
            timeout=10,
            request_interval=0,
            request_jitter=0,
            auto_reset_on_login_expired=True,
        )

        with patch("core.qzone.client.aiohttp.ClientSession", return_value=fake_http):
            api = QzoneAPI(fake_session, config)

        try:
            resp = await api.get_feeds("20002")
        finally:
            await api.close()

        self.assertTrue(resp.ok)
        self.assertEqual(fake_session.login_calls, 1)
        self.assertEqual(fake_session.reset_calls, [True])
        self.assertEqual(len(fake_http.calls), 2)
        self.assertEqual(fake_http.calls[0]["params"]["g_tk"], old_ctx.gtk2)
        self.assertEqual(fake_http.calls[1]["params"]["g_tk"], new_ctx.gtk2)
        self.assertNotEqual(fake_http.calls[0]["params"]["g_tk"], fake_http.calls[1]["params"]["g_tk"])

    async def test_get_qzonetoken_retries_after_html_login_page(self):
        old_ctx = QzoneContext(
            uin=30003,
            skey="old_skey",
            p_skey="old_p_skey",
            raw_cookies={"uin": "o30003"},
        )
        new_ctx = QzoneContext(
            uin=40004,
            skey="new_skey",
            p_skey="new_p_skey",
            raw_cookies={"uin": "o40004"},
        )
        fake_http = FakeClientSession(
            [
                (200, "<html><body>请先登录 QQ空间</body></html>"),
                (200, '<script>window.g_qzonetoken = "fresh-token";</script>'),
            ]
        )
        fake_session = FakeLoginSession([old_ctx, new_ctx])
        config = SimpleNamespace(
            timeout=10,
            request_interval=0,
            request_jitter=0,
            auto_reset_on_login_expired=True,
        )

        with patch("core.qzone.client.aiohttp.ClientSession", return_value=fake_http):
            api = QzoneAPI(fake_session, config)

        try:
            token = await api._get_qzonetoken()
        finally:
            await api.close()

        self.assertEqual(token, "fresh-token")
        self.assertEqual(fake_session.login_calls, 1)
        self.assertEqual(fake_http.calls[0]["url"], f"{api.BASE_URL}/{old_ctx.uin}")
        self.assertEqual(fake_http.calls[1]["url"], f"{api.BASE_URL}/{new_ctx.uin}")

    async def test_upload_image_rebuilds_payload_after_relogin(self):
        old_ctx = QzoneContext(
            uin=30003,
            skey="old_skey",
            p_skey="old_p_skey",
            raw_cookies={"uin": "o30003"},
        )
        new_ctx = QzoneContext(
            uin=40004,
            skey="new_skey",
            p_skey="new_p_skey",
            raw_cookies={"uin": "o40004"},
        )
        fake_http = FakeClientSession(
            [
                (200, '{"ret":-1,"msg":"请先登录","data":{}}'),
                (200, '{"ret":0,"msg":"","data":{}}'),
            ]
        )
        fake_session = FakeLoginSession([old_ctx, new_ctx])
        config = SimpleNamespace(
            timeout=10,
            request_interval=0,
            request_jitter=0,
            auto_reset_on_login_expired=True,
        )

        with patch("core.qzone.client.aiohttp.ClientSession", return_value=fake_http):
            api = QzoneAPI(fake_session, config)

        try:
            resp = await api._upload_image(b"image")
        finally:
            await api.close()

        self.assertTrue(resp.ok)
        self.assertEqual(fake_session.login_calls, 1)
        self.assertEqual(fake_session.reset_calls, [True])
        self.assertEqual(len(fake_http.calls), 2)
        self.assertEqual(fake_http.calls[0]["data"]["uin"], old_ctx.uin)
        self.assertEqual(fake_http.calls[0]["data"]["skey"], old_ctx.skey)
        self.assertEqual(fake_http.calls[0]["data"]["p_skey"], old_ctx.p_skey)
        self.assertEqual(fake_http.calls[1]["data"]["uin"], new_ctx.uin)
        self.assertEqual(fake_http.calls[1]["data"]["skey"], new_ctx.skey)
        self.assertEqual(fake_http.calls[1]["data"]["p_skey"], new_ctx.p_skey)

    async def test_json_parse_error_with_token_marker_does_not_relogin(self):
        ctx = QzoneContext(
            uin=50005,
            skey="skey",
            p_skey="p_skey",
            raw_cookies={"uin": "o50005"},
        )
        fake_http = FakeClientSession([(200, "{\n  g_tk: oops\n}")])
        fake_session = FakeLoginSession([ctx])
        config = SimpleNamespace(
            timeout=10,
            request_interval=0,
            request_jitter=0,
            auto_reset_on_login_expired=True,
        )

        with patch("core.qzone.client.aiohttp.ClientSession", return_value=fake_http):
            api = QzoneAPI(fake_session, config)

        try:
            raw = await api.request("GET", "https://example.test/api")
        finally:
            await api.close()

        self.assertEqual(raw["code"], QZONE_CODE_UNKNOWN)
        self.assertEqual(raw["message"], QZONE_MSG_JSON_PARSE_ERROR)
        self.assertEqual(fake_session.login_calls, 0)
        self.assertEqual(fake_session.reset_calls, [])

    async def test_publish_rejects_images_when_none_can_be_read(self):
        ctx = QzoneContext(
            uin=60006,
            skey="skey",
            p_skey="p_skey",
            raw_cookies={"uin": "o60006"},
        )
        fake_http = FakeClientSession([])
        fake_session = FakeLoginSession([ctx])
        config = SimpleNamespace(
            timeout=10,
            request_interval=0,
            request_jitter=0,
            auto_reset_on_login_expired=True,
        )
        post = Post(text="test", images=["/missing-image.png"])

        with patch("core.qzone.client.aiohttp.ClientSession", return_value=fake_http):
            api = QzoneAPI(fake_session, config)

        try:
            with patch(
                "core.qzone.api.normalize_images",
                new=AsyncMock(return_value=[]),
            ):
                with self.assertRaises(RuntimeError):
                    await api.publish(post)
        finally:
            await api.close()

        self.assertEqual(fake_http.calls, [])


if __name__ == "__main__":
    unittest.main()
