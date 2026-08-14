# BiliNote-Go

面向其它系统集成的视频总结服务。参考精简版 BiliNote 的业务链路，Go 版本只负责 URL 安全校验、任务编排、字幕/媒体获取、远程 ASR、远程 LLM 总结和 PostgreSQL 结果保存。

## Pipeline

```text
Video URL
   ↓
Source Registry
   ├─ BilibiliSource
   │    ├─ native view API metadata
   │    ├─ native player subtitle API
   │    └─ 412/429/-412/-352/no subtitle → yt-dlp fallback
   └─ GenericYTDLPSource
   ↓
Metadata
   ↓
Subtitle first
   ├─ success ───────────────┐
   └─ no subtitle            │
          ↓                  │
       yt-dlp                │
          ↓                  │
       ffmpeg                │
          ↓                  │
    Remote ASR API           │
          └──────────┬───────┘
                     ↓
                 Transcript
                     ↓
              LLM Map/Reduce
                     ↓
                  Summary
                     ↓
                PostgreSQL

/tmp/bilinote/<task>-* is deleted after success/failure/cancellation.
```

## 不包含

- 本地 Whisper/Faster-Whisper/CUDA/CTranslate2
- MinIO/S3
- Redis/RabbitMQ/Kafka
- Python runtime（运行镜像使用 yt-dlp standalone executable）
- 视频/音频长期存储

## 依赖

- PostgreSQL
- OpenAI-compatible `/v1/audio/transcriptions` ASR API
- OpenAI-compatible `/v1/chat/completions` LLM API
- yt-dlp standalone executable
- FFmpeg

## Docker 启动

```bash
cp .env.example .env
# 修改 ASR_BASE_URL / ASR_MODEL / LLM_BASE_URL / LLM_MODEL / API KEY
docker compose up --build -d
```

默认只绑定本机：

```text
http://127.0.0.1:8080
```

检查：

```bash
curl http://127.0.0.1:8080/healthz
curl http://127.0.0.1:8080/readyz
```

## API

创建任务：

```bash
curl -X POST http://127.0.0.1:8080/api/v1/summaries \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=..."}'
```

查询：

```bash
curl http://127.0.0.1:8080/api/v1/tasks/<task_id>
```

取消：

```bash
curl -X POST http://127.0.0.1:8080/api/v1/tasks/<task_id>/cancel
```

## 字幕策略

默认 `video.prefer_subtitle=true`：

1. 尝试字幕（人工 + 自动字幕）。
2. 没有可用字幕才下载最佳音频。
3. FFmpeg 标准化为 16 kHz mono WAV。
4. 调用远程 ASR。
5. Transcript 统一进入 LLM Map/Reduce。

Bilibili 使用专用链路：

1. `x/web-interface/view` 获取原生 metadata、CID 和分P信息。
2. `x/player/wbi/v2` 获取字幕轨道，优先人工中文字幕，其次 AI 中文字幕，最后其它字幕。
3. HTTP `412/429` 或 API code `-412/-352` 会识别为 Bilibili 风控；原生链路失败或无字幕时回退 yt-dlp。
4. `b23.tv` 重定向最多跟随 5 跳，并对目标域名和解析地址重新执行安全校验。
5. Bilibili 字幕正文仍只保留统一 `Transcript`，`provider=bilibili_player_api`；yt-dlp 字幕为 `provider=yt-dlp`。

Bilibili Cookie 通过环境变量传入：

```yaml
bilibili:
  cookie: "${BILIBILI_COOKIE}"
  request_timeout: 20s
  retries: 2
  retry_backoff: 500ms
```

Cookie 为空也可尝试匿名 API；如果触发风控，会自动进入 yt-dlp fallback。

## 临时数据

容器使用 tmpfs：

```yaml
tmpfs:
  - /tmp/bilinote:size=4g,mode=1777
```

每个任务使用独立随机工作目录，Pipeline `defer os.RemoveAll()` 清理。服务启动及每小时额外清理超过 `workspace.stale_after` 的孤儿目录，用于覆盖 OOM/SIGKILL 等无法执行 defer 的情况。

## PostgreSQL Queue

不引入 Redis。Worker 使用 `FOR UPDATE SKIP LOCKED` 抢任务，并维护 lease：

- `queued`
- `running`
- `succeeded`
- `failed`
- `cancelled`

同一 PostgreSQL 可以支撑多个 BiliNote-Go 实例。

## 目录

```text
cmd/server                 启动与装配
internal/api               REST API
internal/video             source/registry/URL security
internal/ytdlp             yt-dlp subprocess wrapper
internal/media             ffmpeg wrapper
internal/asr               OpenAI-compatible ASR
internal/llm               OpenAI-compatible LLM
internal/summary           Map/Reduce
internal/pipeline          核心业务 Pipeline
internal/task              task model
internal/worker            PostgreSQL worker pool
internal/repository        PostgreSQL repository/lease queue
internal/workspace         临时目录清理
migrations                 PostgreSQL schema
prompts                    LLM prompts
```

## 当前 MVP 边界

已实现 Go 主链路、Docker、Bilibili 原生 metadata/player 字幕 API 与风控 fallback。任务错误分类、严格 subprocess 进程组清理、鉴权与真实站点端到端集成测试仍建议作为后续加固项。
