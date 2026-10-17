import asyncio
import html as html_lib
import os
import re

import aiohttp
from bs4 import BeautifulSoup

import astrbot.api.message_components as Comp
from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageEventResult, filter
from astrbot.api.star import Context, Star, register

SEARCH_URL = "https://search.mcmod.cn/s"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

# 清理 mcmod 摘要里的 bbcode 标记，如 [h1=概述]、[h2=内容]、[ban:title_menu]、[/#] 等
BBCODE_RE = re.compile(
    r"\[(?:/?h\d=[^\]]*|/?h\d|ban:[^\]]*|/[^\]]*|[a-z]{1,10}(?:=[^\]]*)?)\]",
    re.IGNORECASE,
)


def _clean_text(text: str) -> str:
    text = BBCODE_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def _log_render_error(error: Exception) -> None:
    logger.warning(
        "mcmod 官方 HTML 渲染失败，已回退文字消息；"
        f"请检查 AstrBot 文转图服务和网络连接: {error}"
    )


# 结果列表模板：先填充数据，再交给 AstrBot 官方 HTML 渲染接口。
LIST_TMPL = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  body {{ margin: 0; padding: 14px; background: #f5f6f8;
        font-family: "Microsoft YaHei", sans-serif; }}
  .card {{ width: 660px; margin: 0 auto; background: #fff; border-radius: 20px;
          overflow: hidden; box-shadow: 0 8px 24px rgba(60,80,70,.18); }}
  .head {{ background: linear-gradient(135deg, #2c6e49 0%, #1e4d34 100%);
          padding: 14px 22px 12px; }}
  .head .brand {{ display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }}
  .head .logo {{ width: 22px; height: 22px; border-radius: 6px; background: #7ed957;
                color: #1e4d34; font-size: 12px; font-weight: 800; display: flex;
                align-items: center; justify-content: center; }}
  .head .brand span {{ color: #cfe8d8; font-size: 11px; letter-spacing: 1px; }}
  h1 {{ font-size: 18px; color: #fff; margin: 0; }}
  .sub {{ padding: 9px 22px; font-size: 11px; color: #2c6e49; background: #eef7f0;
         border-bottom: 1px solid #dcefe2; }}
  .items {{ padding: 4px 12px; }}
  .item {{ padding: 10px 10px; border-bottom: 1px dashed #e3ede7; display: flex;
          gap: 12px; align-items: flex-start; }}
  .item:last-child {{ border-bottom: none; }}
  .num {{ flex: none; min-width: 26px; height: 26px; border-radius: 8px;
         background: linear-gradient(135deg, #2c6e49, #1e7e34); color: #fff;
         font-size: 14px; font-weight: 700; display: flex; align-items: center;
         justify-content: center; padding: 0 4px; box-sizing: border-box; }}
  .t {{ font-size: 14px; color: #2c3e50; font-weight: 600; line-height: 1.4; }}
  .cat {{ display: inline-block; height: 19px; line-height: 19px; padding: 0 8px;
         border-radius: 5px; color: #fff; font-size: 11px; font-weight: 600;
         margin-right: 7px; vertical-align: 2px; }}
  .s {{ font-size: 11px; color: #8a9a90; line-height: 1.6; margin-top: 2px; }}
  .foot {{ background: #f2f8f4; padding: 8px 22px; font-size: 11px; color: #7a8f83;
          text-align: center; }}
  .foot .heart {{ color: #2cbe4e; font-weight: 700; }}
</style></head><body>
<div class="card">
  <div class="head">
    <div class="brand"><div class="logo">MC</div><span>MC百科 · MCWIKI.CN</span></div>
    <h1>搜索「{keyword}」</h1>
  </div>
  <div class="sub">共 {count} 条结果 · 第 {page}/{pages} 页 · 回复数字选择（{start}-{end}）· “第X页”翻页 · “取消”退出</div>
  <div class="items">
  {items}
  </div>
  <div class="foot">Powered by <span class="heart">MC百科</span> mcmod.cn · astrbot_plugin_mcmod_search</div>
</div>
</body></html>"""

ITEM_TMPL = """<div class="item">
  <div class="num">{i}</div>
  <div><div class="t">{title}</div>{snippet}</div>
</div>"""

# 搜索结果分类标记：mcmod 分类 ID → 名字（胶囊颜色在 CSS 里循环取）
CATEGORY_NAMES = {
    "c_1": "科技",
    "c_2": "魔法",
    "c_3": "冒险",
    "c_4": "农业",
    "c_5": "装饰",
    "c_7": "LIB",
    "c_21": "魔改",
    "c_23": "实用",
    "c_24": "辅助",
}
CATEGORY_COLORS = [
    "#2c6e49", "#b06ab3", "#c05621", "#2b6cb0", "#c53030", "#6b7280",
    "#b7791f", "#319795", "#d53f8c", "#4a5568",
]

DETAIL_TMPL = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  body {{ margin: 0; background: #f5f6f8; font-family: "Microsoft YaHei", sans-serif;
        padding: 14px; }}
  .card {{ width: 660px; margin: 0 auto; background: #fff;
          border-radius: 20px; overflow: hidden;
          box-shadow: 0 8px 24px rgba(60,80,70,.18); }}
  .head {{ padding: 16px 22px 12px; display: flex; align-items: center; gap: 14px; }}
  .head .logo {{ flex: none; width: 56px; height: 56px; border-radius: 14px;
                background: #f2f8f4; background-size: cover; background-position: center;
                border: 2px solid #e3ede7; }}
  .head .tt {{ flex: 1; min-width: 0; }}
  h1 {{ font-size: 17px; color: #3a3a4c; margin: 0; line-height: 1.4; }}
  .badges {{ display: inline-block; margin-left: 8px; vertical-align: 2px; }}
  .badge {{ display: inline-block; height: 20px; line-height: 20px; padding: 0 8px;
           border-radius: 5px 0 0 5px; color: #fff; font-size: 11px; font-weight: 600;
           margin-right: 1px; }}
  .badge.src {{ border-radius: 0 5px 5px 0; margin-right: 6px; }}
  .url {{ font-size: 11px; color: #5a89b0; word-break: break-all; margin-top: 4px; }}
  .body {{ padding: 0 22px 14px; }}
  .cols {{ display: flex; gap: 14px; margin-bottom: 12px; }}
  .meta {{ flex: none; width: 224px; background: #f7f9fb;
          border-radius: 12px; padding: 6px 12px; align-self: flex-start;
          border-left: 3px solid #2cbe4e; }}
  .meta .row {{ font-size: 11px; color: #6a6a7c; line-height: 1.5;
               padding: 7px 0; border-bottom: 1px dashed #e3ede7;
               display: flex; flex-wrap: wrap; align-items: center; }}
  .meta .row:last-child {{ border-bottom: none; }}
  .meta .row b {{ color: #1e4d34; font-weight: 700; }}
  .meta .row .v {{ color: #3a3a4c; font-weight: 500; }}
  .meta .tag {{ display: inline-block; height: 18px; line-height: 18px;
               padding: 0 7px; border-radius: 9px; font-size: 10px; margin: 2px 3px 0 0; }}
  .meta .t-green {{ background: #e2f5e6; color: #1e7e34; }}
  .meta .t-blue {{ background: #e3eefb; color: #2b6cb0; }}
  .meta .t-orange {{ background: #fdeee0; color: #c05621; }}
  .meta .t-purple {{ background: #f0e8fb; color: #6b46c1; }}
  .member-card {{ display: inline-flex; align-items: center; gap: 6px;
                 background: #fff; border: 1px solid #e3ede7; border-radius: 20px;
                 padding: 3px 10px 3px 4px; margin: 3px 5px 3px 0; }}
  .member-card .m-avatar {{ width: 24px; height: 24px; border-radius: 50%;
                           object-fit: cover; }}
  .member-card .m-name {{ font-size: 11px; color: #2c6e49; font-weight: 600; }}
  .member-card .role {{ font-size: 9px; color: #fff; background: #2cbe4e;
                       border-radius: 8px; padding: 1px 6px; }}
  .content {{ flex: 1; font-size: 12px; color: #4a4a5c; line-height: 1.7;
             word-break: break-word; overflow: hidden; position: relative; }}
  .content .fade {{ position: absolute; left: 0; right: 0; bottom: 0; height: 44px;
                   background: linear-gradient(rgba(255,255,255,0), #fff 45%);
                   font-size: 11px; color: #5a89b0; text-align: right;
                   line-height: 16px; padding-right: 4px; box-sizing: border-box;
                   padding-top: 26px; }}
  .content .sec {{ font-size: 13px; color: #1e7e34; font-weight: 700;
                  margin: 10px 0 4px; padding-left: 8px;
                  border-left: 3px solid #2cbe4e; line-height: 1.4; }}
  .content .sec:first-child {{ margin-top: 0; }}
  .content .para {{ margin: 4px 0; }}
  .content .li {{ margin: 3px 0; padding-left: 4px; color: #4a4a5c; }}
  .imgs {{ display: flex; gap: 10px; margin-bottom: 12px; }}
  .imgs div {{ flex: 1; height: 130px; border-radius: 12px; background-size: cover;
              background-position: center; border: 1px solid #e3ede7; }}
  .foot {{ padding: 8px 22px 12px; font-size: 11px; color: #8a8a92; text-align: center; }}
  .foot .heart {{ color: #2cbe4e; font-weight: 700; }}
</style></head><body>
<div class="card">
  <div class="head">
    <div class="logo" style="background-image:url('{cover}')"></div>
    <div class="tt">
      <h1>{title}<span class="badges">{badges}</span></h1>
      <div class="url">🔗 {url}</div>
    </div>
  </div>
  <div class="body">
    <div class="cols">
      <div class="meta">{meta}</div>
      <div class="content">{content}</div>
    </div>
    <div class="imgs">{imgs}</div>
  </div>
  <div class="foot">Powered by <span class="heart">MC百科</span> mcmod.cn · astrbot_plugin_mcmod_search</div>
</div>
<script>
  // 正文高度动态匹配左栏：裁切到 meta 高度，溢出时加渐隐省略提示
  window.addEventListener("load", function () {{
    var meta = document.querySelector(".meta");
    var content = document.querySelector(".content");
    if (!meta || !content) return;
    var h = meta.offsetHeight;
    content.style.maxHeight = h + "px";
    if (content.scrollHeight > h + 4) {{
      var fade = document.createElement("div");
      fade.className = "fade";
      fade.textContent = "… 点击页面链接查看更多";
      content.appendChild(fade);
    }}
  }});
</script>
</body></html>"""

# mcmod 官方状态标签配色
BADGE_COLORS = {
    0: "#222222", 1: "#2cbe4e", 2: "#cb6431", 3: "#cb2431",
    4: "#adadad", 5: "#3675e9", 6: "#303030",
}

# 信息栏精排：按字段类型选择标签胶囊颜色，作者行高亮
TAG_COLOR_KEYS = {
    "模组标签": "t-green", "支持的MC版本": "t-blue", "相关链接": "t-orange",
}


def _build_author_cards(soup) -> str:
    """作者成员卡片：头像 + 名字 + 角色标签（从详情页 DOM 解析）"""
    cards = ""
    if soup is None:
        return cards
    info_box = soup.select_one(".class-info-left")
    li = None
    for x in (info_box.select("li") if info_box else []):
        if "Mod作者" in x.get_text():
            li = x
            break
    if li is not None:
        for m in li.select(".frame ul > li"):
            avatar = m.select_one(".avatar img")
            name = m.select_one(".name")
            # 角色在 .position（多成员页）/ .member span:not(.name)（单成员页）
            role_el = m.select_one(".member .position") or m.select_one(
                ".member span:not(.name)"
            )
            av = (avatar.get("src") if avatar else "") or ""
            if av.startswith("//"):
                av = "https:" + av
            role = (
                f'<span class="role">{html_lib.escape(role_el.get_text(strip=True))}</span>'
                if role_el is not None and role_el.get_text(strip=True)
                else ""
            )
            nm = name.get_text(strip=True) if name else ""
            cards += (
                f'<span class="member-card">'
                f'<img class="m-avatar" src="{html_lib.escape(av)}">'
                f'<span class="m-name">{html_lib.escape(nm)}</span>{role}'
                f"</span>"
            )
    return cards


def _build_meta_html(meta: list, authors_html: str = "") -> str:
    rows = []
    for k, v in meta:
        key = k.strip()
        v = v.strip()
        if key.startswith("Mod作者/开发团队"):
            # 作者行：成员卡片；解析失败时回退到文字胶囊
            cards = authors_html
            if not cards:
                names = [
                    n.strip()
                    for n in v.replace("显示更多成员", "").split("/")
                    if n.strip()
                ]
                cards = "".join(
                    f'<span class="tag t-green">{html_lib.escape(n)}</span>'
                    for n in names[:8]
                )
            rows.append(
                f'<div class="row"><b>{html_lib.escape(key)}</b><br>{cards}</div>'
            )
        elif key in TAG_COLOR_KEYS:
            cls = TAG_COLOR_KEYS[key]
            items = [n.strip() for n in re.split(r"[/、 ]+", v) if n.strip()]
            chips = "".join(
                f'<span class="tag {cls}">{html_lib.escape(n)}</span>'
                for n in items[:12]
            )
            rows.append(
                f'<div class="row"><b>{html_lib.escape(key)}</b><br>{chips}</div>'
            )
        else:
            rows.append(
                f'<div class="row"><b>{html_lib.escape(key)}:</b>'
                f'<span class="v">{html_lib.escape(v)}</span></div>'
            )
    return "".join(rows)


class _SearchReply:
    """仅保存当前插件的一次搜索等待，不注册框架的全局会话拦截器。"""

    def __init__(self, handler, continuation):
        self.handler = handler
        self.continuation = continuation
        self.future = asyncio.get_running_loop().create_future()
        self.lock = asyncio.Lock()

    def accepts(self, text):
        if self.continuation:
            return text in ("继续", "是", "y", "Y", "不用", "否", "n", "N", "取消")
        return text in ("取消", "c", "C", "更多", "加载更多", "下批") or bool(
            re.fullmatch(r"只看\s*(模组|整合包|教程|资料|全部)", text)
            or re.fullmatch(r"(?:第)?\s*(\d+)\s*页|[pP]\s*(\d+)", text)
            or re.fullmatch(r"\d+", text)
        )

    def stop(self):
        if not self.future.done():
            self.future.set_result(None)


@register(
    "astrbot_plugin_mcmod_search",
    "AlancoSage",
    "MC百科搜索：官方 HTML 渲染搜索列表和详情卡片并发送链接",
    "v1.0.4",
)
class McmodSearchPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)
        self._search_waiters = {}
        self._closing = False

    @staticmethod
    def _search_key(event):
        return (
            str(event.message_obj.self_id),
            event.unified_msg_origin,
            str(event.get_sender_id()),
        )

    def _stop_search_reply(self, event):
        previous = self._search_waiters.pop(self._search_key(event), None)
        if previous:
            previous.stop()

    async def _wait_for_search_reply(self, event, handler, *, continuation=False):
        if self._closing:
            return
        key = self._search_key(event)
        self._stop_search_reply(event)
        waiter = _SearchReply(handler, continuation)
        self._search_waiters[key] = waiter
        try:
            await asyncio.wait_for(waiter.future, timeout=60)
        finally:
            if self._search_waiters.get(key) is waiter:
                self._search_waiters.pop(key, None)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=100)
    async def receive_search_reply(self, event: AstrMessageEvent):
        waiter = self._search_waiters.get(self._search_key(event))
        if not waiter or waiter.future.done() or not waiter.accepts(event.message_str.strip()):
            return
        async with waiter.lock:
            if waiter.future.done() or self._search_waiters.get(self._search_key(event)) is not waiter:
                return
            # AstrBot 的此开关仅禁止本条操作的默认 LLM 请求，其他插件仍会收到消息。
            event.should_call_llm(True)
            try:
                await waiter.handler(waiter, event)
            except Exception as error:
                if not waiter.future.done():
                    waiter.future.set_exception(error)

    async def terminate(self):
        self._closing = True
        for waiter in list(self._search_waiters.values()):
            waiter.stop()
        self._search_waiters.clear()

    @filter.command("mcmod", alias={"MC百科搜索", "mc百科搜索", "百科搜索", "mc百科查询", "百科查询"})
    async def mcmod_search(self, event: AstrMessageEvent, keyword: str):
        """搜索MC百科，回复数字选择结果后发送详情卡片和链接"""
        self._stop_search_reply(event)
        logger.info(f"mcmod_search 触发: {keyword!r}")
        keyword = keyword.strip()
        if not keyword:
            yield event.plain_result("请在指令后带上要搜索的内容，例如：MC百科搜索 铁锈")
            return

        results, mcmod_pages = await self._search(keyword)
        if not results:
            yield event.plain_result(f"没有找到与「{keyword}」相关的结果。")
            return

        # 分页渲染 + 会话选择：每页 10 条，回复“第X页”翻页
        PER_PAGE = 10
        mcmod_page = 1  # 当前 mcmod 批次
        filter_no = 0  # 当前过滤：0=全部 1=模组 2=整合包 3=资料 4=教程
        total_pages = (len(results) + PER_PAGE - 1) // PER_PAGE
        page = 1
        target = None
        try:
            while True:
                start = (page - 1) * PER_PAGE
                end = min(start + PER_PAGE, len(results))
                page_results = results[start:end]

                items_html = []
                for j, r in enumerate(page_results, start + 1):
                    s = (
                        f'<div class="s">{html_lib.escape(r["snippet"][:60])}…</div>'
                        if r["snippet"]
                        else ""
                    )
                    cat_html = ""
                    if r.get("cat"):
                        ci = list(CATEGORY_NAMES.values()).index(r["cat"]) if r["cat"] in CATEGORY_NAMES.values() else 0
                        color = CATEGORY_COLORS[ci % len(CATEGORY_COLORS)]
                        cat_html = (
                            f'<span class="cat" style="background:{color}">'
                            f'{html_lib.escape(r["cat"])}</span>'
                        )
                    items_html.append(
                        ITEM_TMPL.format(
                            i=j, title=cat_html + html_lib.escape(r["title"]), snippet=s
                        )
                    )
                page_html = LIST_TMPL.format(
                    keyword=html_lib.escape(keyword),
                    count=len(results),
                    page=page,
                    pages=total_pages,
                    start=start + 1,
                    end=end,
                    items="\n".join(items_html),
                )
                async for msg in self._send_list(event, page_html, page_results, start):
                    yield msg
                # 文字提示与图片同步发送
                yield event.plain_result(
                    f"MC百科搜索「{keyword}」共 {len(results)} 条结果 · 第 {page}/{total_pages} 页，"
                    f"回复数字选择（{start + 1}-{end}），“第X页”翻页，“只看模组/整合包/教程/资料”过滤，"
                    f"“更多”加载更多，“取消”退出"
                )

                # 通过外层字典捕获用户的选择（controller.stop() 不支持传返回值）
                choice: dict = {}
                initiator_id = event.get_sender_id()  # 会话发起人

                async def chooser(controller: _SearchReply, ev: AstrMessageEvent):
                    # 只响应：会话发起人本人，或 @机器人 / 引用回复机器人消息的消息
                    chain = ev.message_obj.message
                    self_id = ev.message_obj.self_id
                    at_bot = any(
                        getattr(c, "qq", None) == self_id
                        for c in chain
                        if type(c).__name__ == "At"
                    )
                    replied = any(type(c).__name__ == "Reply" for c in chain)
                    initiator = ev.get_sender_id() == initiator_id
                    if not (at_bot or replied or initiator):
                        return
                    text = ev.message_str.strip()
                    if text in ("取消", "c", "C"):
                        await ev.send(MessageEventResult().message("已取消选择。"))
                        controller.stop()
                        return
                    # 加载更多：抓取下一批 mcmod 结果（30 条）
                    if text in ("更多", "加载更多", "下批"):
                        choice["more"] = True
                        controller.stop()
                        return
                    # 过滤：只看模组/整合包/教程/资料 等
                    fm = re.fullmatch(r"只看\s*(模组|整合包|教程|资料|全部)", text)
                    if fm:
                        choice["filter"] = fm.group(1)
                        controller.stop()
                        return
                    # 翻页：第X页 / 第 X 页 / X页 / pX / PX（容忍空格）
                    m = re.fullmatch(r"(?:第)?\s*(\d+)\s*页|[pP]\s*(\d+)", text)
                    if m:
                        p = int(m.group(1) or m.group(2))
                        if not (1 <= p <= total_pages):
                            await ev.send(
                                MessageEventResult().message(
                                    f"页码超出范围，请回复 1-{total_pages}。"
                                )
                            )
                            return
                        choice["page"] = p
                        controller.stop()
                        return
                    if not re.fullmatch(r"\d+", text):
                        await ev.send(
                            MessageEventResult().message(
                                f"请回复数字序号（{start + 1}-{end}）、“第X页”翻页、“只看模组”过滤、“更多”加载更多或“取消”。"
                            )
                        )
                        return
                    idx = int(text)
                    if not (start + 1 <= idx <= end):
                        await ev.send(
                            MessageEventResult().message(
                                f"序号超出本页范围，本页为 {start + 1}-{end}。"
                            )
                        )
                        return
                    choice["index"] = idx
                    controller.stop()

                await self._wait_for_search_reply(event, chooser)
                if "filter" in choice:
                    # 按 mcmod filter 参数重新搜索
                    filter_word = choice["filter"]
                    filter_map = {"模组": 1, "整合包": 2, "资料": 3, "教程": 4, "全部": 0}
                    filter_no = filter_map.get(filter_word, 0)
                    if filter_no == 0:
                        results, mcmod_pages = await self._search(keyword)
                    else:
                        results, mcmod_pages = await self._search(
                            keyword, filter=filter_no
                        )
                    mcmod_page = 1
                    total_pages = (len(results) + PER_PAGE - 1) // PER_PAGE
                    page = 1
                    if not results:
                        yield event.plain_result(
                            f"「{keyword}」没有{filter_word}类型的结果。"
                        )
                        continue
                    yield event.plain_result(
                        f"已按“{filter_word}”过滤，共 {len(results)} 条结果。"
                    )
                    continue
                if "more" in choice:
                    # 加载下一批 mcmod 结果
                    if mcmod_page >= mcmod_pages:
                        yield event.plain_result(
                            f"没有更多结果了（mcmod 共 {mcmod_pages} 页）。"
                        )
                        page = 1
                        continue
                    mcmod_page += 1
                    more_results, mp = await self._search(keyword, mcmod_page)
                    mcmod_pages = max(mcmod_pages, mp)
                    results.extend(more_results)
                    total_pages = (len(results) + PER_PAGE - 1) // PER_PAGE
                    yield event.plain_result(
                        f"已加载更多，当前共 {len(results)} 条结果。"
                    )
                    continue
                if "page" in choice:
                    page = choice["page"]
                    # 跳转超出已加载结果时，自动抓取所需的 mcmod 批次
                    needed = ((page * PER_PAGE - 1) // 30) + 1  # 需要的 mcmod 批次数
                    while mcmod_page < min(needed, mcmod_pages):
                        mcmod_page += 1
                        more_results, mp = await self._search(
                            keyword, mcmod_page, filter_no
                        )
                        mcmod_pages = max(mcmod_pages, mp)
                        results.extend(more_results)
                        total_pages = (len(results) + PER_PAGE - 1) // PER_PAGE
                    yield event.plain_result(
                        f"已加载到第 {page} 页（共 {len(results)} 条结果）。"
                    )
                    continue
                if "index" in choice:
                    target = results[choice["index"] - 1]
                break
        except asyncio.TimeoutError:
            return
        except Exception as e:
            logger.error(f"mcmod 会话等待异常: {e}")
            yield event.plain_result("会话处理出错，请重试。")
            return

        if target is None:
            return

        # 首次详情卡片
        async for msg in self._send_detail(event, target):
            yield msg

        # 询问是否继续搜索其他结果
        again: dict = {}
        try:
            async def again_chooser(controller: _SearchReply, ev: AstrMessageEvent):
                chain = ev.message_obj.message
                self_id = ev.message_obj.self_id
                at_bot = any(
                    getattr(c, "qq", None) == self_id
                    for c in chain
                    if type(c).__name__ == "At"
                )
                replied = any(type(c).__name__ == "Reply" for c in chain)
                initiator = ev.get_sender_id() == event.get_sender_id()
                if not (at_bot or replied or initiator):
                    return
                text = ev.message_str.strip()
                if text in ("继续", "是", "y", "Y"):
                    again["yes"] = True
                    controller.stop()
                elif text in ("不用", "否", "n", "N", "取消"):
                    again["yes"] = False
                    controller.stop()
                # 其他内容静默忽略，继续等待

            yield event.plain_result("要继续搜索其他结果吗？回复“继续”或“不用”。")
            await self._wait_for_search_reply(event, again_chooser, continuation=True)
        except asyncio.TimeoutError:
            return
        except Exception as e:
            logger.error(f"mcmod 继续询问异常: {e}")
            return

        if not again.get("yes"):
            return

        # 继续搜索：用同一个关键词重新回到列表选择
        page = 1
        target = None
        try:
            while True:
                start = (page - 1) * PER_PAGE
                end = min(start + PER_PAGE, len(results))
                page_results = results[start:end]

                items_html = []
                for j, r in enumerate(page_results, start + 1):
                    s = (
                        f'<div class="s">{html_lib.escape(r["snippet"][:60])}…</div>'
                        if r["snippet"]
                        else ""
                    )
                    cat_html = ""
                    if r.get("cat"):
                        ci = list(CATEGORY_NAMES.values()).index(r["cat"]) if r["cat"] in CATEGORY_NAMES.values() else 0
                        color = CATEGORY_COLORS[ci % len(CATEGORY_COLORS)]
                        cat_html = (
                            f'<span class="cat" style="background:{color}">'
                            f'{html_lib.escape(r["cat"])}</span>'
                        )
                    items_html.append(
                        ITEM_TMPL.format(
                            i=j, title=cat_html + html_lib.escape(r["title"]), snippet=s
                        )
                    )
                page_html = LIST_TMPL.format(
                    keyword=html_lib.escape(keyword),
                    count=len(results),
                    page=page,
                    pages=total_pages,
                    start=start + 1,
                    end=end,
                    items="\n".join(items_html),
                )
                async for msg in self._send_list(event, page_html, page_results, start):
                    yield msg
                yield event.plain_result(
                    f"MC百科搜索「{keyword}」共 {len(results)} 条结果 · 第 {page}/{total_pages} 页，"
                    f"回复数字选择（{start + 1}-{end}），“第X页”翻页，“取消”退出"
                )

                choice: dict = {}
                initiator_id = event.get_sender_id()

                async def chooser2(controller: _SearchReply, ev: AstrMessageEvent):
                    chain = ev.message_obj.message
                    self_id = ev.message_obj.self_id
                    at_bot = any(
                        getattr(c, "qq", None) == self_id
                        for c in chain
                        if type(c).__name__ == "At"
                    )
                    replied = any(type(c).__name__ == "Reply" for c in chain)
                    initiator = ev.get_sender_id() == initiator_id
                    if not (at_bot or replied or initiator):
                        return
                    text = ev.message_str.strip()
                    if text in ("取消", "c", "C"):
                        await ev.send(MessageEventResult().message("已取消选择。"))
                        controller.stop()
                        return
                    m = re.fullmatch(r"(?:第)?\s*(\d+)\s*页|[pP]\s*(\d+)", text)
                    if m:
                        p = int(m.group(1) or m.group(2))
                        if not (1 <= p <= total_pages):
                            await ev.send(
                                MessageEventResult().message(
                                    f"页码超出范围，请回复 1-{total_pages}。"
                                )
                            )
                            return
                        choice["page"] = p
                        controller.stop()
                        return
                    if not re.fullmatch(r"\d+", text):
                        await ev.send(
                            MessageEventResult().message(
                                f"请回复数字序号（{start + 1}-{end}）、“第X页”翻页或“取消”。"
                            )
                        )
                        return
                    idx = int(text)
                    if not (start + 1 <= idx <= end):
                        await ev.send(
                            MessageEventResult().message(
                                f"序号超出本页范围，本页为 {start + 1}-{end}。"
                            )
                        )
                        return
                    choice["index"] = idx
                    controller.stop()

                await self._wait_for_search_reply(event, chooser2)
                if "page" in choice:
                    page = choice["page"]
                    continue
                if "index" in choice:
                    target = results[choice["index"] - 1]
                break
        except asyncio.TimeoutError:
            return
        except Exception as e:
            logger.error(f"mcmod 会话等待异常: {e}")
            yield event.plain_result("会话处理出错，请重试。")
            return

        if target is None:
            return

        # 继续后的详情卡片（复用同一渲染逻辑）
        async for msg in self._send_detail(event, target):
            yield msg

    async def _send_list(
        self, event: AstrMessageEvent, page_html: str, results: list, start: int
    ):
        """优先发送列表图片；渲染服务不可用时保留文字列表和编号选择。"""
        try:
            list_img = await self._render_html(page_html)
        except Exception as error:
            _log_render_error(error)
            lines = ["图片暂时不可用，以下为文字搜索结果："]
            for index, result in enumerate(results, start + 1):
                lines.append(f"{index}. {result['title']}\n{result['url']}")
            yield event.plain_result("\n\n".join(lines))
            return

        try:
            yield event.image_result(list_img)
        finally:
            try:
                os.remove(list_img)
            except OSError:
                pass

    async def _send_detail(self, event: AstrMessageEvent, target: dict):
        """获取页面信息并渲染详情卡片发送"""
        yield event.plain_result(f"正在获取 {target['url']} 的页面信息，请稍候…")

        try:
            info = await self._detail_info(target["url"])
        except Exception as e:
            logger.error(f"mcmod 获取页面信息失败: {e}")
            yield event.plain_result("页面信息获取失败，这里是链接：\n" + target["url"])
            return

        # 精排信息栏：分隔行 + 标签胶囊 + 作者高亮
        meta_html = _build_meta_html(info["meta"], info["authors_html"])
        imgs_html = "".join(
            f'<div style="background-image:url(\'{html_lib.escape(src)}\')"></div>'
            for src in info["images"]
        )
        # 状态标签：mcmod 官方配色（status 前一块、source 圆角收尾）
        badges_html = ""
        for i, b in enumerate(info["badges"]):
            color = BADGE_COLORS.get(b["n"], "#adadad")
            last = i == len(info["badges"]) - 1
            cls = "badge" + (" src" if last else "")
            badges_html += (
                f'<span class="{cls}" style="background:{color}">'
                f'{html_lib.escape(b["text"])}</span>'
            )
        card_html = DETAIL_TMPL.format(
            title=html_lib.escape(info["title"]),
            url=html_lib.escape(target["url"]),
            meta=meta_html,
            content=info["content"],
            imgs=imgs_html,
            cover=html_lib.escape(info["cover"]),
            badges=badges_html,
        )
        try:
            card_img = await self._render_html(card_html)
        except Exception as error:
            _log_render_error(error)
            yield event.plain_result(
                f"详情图片暂时不可用：{info['title'] or target['title']}\n"
                f"页面链接：{target['url']}"
            )
            return

        try:
            yield event.chain_result(
                [
                    Comp.Image.fromFileSystem(card_img),
                    Comp.Plain(f"页面链接：{target['url']}"),
                ]
            )
        finally:
            try:
                os.remove(card_img)
            except OSError:
                pass

    async def _search(self, keyword: str, page: int = 1, filter: int = 0) -> tuple:
        """请求 mcmod 搜索页并解析结果列表，返回 (结果列表, mcmod 总页数)"""
        params = {"key": keyword}
        if page > 1:
            params["page"] = page
        if filter:
            params["filter"] = filter
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
            async with session.get(SEARCH_URL, params=params) as resp:
                resp.raise_for_status()
                html = await resp.text()

        soup = BeautifulSoup(html, "lxml")
        items = soup.select(".search-result-list .result-item")
        results = []
        for item in items:
            # .head 里可能有分类图标等无文字链接，取第一个有文字的
            a = next(
                (a for a in item.select(".head a") if a.get_text(strip=True)), None
            )
            if not a:
                continue
            href = a.get("href", "")
            if href.startswith("//"):
                href = "https:" + href
            title = a.get_text(" ", strip=True)
            body = item.select_one(".body")
            snippet = body.get_text(" ", strip=True) if body else ""
            # 分类标记：模组/资料页有 c_N 分类图标；物品/附魔等是 head 文本前缀 (xxx)
            cat = ""
            cat_cls = item.select_one(".class-category a")
            if cat_cls is not None:
                for c in cat_cls.get("class", []):
                    if c.startswith("c_"):
                        cat = CATEGORY_NAMES.get(c, "")
            else:
                head_text = item.select_one(".head")
                if head_text is not None:
                    m = re.search(r"\((物品/方块|附魔/魔咒|整合包|教程|用户|社群)\)", head_text.get_text())
                    if m:
                        cat = m.group(1)
            results.append(
                {
                    "title": title,
                    "url": href,
                    "snippet": snippet,
                    "cat": cat,
                }
            )
        # mcmod 总页数：从结果信息“共约 N 页”提取
        info_p = soup.select_one(".search-result .info")
        total_pages = 1
        if info_p:
            m = re.search(r"共约 (\d+) 页", info_p.get_text())
            if m:
                total_pages = int(m.group(1))
        return results, total_pages

    async def _render_html(self, page_html: str) -> str:
        """调用 AstrBot 官方 HTML 渲染，返回下载后的图片路径。"""
        # 把完整 HTML 作为数据传递，避免搜索内容中的 Jinja2 标记被二次解析。
        return await asyncio.wait_for(
            self.html_render(
                "{{ page_html | safe }}",
                {"page_html": page_html},
                return_url=False,
                options={
                    "type": "jpeg",
                    "quality": 90,
                    "full_page": True,
                    "viewport_width": 720,
                    "viewport_height": 600,
                },
            ),
            timeout=45,
        )

    async def _detail_info(self, url: str) -> dict:
        """抓取详情页信息：标题、meta 字段、正文、正文图片"""
        timeout = aiohttp.ClientTimeout(total=15)
        async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
            async with session.get(url) as resp:
                resp.raise_for_status()
                html = await resp.text()

        soup = BeautifulSoup(html, "lxml")

        # 状态标签（活跃/闭源等），带 mcmod 官方配色索引
        # 注意：必须先提取——后面从标题里剔除这些元素时会把它们从文档树中删除
        badges = []
        for el in soup.select(".class-status, .class-source"):
            t = _clean_text(el.get_text(strip=True))
            if not t:
                continue
            n = 0
            for c in el.get("class", []):
                if c.startswith("status-") or c.startswith("source-"):
                    try:
                        n = int(c.split("-")[1])
                    except (ValueError, IndexError):
                        n = 0
            badges.append({"text": t, "n": n})

        title_el = soup.select_one(".class-title")
        if title_el:
            # 去掉标题里的状态文字（活跃/闭源等），彩色标签会单独渲染
            for el in title_el.select(".class-status, .class-source"):
                el.extract()
            title = _clean_text(title_el.get_text(" ", strip=True))
        else:
            title = ""

        # meta 字段：从信息栏（.class-info-left）提取
        meta = []
        info_box = soup.select_one(".class-info-left")
        if info_box:
            seen = set()
            for row in info_box.select("li, tr, p"):
                t = _clean_text(row.get_text(" ", strip=True))
                if t and (": " in t or "：" in t) and t not in seen:
                    seen.add(t)
                    # 兼容半角/全角冒号
                    k, _, v = (
                        t.partition("：") if "：" in t else t.partition(": ")
                    )
                    meta.append((k, v))

        # 正文：.class-text 下最后一个 .class-menu-main（排除菜单目录、信息栏、标题栏）
        # 带格式：小节标题（common-text-title）加粗高亮，列表条目转为 • 圆点
        content = ""
        content_el = None
        text_el = soup.select_one(".class-text")
        if text_el:
            content_el = None
            for el in text_el.select(".class-menu-main"):
                t = _clean_text(el.get_text(" ", strip=True))
                if len(t) > len(_clean_text(content)):
                    content_el = el
            if content_el is not None:
                li = content_el.find("li", class_="common-text")
                if li is not None:
                    parts = []
                    budget = 620  # 字符预算：超过则省略，防止卡片过长
                    total = 0
                    truncated = False
                    for node in li.find_all(recursive=False):
                        if truncated:
                            break
                        if node.name == "p":
                            ts = node.select_one(".common-text-title")
                            if ts:
                                txt = _clean_text(ts.get_text(" ", strip=True))
                                parts.append(
                                    f'<div class="sec">{html_lib.escape(txt)}</div>'
                                )
                                total += len(txt)
                                continue
                            txt = _clean_text(node.get_text(" ", strip=True))
                            if not txt:
                                continue
                            if total + len(txt) > budget:
                                txt = txt[: max(0, budget - total)] + "…"
                                truncated = True
                            parts.append(f'<p class="para">{html_lib.escape(txt)}</p>')
                            total += len(txt)
                        elif node.name == "ul":
                            for it in node.find_all("li", recursive=False):
                                txt = _clean_text(it.get_text(" ", strip=True))
                                if not txt:
                                    continue
                                if total + len(txt) > budget:
                                    txt = txt[: max(0, budget - total)] + "…"
                                    truncated = True
                                parts.append(f'<p class="li">• {html_lib.escape(txt)}</p>')
                                total += len(txt)
                                if truncated:
                                    break
                        elif node.name == "table":
                            pass  # 画廊图片已在下方单独提取
                    content_html = "".join(parts)
                else:
                    content_html = html_lib.escape(
                        _clean_text(content_el.get_text(" ", strip=True))[:800]
                    )
            else:
                top = text_el.select_one(".class-text-top")
                if top:
                    top.extract()
                for sel in (".class-menu-main", ".class-info", ".class-title",
                            ".class-nav", ".common-nav"):
                    el = text_el.select_one(sel)
                    if el:
                        el.extract()
                content_html = html_lib.escape(
                    _clean_text(text_el.get_text(" ", strip=True))[:800]
                )
            if title and content_html.lstrip().startswith(html_lib.escape(title)):
                pass  # 标题已在头部显示，正文保留原样（小节结构不受影响）

        # 正文图片：识别模组展示图——遍历正文各小节，
        # 跳过非展示类小节（注意事项/奖项/声明/链接/赞助/鸣谢）和签名图
        images = []
        NON_SHOWCASE = ("注意", "声明", "奖项", "相关链接", "赞助", "鸣谢", "许可")
        if content_el is not None:
            li = content_el.find("li", class_="common-text")
            if li is not None:
                cur_sec = "(开头)"
                for node in li.find_all(recursive=False):
                    if node.name == "p":
                        # 小节标题 p：更新当前小节；普通 p 里的图也要收集
                        ts = node.select_one(".common-text-title")
                        if ts:
                            cur_sec = _clean_text(ts.get_text(" ", strip=True))
                            continue
                        if any(k in cur_sec for k in NON_SHOWCASE):
                            continue
                        for im in node.select("img"):
                            src = (
                                im.get("data-src")
                                or im.get("data-original")
                                or im.get("src")
                                or ""
                            )
                            if not src or "loading" in src or "/avatar/" in src:
                                continue
                            if src.startswith("//"):
                                src = "https:" + src
                            host = src.split("//")[-1].split("/")[0]
                            if "/sig/" in src or "sig." in host:
                                continue
                            if src.startswith("https://") and src not in images:
                                images.append(src)
                        continue
                    if any(k in cur_sec for k in NON_SHOWCASE):
                        continue
                    for im in node.select("img"):
                        src = (
                            im.get("data-src")
                            or im.get("data-original")
                            or im.get("src")
                            or ""
                        )
                        if not src or "loading" in src or "/avatar/" in src:
                            continue
                        if src.startswith("//"):
                            src = "https:" + src
                        host = src.split("//")[-1].split("/")[0]
                        if "/sig/" in src or "sig." in host:
                            continue
                        if src.startswith("https://") and src not in images:
                            images.append(src)
        images = images[:4]

        # 模组封面图（作为头像）
        cover = ""
        cov = soup.select_one(
            'img[src*="/cover/"], img[data-src*="/cover/"]'
        )
        if cov:
            cover = cov.get("data-src") or cov.get("src") or ""
            if cover.startswith("//"):
                cover = "https:" + cover

        # 作者成员卡片（需在信息栏 DOM 被修改前解析）
        authors_html = _build_author_cards(soup)

        return {
            "title": title,
            "meta": meta,
            "content": content_html,
            "images": images,
            "cover": cover,
            "badges": badges,
            "authors_html": authors_html,
        }

