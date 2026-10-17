# astrbot_plugin_mcmod_search

一个 AstrBot 插件：搜索 [MC百科](https://www.mcmod.cn)，通过 AstrBot 官方 HTML 渲染接口生成编号列表，回复数字后发送对应的详情卡片和页面链接。

灵感来源：MC百科官方搜索页（https://search.mcmod.cn）。

## 使用方法

```text
MC百科搜索 铁锈
```

1. 机器人发送编号搜索结果，每页 10 条。
2. 回复数字选择条目；回复“第2页”等指令翻页，也可回复“只看模组/整合包/教程/资料”过滤、“更多”加载更多结果。
3. 机器人发送详情卡片和页面链接，可回复“继续”再次选择其他结果。
4. 回复“取消”退出选择；60 秒不操作会静默结束。

搜索只识别同一聊天、同一账号下搜索发起者的有效操作。普通闲聊、其他命令、其他成员的消息继续正常处理；“继续／不用”只控制这次搜索。等待期间不接管 AstrBot 全局会话，不终止消息向其他插件传播。再次搜索会替换自己上一次的等待，重载时会清理等待状态。

> 也可以使用 `/mcmod 关键词` 触发同样的功能。

## 渲染方式

- 搜索列表和详情卡片统一调用 AstrBot 官方的 `self.html_render()`，使用 AstrBot 配置的文转图服务。
- 插件无需额外安装 Playwright、Chromium 或浏览器系统依赖。
- 保留卡片的 HTML/CSS 样式，包括分类标签、作者头像、模组信息和展示图片。
- 详情卡片根据百科页面内容生成，附上原页面链接。
- 渲染调用最多等待 45 秒；服务不可用或超时时自动发送文字结果和页面链接，仍可回复编号选择。

接口说明见 [AstrBot 官方文转图文档](https://docs.astrbot.app/dev/star/guides/html-to-pic.html)。

## 安装与配置

当前版本：**v1.0.4**。使用官方 HTML 渲染，保留搜索等待隔离修复。

在 AstrBot WebUI 的插件页面，使用以下仓库地址安装：

```text
https://github.com/AlancoSage/astrbot_plugin_mcmod_search
```

也可以前往 [Releases 下载最新 ZIP 安装包](https://github.com/AlancoSage/astrbot_plugin_mcmod_search/releases/latest)，在 WebUI 上传安装，或将源码放入 AstrBot 的 `data/plugins/` 下。AstrBot 会自动按 `requirements.txt` 安装网络请求和 HTML 解析依赖。

无需设置插件专用的渲染地址。默认使用 AstrBot 的官方远程文转图服务；若 AstrBot 配置了自建服务，则沿用该配置。渲染需要 AstrBot 能访问该服务，服务也需要能加载卡片里的远程图片。自建服务说明见 [AstrBot 官方文档](https://docs.astrbot.app/others/self-host-t2i.html)。

更新后重载插件即可，无需执行浏览器下载命令。

## 验证

离线回归测试（无需安装 AstrBot 或连接渲染服务）：

```bash
python -m unittest test_render_fallback test_session_isolation -v
```

安装依赖后，可手动检查百科搜索和生成列表 HTML：

```bash
python test_core.py 铁锈
```

此命令会请求 MC百科并生成 `test_list.html`。实际图片效果可在 AstrBot 中发送搜索指令检查。

## 致谢

- [AstrBot](https://github.com/AstrBotDevs/AstrBot)
- [MC百科](https://www.mcmod.cn)（搜索数据来源，内容遵循其 BY-NC-SA 3.0 协议）

## 构建安装包

在源码仓库中，每次修改后先同步更新 `main.py` 和 `metadata.yaml` 的版本号，完成验证，再运行：

```bash
python scripts/build_release.py
```

安装包生成在插件目录，名称包含版本号；已有同版本安装包不会被覆盖。ZIP 只包含运行所需的源码、依赖说明和使用文档；测试和构建脚本保留在源码仓库中。
