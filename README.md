# BiliNote Summary Refactor

一个从 BiliNote 视频总结场景裁剪出来的轻量服务：**多视频源 URL → 字幕优先 → 音频/ASR fallback → LLM 总结**。

## 当前范围

- Bilibili：专用 metadata / player 字幕 API 优先，412/风控或无字幕时回退 yt-dlp
- YouTube：yt-dlp metadata + 人工/自动字幕优先
- 抖音、快手、TikTok：使用 Generic yt-dlp source；其它站点需显式加入 `video.allowed_domains` 后才能进入同一 Pipeline
- 无字幕：yt-dlp 下载音频 → FFmpeg 标准化 → Faster-Whisper
- NVIDIA GPU：CUDA + CTranslate2/Faster-Whisper；CPU 也可配置
- OpenAI-compatible LLM
- 长字幕 Map/Reduce 总结
- SQLite 任务历史
- 极简 Web：提交、任务、结果、配置
- YAML 配置 + 环境变量展开
- 单 Docker GPU 服务

> 除 Bilibili 外仍由 `GenericYtDlpSource` 复用 yt-dlp extractor，但为降低 SSRF 风险，入口默认启用域名 allowlist。默认包含 Bilibili、YouTube、抖音、快手、TikTok；其它站点只需加入 `video.allowed_domains`，无需新增 Python adapter。

## Pipeline

```text
Video URL
   │
   ├─ Bilibili ──> BilibiliSource ──> 原生字幕 API
   │                                      │
   │                                      └─失败/无字幕──┐
   │                                                    │
   └─ Other URL ─> GenericYtDlpSource ─> yt-dlp 字幕 ───┤
                                                        │
                                                无可用字幕
                                                        │
                                                        ▼
                                                   yt-dlp audio
                                                        │
                                                     FFmpeg
                                                        │
                                                Faster-Whisper
                                                        │
                                                        ▼
                                                   Transcript
                                                        │
                                                   Chunk/Reduce
                                                        │
                                                        ▼
                                              OpenAI-compatible LLM
                                                        │
                                                        ▼
                                            JSON + Markdown + SQLite
```

## 运行级加固

当前版本额外包含：

- Bilibili HTTP 412/429、API `-412/-352` 显式识别与重试
- yt-dlp metadata fallback
- yt-dlp 下载/分片重试与指数退避
- yt-dlp/FFmpeg 子进程超时、取消时终止进程组
- 每任务 `task.log`
- 命令行、stdout/stderr、任务错误中的 URL token/signature、Authorization、B站 Cookie 字段脱敏，并限制单任务日志大小
- 任务 soft timeout（native Whisper 无法被 Python thread 强制终止）
- 用户取消任务
- Faster-Whisper 模型加载与 native 转写都采用 cancellation shielding；取消后不会提前释放 GPU slot，也不会并发重复加载第二份模型
- Python / yt-dlp / FFmpeg / data directory / CUDA / CTranslate2 启动检查
- GPU 和 ASR 模型手动测试接口
- 任务自动过期清理
- Docker 日志轮转
- URL 默认禁止 localhost、私网、保留地址，并启用视频源域名 allowlist；安全策略不可从 Web 热放宽
- SQLite 自动兼容旧表结构
- Web Secret 不回显；Markdown 结果经过 allowlist HTML sanitizer；Web 带 CSP/安全头、CSRF 和 same-origin 防护
- Docker 单文件 bind mount 无法原子 replace 时，配置保存自动退化为锁保护的原地写入
- ASR model/device/compute/concurrency 固定为 restart-required，避免热切换导致双模型占用 GPU
- 任务队列、视频时长、下载大小和最低磁盘余量均有上限
- `/readyz` 按 TTL 重新检查运行环境，不再永久使用启动时快照

## Docker 启动

```bash
cp .env.example .env
# 修改 .env / config.yaml
docker compose up --build -d
```

打开：

```text
http://localhost:8080
```

建议首次启动后访问 `/settings`：

1. 测试 LLM
2. 测试 GPU
3. 点击“加载 Whisper 模型”完成模型下载/加载验证

查看运行状态：

```bash
curl http://localhost:8080/healthz
curl http://localhost:8080/readyz
```

`healthz=200` 只表示进程存活；`readyz=200` 才表示当前配置要求的 yt-dlp/FFmpeg/GPU 等检查通过。

## 最小配置

```yaml
video:
  prefer_subtitle: true
  subtitle_languages:
    - zh-Hans
    - zh-Hant
    - zh.*
    - en.*
  cookies_file: ""
  request_timeout_seconds: 20
  allow_private_urls: false
  allow_unlisted_domains: false
  allowed_domains:
    - bilibili.com
    - b23.tv
    - youtube.com
    - youtu.be
    - douyin.com
    - iesdouyin.com
    - kuaishou.com
    - tiktok.com

bilibili:
  prefer_subtitle: true
  cookie: "${BILIBILI_COOKIE}"

asr:
  enabled: true
  model: large-v3
  device: cuda
  compute_type: float16
  language: zh
  concurrency: 1

llm:
  base_url: "${LLM_BASE_URL}"
  api_key: "${LLM_API_KEY}"
  model: "${LLM_MODEL}"
  temperature: 0.2
```

