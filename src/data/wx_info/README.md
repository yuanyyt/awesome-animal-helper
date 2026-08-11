# 动物趣事 Wiki 数据管线

这套管线只把真实抓取到的微信正文交给 LLM。验证码页、环境异常页和正文过短的页面会记录为失败，不会根据标题生成内容。

首次使用先安装浏览器并完成人工验证：

```bash
uv sync
uv run playwright install chromium
uv run python -m src.crawler.wechat_wiki login
```

浏览器打开后完成微信验证，确认能看到文章正文，再回到终端按 Enter。登录资料保存在已忽略的 `.browser-profile/`，不要复制或提交该目录。

批量抓取正文并生成 Wiki：

```bash
uv run python -m src.crawler.wechat_wiki run
```

也可以分步运行：

```bash
uv run python -m src.crawler.wechat_wiki crawl
uv run python -m src.crawler.wechat_wiki build
```

LLM 从项目根目录 `.env` 读取 `LLM_MODEL`、`LLM_BASE_URL`，密钥优先使用 `LLM_API_KEY`，未配置时使用 `DASHSCOPE_API_KEY`。抓取在检测到微信验证页时立即停止；重新执行 `login` 后再次运行即可从成功记录之后继续。

生成结果位于 `wiki/`：

```text
wiki/
├── index.md
├── manifest.json
├── report.json
└── 场馆/学名/动物名.md
```

`manifest.json` 是后端读取索引，Markdown 是可人工检查的内容主数据，`report.json` 记录抓取、抽取、未归档和待确认情况。
