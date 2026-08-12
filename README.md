---
domain: multi-modal
tags:
- 动物园导览
- 智能体
- 地图导航
datasets:
  evaluation:
  test:
  train:
models:
license: Apache License 2.0
---

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

后端按职责分层：`api` 负责 HTTP/WebSocket 与依赖生命周期，`agents` 负责 Agno
编排，`services` 放置确定性业务逻辑，`repositories` 读取本地数据，`integrations`
封装高德和实时音频等外部服务，`domain` 保存跨层共享的数据模型。启动入口保持为
`src.backend.main:app`。

另开一个终端启动 Vue 前端：

```bash
npm install --prefix src/frontend
npm run dev --prefix src/frontend
```

打开 <http://127.0.0.1:5173>。开发服务器会将 `/api`（包括 WebSocket）和
`/_AMapService` 请求代理到 FastAPI。

园区地图使用高德 Web Service 获取场馆点位，并使用 JS API 提供拖拽和缩放能力。
地图同时提供卫生间、餐饮、饮水、游客服务、交通等分类设施图层，以及北门站、
猩猩馆站、中心广场站、东门站、猴山站组成的单向观光车环线。设施优先匹配高德
POI，未匹配项由园区导览图补全；公开接口不返回内部点位来源字段。
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
地图标记支持多选场馆，也可以点击“在地图上设置起点”后选择任意起点。路线、动物、
服务按钮用于表达回答偏好，Wiki、RAG、路径规划和园区服务工具始终对 Agent 可用，
由 Agent 根据完整问题自主选择；一个问题可以同时查询动物故事并规划路线。后端只将
消息中的动物和场馆名称标准化，不使用关键词规则替 Agent 判断意图。Agent 会在缺少
时间、体力等必要信息时通过 HITL 暂停并等待前端输入，随后返回一条匹配偏好的路线。
用户在问题中明确提出的园内起点优先于地图起点；工具会从场馆、入口、设施和观光车站
解析其坐标，无法解析时不会静默使用默认起点。
路线规划会额外询问“纯步行”或“可乘观光车”。观光车方案使用实时上海时间判断
当日运营状态，车程按 12 km/h、每次乘车平均候车 5 分钟估算，并在界面中与红色
步行路线分色展示。

LLM 配置同样放在项目根目录 `.env`：

```dotenv
DASHSCOPE_API_KEY=OpenAI兼容服务密钥
LLM_BASE_URL=https://example.com/compatible-mode/v1
LLM_MODEL=qwen3.6-flash
# 可选；导览默认关闭思考模式，需要时可显式开启
LLM_ENABLE_THINKING=false
```

后端默认关闭模型思考模式以缩短首字延迟；设置 `LLM_ENABLE_THINKING=true` 可显式开启。
模型接口会启用并行工具调用，Agno 将同一轮中互不依赖的 Wiki、知识检索、设施查询
和路线规划并发执行；存在结果依赖的工具仍会按顺序调用。
对话仅保留最近三轮文本，不重复向模型发送历史工具结果和路线坐标，以控制上下文长度。
会话保存在忽略版本控制的
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
继续文字导览。动物知识由本地持久化知识库提供，语音与文字请求共用相同的检索工具。

### 动物知识检索

动物讲解使用独立的 `src/data/runtime/knowledge.db`，将 CSV 结构化资料、场馆关系、
`intro.md` 讲解段落和 `sqlite-vec` 向量索引持久化到同一个 SQLite 数据库。首次启动
会自动构建；数据库就绪后不会再次读取源文件或校验版本。需要人工更新数据时执行：

```bash
uv run python -m src.backend.knowledge rebuild
```

默认使用百炼 `text-embedding-v4` 的 1024 维向量，并复用
`DASHSCOPE_API_KEY` 与 `LLM_BASE_URL`。也可单独设置：

```dotenv
EMBEDDING_BASE_URL=https://example.com/compatible-mode/v1
EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024
```

Agent 使用两个独立的动物知识工具：

- `search_animal_knowledge` 负责 CSV 通用物种资料和 `intro.md` 场馆讲解的精确匹配与语义召回。只有需要上下文时，才使用本轮返回的 Chunk ID 调用 `get_neighboring_knowledge_chunks`，且不会跨越讲解章节边界。
- `search_animal_wiki_stories` 负责昵称、具体个体、饲养训练和公众号园区故事，每条结果保留文章标题、链接和原文证据。

通用物种知识与园区趣事可在同一轮同时检索。Wiki 中特定个体的经历不会被当作整个物种的普遍习性。
场馆科普讲解和节假日行为训练展示时刻由 `get_zoo_education_schedule`
提供，支持按场馆查询或返回完整时刻表。

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
## 从 ModelScope 克隆

```bash
git clone https://www.modelscope.cn/studios/yuanyyt/awesome_animal_helper.git
```

## Docker 与 ModelScope Studio

ModelScope Studio 使用仓库根目录的 `Dockerfile` 构建，容器统一在
`0.0.0.0:7860` 提供 Vue 页面、FastAPI 接口和 WebSocket。运行时数据库写入
`/mnt/workspace/awesome-animal-helper/runtime`，在 Studio 后台将 API Key 配置为秘钥，
不要通过 `Dockerfile`、构建参数或 `.env` 写入镜像。

当前受限开发环境没有 root 权限，无法安装 Docker daemon。Ubuntu 主机可执行：

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh
sudo usermod -aG docker "$USER"
```

重新登录后验证：

```bash
docker version
docker compose version
```

如果 Docker 官方软件源无法访问，可以改用镜像站安装：

```bash
curl -fsSL https://get.docker.com -o get-docker.sh
sudo DOWNLOAD_URL=https://mirrors.aliyun.com/docker-ce sh get-docker.sh
sudo usermod -aG docker "$USER"
```

正常网络下构建和运行：

```bash
docker build -t awesome-animal-helper:local .
docker run --rm -p 7860:7860 --env-file .env \
  -v awesome-animal-runtime:/mnt/workspace/awesome-animal-helper/runtime \
  awesome-animal-helper:local
```

拉取基础镜像或依赖较慢时，可在构建时切换镜像站，不必修改 `Dockerfile`：

```bash
docker build -t awesome-animal-helper:local \
  --build-arg NODE_IMAGE=docker.m.daocloud.io/library/node:22-bookworm-slim \
  --build-arg PYTHON_IMAGE=docker.m.daocloud.io/library/python:3.12-slim-bookworm \
  --build-arg NPM_REGISTRY=https://registry.npmmirror.com \
  --build-arg PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple \
  .
```

镜像采用多阶段构建：源代码只进入临时构建阶段，最终镜像仅复制 Vue 构建产物、
后端字节码、运行依赖和必需数据，不包含项目 Python 源文件、Git 历史、公众号原始
抓取结果或 `.env`。这能阻止从最终镜像层直接还原源码，但字节码仍可能被专业工具
逆向；浏览器端 JavaScript 和接口返回的公开知识内容也无法对访问者保密。