## Cookies

### Bilibili

使用环境变量：

```env
BILIBILI_COOKIE=SESSDATA=...; bili_jct=...
```

Bilibili Cookie 不会通过 GET settings API 明文返回。

### 其它 yt-dlp 视频源

需要登录态的站点建议使用 Netscape `cookies.txt`：

```text
./cookies/cookies.txt
```

并配置：

```yaml
video:
  cookies_file: /cookies/cookies.txt
```

Compose 已把宿主机 `./cookies` 只读挂载到容器 `/cookies`。

## Web / API 鉴权

Docker Compose 默认只发布到宿主机 `127.0.0.1:${APP_PORT}`。如果需要改成 `0.0.0.0`、反向代理或对其它机器开放，建议同时配置：

```env
WEB_USERNAME=admin
WEB_PASSWORD=change-me
```

设置后 Web 和 `/api/*` 使用 HTTP Basic Auth；`/healthz`、`/readyz` 保持匿名，便于容器/编排健康检查。Web 表单另外带 CSRF token，浏览器 REST 写请求执行 same-origin + CSRF 校验。

## API

创建任务：

```bash
curl -X POST http://localhost:8080/api/v1/summaries \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.youtube.com/watch?v=..."}'
```

或：

```bash
curl -X POST http://localhost:8080/api/v1/summaries \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://www.bilibili.com/video/BV..."}'
```

查询：

```bash
curl http://localhost:8080/api/v1/tasks/<task_id>
```

取消：

```bash
curl -X POST http://localhost:8080/api/v1/tasks/<task_id>/cancel
```

长日志：

```bash
curl http://localhost:8080/api/v1/tasks/<task_id>/log
```

环境检查：

```bash
curl -X POST http://localhost:8080/api/v1/system/test-gpu
curl -X POST http://localhost:8080/api/v1/system/test-asr
```

## 数据目录

```text
/data/
├── tasks.db
└── tasks/
    └── <task_id>/
        ├── task.log
        ├── metadata.json
        ├── subtitles/
        ├── transcript.json
        ├── summary.json
        └── summary.md
```

如果视频没有字幕，还会临时/持久存在任务目录中的音频文件。

## Windows + Docker Desktop + NVIDIA

建议：

- Docker Desktop 使用 WSL2 backend
- Windows NVIDIA 驱动支持 WSL GPU
- Docker Desktop 已开启 GPU passthrough
- 运行 `docker compose up --build -d` 后先访问 `/settings` 做 GPU 检测

宿主机可先检查：

```powershell
nvidia-smi
```

容器启动后：

```bash
docker exec bilinote-summary nvidia-smi
docker exec bilinote-summary python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())"
```

## 部署机验收

Linux / WSL：

```bash
./scripts/acceptance.sh
```

Windows PowerShell：

```powershell
./scripts/acceptance.ps1
```

脚本会检查容器内 Python、yt-dlp、FFmpeg、`nvidia-smi`、CTranslate2 CUDA device，以及 `/healthz` / `/readyz`。

## 本地测试

```bash
python -m pip install -r requirements-dev.txt
python -m compileall -q app tests run.py
python -m pytest -q
python scripts/runtime_smoke.py
```

当前测试包含：URL/私网与域名 allowlist、source registry、VTT、Chunk、子进程超时/取消、日志脱敏、XSS sanitizer、CSRF、队列容量、ASR 热配置拒绝、Whisper 模型加载取消等。

本轮还执行过一条独立 runtime smoke：使用模拟 yt-dlp extractor 和本地 OpenAI-compatible HTTP 服务，真实启动 FastAPI lifespan 并完成“创建任务 → YouTube source → 字幕 → 总结 → SQLite/文件落盘 → succeeded”的完整链路。

## 已知边界

- yt-dlp 是变化最快的边界，应定期升级镜像。不要自己在本项目内重写各站点解析协议。
- 部分站点需要 Cookie、JS challenge、地区 IP 或账号权限。
- `allow_private_urls=false` + `allowed_domains` 会保护入口和后续复用的 canonical URL，但 yt-dlp extractor 内部自己的站点请求仍不是完整网络沙箱。公网/多租户部署必须在 Docker/K8s/防火墙层限制容器访问 RFC1918、link-local 和云 metadata 网段。
- Faster-Whisper native 模型加载/推理无法被 Python thread 强制中断；取消或 soft timeout 发生在 native 阶段时，会等待当前调用安全结束后再释放 GPU slot。若业务要求严格 hard timeout，需要把 ASR 放入可杀掉的独立 worker process。
- 目前使用进程内 `asyncio.Queue`，适合单容器/单节点。水平扩容前应换成 Redis/外部队列。
