# 动物趣事 Wiki 数据管线

Wiki 构建器不直接访问微信公众号，只读取已经整理好的 Markdown，并把正文交给 LLM 提取动物趣事。

## 1. 抓取微信公众号文章

使用 [bzd6661/wechat-article-for-ai](https://github.com/bzd6661/wechat-article-for-ai) 将公众号文章转换为带 YAML front matter 的 Markdown：

```bash
git clone https://github.com/bzd6661/wechat-article-for-ai.git
cd wechat-article-for-ai
pip install -r requirements.txt
python main.py -f /path/to/urls.txt \
  -o /path/to/awesome-animal-helper/src/data/wx_info/output \
  -v
```

如遇微信验证码，可增加 `--no-headless`，在打开的浏览器中手动完成验证。输出应保持该工具的默认结构：

```text
src/data/wx_info/output/
└── 文章标题/
    ├── 文章标题.md
    └── images/
```

每篇 Markdown 必须包含 `title`、`author`、`date`、`source` 四个 front matter 字段。请等待公众号抓取全部完成后再构建 Wiki。

## 2. 构建 Wiki

在本项目根目录配置 `.env`：

```dotenv
LLM_MODEL=模型名
LLM_BASE_URL=OpenAI兼容接口地址
DASHSCOPE_API_KEY=接口密钥
```

也可以使用 `LLM_API_KEY`，其优先级高于 `DASHSCOPE_API_KEY`。执行全量构建：

```bash
uv run python -m src.crawler.wechat_wiki build \
  --articles src/data/wx_info/output \
  --wiki src/data/wx_info/wiki
```

构建器会忽略文章图片和微信播放器噪声，并生成：

```text
wiki/
├── index.md
├── manifest.json
├── report.json
└── 场馆/学名/动物名.md
```

`manifest.json` 供后端读取，Markdown 用于人工检查，`report.json` 记录文章抽取成功数、失败数、未归档内容和待确认信息。

## 3. 单文章真实 LLM 验证

真实接口测试固定读取《水獭宝宝能有什么坏心思？》，不会重建正式 Wiki：

```bash
RUN_LLM_INTEGRATION=1 uv run pytest tests/test_wechat_wiki_integration.py -q
```
