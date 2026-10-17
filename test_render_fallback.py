"""官方 HTML 渲染及文字兜底的离线回归测试，无需安装 AstrBot。

运行：python -m unittest test_render_fallback -v
"""

import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch


def _load_plugin(plugin_path=None):
    names = (
        "aiohttp", "bs4", "astrbot", "astrbot.api",
        "astrbot.api.message_components", "astrbot.api.event",
        "astrbot.api.star", "astrbot.core", "astrbot.core.utils",
        "astrbot.core.utils.session_waiter",
    )
    modules = {name: types.ModuleType(name) for name in names}
    modules["bs4"].BeautifulSoup = Mock()
    modules["astrbot.api"].logger = Mock()
    modules["astrbot.api.message_components"].Image = Mock()
    modules["astrbot.api.message_components"].Plain = Mock()
    events = modules["astrbot.api.event"]
    events.AstrMessageEvent = object
    events.MessageEventResult = Mock()
    events.filter = types.SimpleNamespace(command=lambda *a, **kw: lambda f: f,
        event_message_type=lambda *a, **kw: lambda f: f,
        EventMessageType=types.SimpleNamespace(ALL="all"))
    stars = modules["astrbot.api.star"]
    stars.Context = object
    stars.Star = object
    stars.register = lambda *a, **kw: lambda cls: cls
    waiters = modules["astrbot.core.utils.session_waiter"]
    waiters.SessionController = object
    waiters.session_waiter = Mock()
    spec = importlib.util.spec_from_file_location(
        "mcmod_under_test", plugin_path or Path(__file__).with_name("main.py")
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, modules):
        spec.loader.exec_module(module)
    return module


class FakeEvent:
    def __init__(self, text=""):
        self.unified_msg_origin = "test:private:user"
        self.message_str = text
        self.message_obj = types.SimpleNamespace(message=[], self_id="bot")

    def get_sender_id(self):
        return "user"

    def plain_result(self, text):
        return ("plain", text)

    def image_result(self, path):
        if not Path(path).is_file():
            raise AssertionError("图片发送前被删除")
        return ("image", path)

    def chain_result(self, components):
        return ("chain", components)


class RenderFallbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.module = _load_plugin()
        self.plugin = object.__new__(self.module.McmodSearchPlugin)
        self.plugin._search_waiters = {}
        self.plugin._closing = False
        self.event = FakeEvent()
        self.service_error = RuntimeError("All endpoints failed: HTTP 503")
        self.target = {
            "title": "测试模组", "url": "https://www.mcmod.cn/class/1.html",
            "snippet": "测试摘要", "cat": "",
        }
        self.info = {
            "title": "测试模组", "meta": [], "authors_html": "",
            "images": [], "badges": [], "content": "", "cover": "",
        }

    async def test_service_failure_preserves_list_numbers_and_urls(self):
        self.plugin._render_html = AsyncMock(side_effect=self.service_error)
        messages = [msg async for msg in self.plugin._send_list(
            self.event, "html", [self.target], 10
        )]
        self.assertIn("11. 测试模组", messages[0][1])
        self.assertIn(self.target["url"], messages[0][1])
        hint = self.module.logger.warning.call_args.args[0]
        self.assertIn("AstrBot 文转图服务", hint)
        self.assertIn("HTTP 503", hint)

    async def test_render_timeout_also_returns_text(self):
        self.plugin._render_html = AsyncMock(side_effect=TimeoutError("render timeout"))
        messages = [msg async for msg in self.plugin._send_list(
            self.event, "html", [self.target], 0
        )]
        self.assertIn("1. 测试模组", messages[0][1])
        self.module.logger.error.assert_not_called()

    async def test_service_failure_preserves_detail_link(self):
        self.plugin._detail_info = AsyncMock(return_value=self.info)
        self.plugin._render_html = AsyncMock(side_effect=self.service_error)
        messages = [msg async for msg in self.plugin._send_detail(self.event, self.target)]
        self.assertIn("详情图片暂时不可用", messages[-1][1])
        self.assertIn(self.target["url"], messages[-1][1])

    async def test_successful_image_exists_until_message_is_consumed(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "list.png"
            image_path.write_bytes(b"image")
            self.plugin._render_html = AsyncMock(return_value=str(image_path))
            messages = self.plugin._send_list(self.event, "html", [self.target], 0)
            self.assertEqual((await anext(messages))[0], "image")
            self.assertTrue(image_path.exists())
            with self.assertRaises(StopAsyncIteration):
                await anext(messages)
            self.assertFalse(image_path.exists())

    async def test_closing_generator_cleans_image(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "list.png"
            image_path.write_bytes(b"image")
            self.plugin._render_html = AsyncMock(return_value=str(image_path))
            messages = self.plugin._send_list(self.event, "html", [self.target], 0)
            await anext(messages)
            await messages.aclose()
            self.assertFalse(image_path.exists())

    async def test_render_uses_official_api_and_treats_html_as_data(self):
        self.plugin.html_render = AsyncMock(return_value="rendered.jpg")
        page_html = '<h1>搜索 {{ 7 * 7 }} {% raw %}</h1><style>body {margin:0}</style>'
        result = await self.plugin._render_html(page_html)
        self.assertEqual(result, "rendered.jpg")
        args, kwargs = self.plugin.html_render.await_args
        self.assertEqual(args, ("{{ page_html | safe }}", {"page_html": page_html}))
        self.assertFalse(kwargs["return_url"])
        self.assertTrue(kwargs["options"]["full_page"])
        self.assertEqual(kwargs["options"]["viewport_width"], 720)
        self.assertEqual(kwargs["options"]["type"], "jpeg")

    async def test_render_service_errors_propagate_to_fallback(self):
        self.plugin.html_render = AsyncMock(side_effect=self.service_error)
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            await self.plugin._render_html("html")

    async def test_official_render_has_bounded_wait(self):
        self.plugin.html_render = AsyncMock(return_value="rendered.jpg")

        async def timeout(awaitable, *, timeout):
            awaitable.close()
            raise TimeoutError("render service timed out")

        with patch.object(self.module.asyncio, "wait_for", side_effect=timeout) as wait:
            with self.assertRaises(TimeoutError):
                await self.plugin._render_html("html")
        self.assertEqual(wait.call_args.kwargs["timeout"], 45)

    async def test_successful_detail_keeps_image_until_sent_then_cleans(self):
        with tempfile.TemporaryDirectory() as directory:
            image_path = Path(directory) / "detail.jpg"
            image_path.write_bytes(b"image")
            self.plugin._detail_info = AsyncMock(return_value=self.info)
            self.plugin._render_html = AsyncMock(return_value=str(image_path))
            messages = self.plugin._send_detail(self.event, self.target)
            self.assertEqual((await anext(messages))[0], "plain")
            self.assertEqual((await anext(messages))[0], "chain")
            self.assertTrue(image_path.exists())
            self.module.Comp.Image.fromFileSystem.assert_called_once_with(str(image_path))
            self.module.Comp.Plain.assert_called_once_with(
                f"页面链接：{self.target['url']}"
            )
            with self.assertRaises(StopAsyncIteration):
                await anext(messages)
            self.assertFalse(image_path.exists())

    async def test_service_failure_allows_paging_selection_and_continue(self):
        inputs = iter(("第2页", "11", "继续", "第2页", "11"))

        async def wait_reply(event, handler, **kwargs):
            await handler(Mock(), FakeEvent(next(inputs)))

        self.plugin._wait_for_search_reply = wait_reply
        results = [dict(self.target, title=f"模组{i}") for i in range(1, 12)]
        self.plugin._search = AsyncMock(return_value=(results, 1))
        self.plugin._detail_info = AsyncMock(return_value=self.info)
        self.plugin._render_html = AsyncMock(side_effect=self.service_error)
        messages = [msg async for msg in self.plugin.mcmod_search(self.event, "测试")]
        text = "\n".join(msg[1] for msg in messages)
        self.assertNotIn("会话处理出错", text, str(self.module.logger.error.call_args_list))
        self.assertIn("11. 模组11", text)
        self.assertEqual(text.count("详情图片暂时不可用"), 2)


if __name__ == "__main__":
    unittest.main()
