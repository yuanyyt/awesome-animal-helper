# Awesome Animal Helper

从 Excel 读取动物名称，优先查询中文 Wikipedia，并结合 Wikidata
结构化属性与可选的 OpenAI 兼容 LLM，生成中文动物资料 CSV。

## 动物园导览应用

后端从 `src/data/animals.csv` 和 `src/data/animal_sites.xlsx` 读取动物与场馆数据，
提供统一查询接口：

```bash
uv run uvicorn src.backend.main:app --reload
```

接口文档位于 <http://127.0.0.1:8000/docs>，动物查询接口为
`GET /api/animals`，支持 `q`、`site` 和 `name` 参数。

另开一个终端启动 Vue 前端：

```bash
npm install --prefix src/frontend
npm run dev --prefix src/frontend
```

打开 <http://127.0.0.1:5173>。开发服务器会将 `/api`（包括 WebSocket）和
`/_AMapService` 请求代理到 FastAPI。

园区地图使用高德 Web Service 获取场馆点位，并使用 JS API 提供拖拽和缩放能力。
在项目根目录 `.env` 中配置：

```dotenv
AMAP_WEBSERVICE_KEY=服务端Web服务Key
JS_API_KEY=浏览器端JS API Key
SECURITY_KEY=JS API安全密钥
```

`SECURITY_KEY` 仅由 FastAPI 的 `/_AMapService` 安全代理使用，不会返回给浏览器；
交互地图加载失败时会自动回退到 `/api/map/image` 提供的静态地图。

### 智能路线规划

第二页采用连续对话流，地图、场馆动物图册和路线方案都会作为消息出现在同一会话中。
地图标记支持多选场馆，也可以点击“在地图上设置起点”后选择任意起点。后端先用
确定性规则识别路线规划、动物知识、混合或未知意图，并将消息中的动物名称映射到
本地场馆；地图已选场馆与消息提到的必看动物会在进入 LLM 前合并。Agent 会在缺少
时间、体力等必要信息时通过 HITL 暂停并等待前端输入，随后返回轻松、均衡、尽兴
三种高德步行方案。

LLM 配置同样放在项目根目录 `.env`：

```dotenv
DASHSCOPE_API_KEY=OpenAI兼容服务密钥
LLM_BASE_URL=https://example.com/compatible-mode/v1
LLM_MODEL=qwen3.6-flash
```

后端固定传递 `enable_thinking=false`。会话保存在忽略版本控制的
`src/data/runtime/guide_agent.db`；距离、步行时间、指令和路线坐标来自高德，
参观时间、体力上限和卡路里会在界面中标注为估算值。

相关接口：

- `POST /api/guide/chat`：发送新消息和当前地图选择。
- `POST /api/guide/chat/{run_id}/continue`：提交 HITL 字段并恢复会话。
- `WS /api/guide/voice`：传输实时 PCM 音频、转写及结构化路线结果。

### 实时语音导览

聊天框中的麦克风使用 Qwen-Audio 实时模型完成语音识别和语音合成，Agno 是文字与
语音请求共用的唯一业务 Agent。点击一次开始录音，再点击一次提交；用户重新开始
录音时会立即停止上一段播报。浏览器只连接 FastAPI，百炼 API Key 不会下发到前端。
输入为 16 kHz、16-bit、单声道 PCM，回复按 24 kHz PCM 流式播放。

默认复用 `LLM_BASE_URL` 的业务空间域名，也可以单独配置：

```dotenv
AUDIO_REALTIME_BASE_URL=wss://WorkspaceId.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime
AUDIO_REALTIME_MODEL=qwen-audio-3.0-realtime-flash
AUDIO_REALTIME_VOICE=longanqian
```

语音转写会携带浏览器当前的 Agno `session_id`，因此语音和文字共享同一段会话历史、
意图识别、动物资料查询和高德路线规划能力。Qwen-Audio 不直接调用业务工具，只朗读
Agno 的最终答复。麦克风只能在 `localhost` 或 HTTPS 安全上下文使用；拒绝权限时仍可
继续文字导览。动物知识目前来自本地 CSV，后续可通过 `AnimalKnowledgeProvider` 接口
替换为更完整的知识解说服务。

## 爬虫运行

仅使用 Wikipedia 和 Wikidata：

```bash
uv run python -m src.crawler --no-llm
```

启用 LLM 清洗：

```bash
# 也可以将这三个变量写入项目根目录 .env
export LLM_API_KEY="..."
export LLM_MODEL="qwen3.6-flash"
export LLM_BASE_URL="https://ws-tws8oqjtpbek3bko.cn-beijing.maas.aliyuncs.com/compatible-mode/v1"
uv run python -m src.crawler
```

LLM 请求固定传递 `enable_thinking=false`，不会生成或打印思考过程。

默认读取 `src/data/animal_sites.xlsx`，输出 `src/data/animals.csv`。

可选代理池文件每行一个 HTTP(S) 代理地址：

```text
http://user:password@proxy-a.example:8080
http://proxy-b.example:8080
```

```bash
uv run python -m src.crawler --proxy-file proxies.txt --delay 2 --jitter 1
```

没有代理文件时，请求仍使用本机唯一出口 IP；轮换 User-Agent 不能替代代理池。
遇到 `429` 时程序会遵循 `Retry-After` 并指数退避。
