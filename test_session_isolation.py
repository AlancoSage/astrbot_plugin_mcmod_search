"""搜索等待与正常消息、其他插件、其他用户之间的隔离回归测试。"""

import asyncio
import os
import types
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_render_fallback import _load_plugin


class Event:
    def __init__(self, text="", *, sender="alice", origin="onebot:group:1", bot="bot"):
        self.message_str = text
        self.unified_msg_origin = origin
        self.message_obj = types.SimpleNamespace(self_id=bot, message=[])
        self.sender = sender
        self.stopped = False
        self.suppress_llm = False
        self.should_call_llm = Mock(side_effect=self._set_llm)
        self.send = AsyncMock()

    def _set_llm(self, value):
        self.suppress_llm = value

    def get_sender_id(self):
        return self.sender

    def plain_result(self, text):
        return ("plain", text)

    def stop_event(self):
        self.stopped = True


class SessionIsolationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.module = _load_plugin(os.environ.get("MCMOD_TEST_MAIN"))
        self.plugin = object.__new__(self.module.McmodSearchPlugin)
        self.plugin._search_waiters = {}
        self.plugin._closing = False
        self.owner = Event()

    async def asyncTearDown(self):
        await self.plugin.terminate()
        await asyncio.sleep(0)

    async def wait_reply(self, *, event=None, continuation=False, stop=True):
        seen = []

        async def handler(controller, incoming):
            seen.append(incoming.message_str)
            if stop:
                controller.stop()

        task = asyncio.create_task(self.plugin._wait_for_search_reply(
            event or self.owner, handler, continuation=continuation
        ))
        await asyncio.sleep(0)
        return task, seen

    async def dispatch(self, event):
        await self.plugin.receive_search_reply(event)
        # 模拟消息管线继续调用其他插件。
        other_plugin = Mock()
        if not event.stopped:
            other_plugin(event)
        other_plugin.assert_called_once_with(event)

    async def test_chat_commands_and_media_pass_while_waiting_in_every_phase(self):
        for continuation in (False, True):
            task, seen = await self.wait_reply(continuation=continuation)
            for text in ("今天天气怎么样", "/角色 状态", "MC百科搜索 新关键词", "继续说刚才的话题", "", "不用担心"):
                event = Event(text)
                await self.dispatch(event)
                event.should_call_llm.assert_not_called()
            self.assertEqual(seen, [])
            self.assertFalse(task.done())
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_continue_and_decline_only_finish_search_and_keep_propagation(self):
        for text in ("继续", "不用", "取消"):
            task, seen = await self.wait_reply(continuation=True)
            event = Event(text)
            await self.dispatch(event)
            await task
            self.assertEqual(seen, [text])
            self.assertEqual(self.plugin._search_waiters, {})
            self.assertTrue(event.suppress_llm)
            chat = Event("接着聊今天的事")
            await self.dispatch(chat)
            chat.should_call_llm.assert_not_called()

    async def test_other_users_chats_and_accounts_cannot_answer(self):
        task, seen = await self.wait_reply(continuation=True)
        for event in (Event("继续", sender="bob"), Event("继续", origin="onebot:group:2"), Event("继续", bot="other-bot")):
            event.message_obj.message = [types.SimpleNamespace(qq="bot"), types.SimpleNamespace(id="reply")]
            await self.dispatch(event)
            event.should_call_llm.assert_not_called()
        self.assertEqual(seen, [])
        self.assertFalse(task.done())
        await self.dispatch(Event("不用"))
        await task

    async def test_select_paging_filter_and_more_are_still_supported(self):
        for text in ("1", "第 2 页", "p3", "只看模组", "更多", "取消"):
            task, seen = await self.wait_reply()
            event = Event(text)
            await self.dispatch(event)
            await task
            self.assertEqual(seen, [text])
            self.assertTrue(event.suppress_llm)

    async def test_unanswered_search_expires_without_affecting_messages(self):
        real_wait_for = asyncio.wait_for

        async def quick_timeout(awaitable, *, timeout):
            self.assertEqual(timeout, 60)
            return await real_wait_for(awaitable, timeout=0.02)

        with patch.object(self.module.asyncio, "wait_for", side_effect=quick_timeout):
            task, seen = await self.wait_reply(continuation=True)
            event = Event("不回复这个问题，聊别的")
            await self.dispatch(event)
            event.should_call_llm.assert_not_called()
            with self.assertRaises(TimeoutError):
                await task
        self.assertEqual(seen, [])
        self.assertEqual(self.plugin._search_waiters, {})
        await self.dispatch(Event("继续"))

    async def test_replacement_does_not_remove_new_wait_and_users_are_independent(self):
        old, old_seen = await self.wait_reply(continuation=True)
        new, new_seen = await self.wait_reply(continuation=True)
        await old
        bob = Event(sender="bob")
        other, other_seen = await self.wait_reply(event=bob, continuation=True)
        await self.dispatch(Event("继续"))
        await new
        self.assertEqual(old_seen, [])
        self.assertEqual(new_seen, ["继续"])
        self.assertFalse(other.done())
        await self.dispatch(Event("不用", sender="bob"))
        await other
        self.assertEqual(other_seen, ["不用"])

    async def test_restart_search_releases_old_wait(self):
        old, _ = await self.wait_reply(continuation=True)
        self.plugin._search = AsyncMock(return_value=([], 1))
        messages = [message async for message in self.plugin.mcmod_search(self.owner, "新关键词")]
        await old
        self.assertEqual(self.plugin._search_waiters, {})
        self.assertIn("没有找到", messages[0][1])

    async def test_unload_releases_all_waits_and_cancellation_cleans_up(self):
        task, _ = await self.wait_reply()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(self.plugin._search_waiters, {})
        first, _ = await self.wait_reply()
        second, _ = await self.wait_reply(event=Event(sender="bob"))
        await self.plugin.terminate()
        await asyncio.gather(first, second)
        self.assertEqual(self.plugin._search_waiters, {})
        task, _ = await self.wait_reply()
        await task
        self.assertEqual(self.plugin._search_waiters, {})

    async def test_bad_callback_releases_wait_without_stopping_other_plugins(self):
        handler = AsyncMock(side_effect=RuntimeError("send failure"))
        task = asyncio.create_task(self.plugin._wait_for_search_reply(self.owner, handler))
        await asyncio.sleep(0)
        await self.dispatch(Event("1"))
        with self.assertRaisesRegex(RuntimeError, "send failure"):
            await task
        self.assertEqual(self.plugin._search_waiters, {})


if __name__ == "__main__":
    unittest.main()
