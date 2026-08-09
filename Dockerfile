ARG BASE_REGISTRY=docker.io
# Ubuntu 24.04 provides Python 3.12. CUDA 12.9 keeps the runtime in the CUDA-12
# family required by current CTranslate2/faster-whisper and is appropriate for
# newer NVIDIA GPUs, including RTX 50-series/Blackwell hosts.
FROM ${BASE_REGISTRY}/nvidia/cuda:12.9.1-cudnn-runtime-ubuntu24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG PIP_INDEX=https://pypi.org/simple

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv ffmpeg curl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -i ${PIP_INDEX} -r /app/requirements.txt \
    && python -c "import sys; assert sys.version_info >= (3,11); import bleach, markdown, ctranslate2, faster_whisper" \
    && yt-dlp --version \
    && ffmpeg -version | head -1

COPY app /app/app
COPY prompts /app/prompts
COPY config.yaml /app/config.yaml
COPY run.py /app/run.py

RUN mkdir -p /data/tasks /models /cookies
ENV APP_CONFIG=/app/config.yaml
ENV HF_HOME=/models/huggingface
ENV PYTHONUNBUFFERED=1

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/run.py"]
