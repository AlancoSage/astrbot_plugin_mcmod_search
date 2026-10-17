# 手动网络检查：搜索解析、bbcode 清理和列表 HTML；图片渲染见 test_render_fallback.py。
import asyncio
import html as html_lib
import re
import sys
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup

SEARCH_URL = "https://search.mcmod.cn/s"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

BBCODE_RE = re.compile(
    r"\[(?:/?h\d=[^\]]*|/?h\d|ban:[^\]]*|/[^\]]*|[a-z]{1,10}(?:=[^\]]*)?)\]",
    re.IGNORECASE,
)


def _clean_text(text: str) -> str:
    text = BBCODE_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


LIST_TMPL = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>
  body {{ margin: 0; background: #f5f6f8; font-family: "Microsoft YaHei", sans-serif; }}
  .card {{ width: 640px; background: #fff; border-radius: 12px; padding: 20px 24px;
          box-shadow: 0 2px 8px rgba(0,0,0,.08); }}
  h1 {{ font-size: 20px; color: #2c3e50; margin: 0 0 4px; }}
  .sub {{ font-size: 12px; color: #888; margin-bottom: 14px; }}
  .item {{ padding: 10px 12px; border-bottom: 1px solid #f0f0f0; display: flex; gap: 10px; }}
  .item:last-child {{ border-bottom: none; }}
  .num {{ flex: none; width: 26px; height: 26px; border-radius: 50%; background: #4a7dbd;
         color: #fff; font-size: 14px; display: flex; align-items: center;
         justify-content: center; margin-top: 2px; }}
  .t {{ font-size: 15px; color: #2c3e50; font-weight: 600; line-height: 1.4; }}
  .s {{ font-size: 12px; color: #999; line-height: 1.5; margin-top: 2px; }}
  .foot {{ margin-top: 12px; font-size: 12px; color: #aaa; text-align: center; }}
</style></head><body>
<div class="card">
  <h1>MC百科搜索「{keyword}」</h1>
  <div class="sub">共 {count} 条结果 · 回复数字选择（1-{count}），回复“取消”退出</div>
  {items}
  <div class="foot">Powered by mcmod.cn · astrbot_plugin_mcmod_search</div>
</div>
</body></html>"""

ITEM_TMPL = """<div class="item">
  <div class="num">{i}</div>
  <div><div class="t">{title}</div>{snippet}</div>
</div>"""


async def _search(keyword: str) -> list:
    params = {"key": keyword}
    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout, headers=HEADERS) as session:
        async with session.get(SEARCH_URL, params=params) as resp:
            resp.raise_for_status()
            html = await resp.text()

    soup = BeautifulSoup(html, "lxml")
    items = soup.select(".search-result-list .result-item")
    results = []
    for item in items:
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
        results.append({"title": title, "url": href, "snippet": snippet})
    return results


async def main():
    kw = sys.argv[1] if len(sys.argv) > 1 else "铁锈"
    results = await _search(kw)
    print(f"[1] 搜索「{kw}」共 {len(results)} 条结果")
    assert results, "搜索结果为空！"

    for r in results:
        r["title"] = _clean_text(r["title"])
        r["snippet"] = _clean_text(r["snippet"])
    print("[2] 清理后前 3 条:")
    for i, r in enumerate(results[:3], 1):
        print(f"  {i}. {r['title']} | {r['snippet'][:50]}")
    assert "[h1" not in results[0]["title"] and "[ban" not in results[0]["title"], "bbcode 未清理干净"

    items_html = []
    for i, r in enumerate(results, 1):
        s = (
            f'<div class="s">{html_lib.escape(r["snippet"][:60])}…</div>'
            if r["snippet"]
            else ""
        )
        items_html.append(ITEM_TMPL.format(i=i, title=html_lib.escape(r["title"]), snippet=s))
    page_html = LIST_TMPL.format(
        keyword=html_lib.escape(kw), count=len(results), items="\n".join(items_html)
    )
    path = Path("test_list.html")
    path.write_text(page_html, encoding="utf-8")
    print(f"[3] 列表 HTML 已生成: {path.resolve()}")


if __name__ == "__main__":
    asyncio.run(main())
