# BiliNote-Go Video Worker

面向其它系统集成的视频知识摄取 Worker。核心职责是 URL 安全校验、字幕/媒体获取、远程 ASR 和临时工作目录管理。

## Knowledge ingestion pipeline

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
              Extraction Done
```

`POST /api/v1/extractions` **不会调用 Summary LLM**。这是 KnowledgeHub/WeKnora 视频知识摄取应使用的接口。

旧的 `POST /api/v1/summaries` 暂时保留用于兼容已有独立视频总结调用，它仍执行 Map/Reduce Summary。

`/tmp/bilinote/<task>-*` 在成功、失败或取消后删除。

## 依赖

- PostgreSQL（Worker queue/result state）
- OpenAI-compatible `/v1/audio/transcriptions` ASR API
- yt-dlp standalone executable
- FFmpeg
- Summary API 兼容模式额外需要 OpenAI-compatible `/v1/chat/completions` LLM API

## API

### 创建 transcript extraction

```bash
curl -X POST http://127.0.0.1:8080/api/v1/extractions \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.bilibili.com/video/BV..."}'
```

返回 `202`：

```json
{
  "id": "...",
  "kind": "extraction",
  "status": "queued"
}
```

### 兼容的 summary task

```bash
curl -X POST http://127.0.0.1:8080/api/v1/summaries \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=..."}'
```

### 查询任务

```bash
curl http://127.0.0.1:8080/api/v1/tasks/<task_id>
```

Extraction 成功结果包含：

```json
{
  "id": "...",
  "kind": "extraction",
  "status": "succeeded",
  "video": {},
  "transcript": {
    "language": "zh-CN",
    "source": "subtitle",
    "provider": "bilibili_player_api",
    "segments": []
  }
}
```

Extraction 无论 `summary.keep_transcript` 如何配置都会保存 transcript，因为 transcript 是该任务的正式输出。

### 取消

```bash
curl -X POST http://127.0.0.1:8080/api/v1/tasks/<task_id>/cancel
```

## 字幕策略

默认 `video.prefer_subtitle=true`：

1. 尝试字幕（人工 + 自动字幕）。
2. 没有可用字幕才下载最佳音频。
3. FFmpeg 标准化为 16 kHz mono WAV。
4. 调用远程 ASR。
5. Transcript 作为 extraction 的最终业务结果。

Bilibili 使用专用链路：

1. `x/web-interface/view` 获取原生 metadata、CID 和分P信息。
2. `x/player/wbi/v2` 获取字幕轨道，优先人工中文字幕，其次 AI 中文字幕，最后其它字幕。
3. HTTP `412/429` 或 API code `-412/-352` 会识别为 Bilibili 风控；原生链路失败或无字幕时回退 yt-dlp。
4. `b23.tv` 重定向最多跟随 5 跳，并对目标域名和解析地址重新执行安全校验。
5. Bilibili 字幕正文统一使用 `Transcript`；原生字幕 `provider=bilibili_player_api`，yt-dlp 字幕 `provider=yt-dlp`。

## PostgreSQL Queue

Worker 使用 `FOR UPDATE SKIP LOCKED` 抢任务并维护 lease。任务增加：

```text
kind = extraction | summary
```

状态仍为：

```text
queued
running
succeeded
failed
cancelled
```

同一 PostgreSQL 可以支撑多个 Video Worker 实例。

## 目录

```text
cmd/server                 启动与装配
internal/api               REST API
internal/video             source/registry/URL security
internal/ytdlp             yt-dlp subprocess wrapper
internal/media             ffmpeg wrapper
internal/asr               OpenAI-compatible ASR
internal/llm               仅 summary 兼容模式
internal/summary           仅 summary 兼容模式
internal/pipeline          subtitle/ASR pipeline + task kind routing
internal/task              task model
internal/worker            PostgreSQL worker pool
internal/repository        PostgreSQL repository/lease queue
internal/workspace         临时目录清理
migrations                 PostgreSQL schema
```
