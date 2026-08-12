ARG BASE_REGISTRY=docker.io
# Ubuntu 24.04 provides Python 3.12. The plain Ubuntu base is used instead of
# nvidia/cuda because the CUDA runtime (cuBLAS/cuDNN) is installed from pip
# wheels below — this keeps the base image small and lets all large downloads
# go through fast direct/mirror connections. GPU passthrough only needs the
# host NVIDIA driver (CUDA 12.x capable).
FROM ${BASE_REGISTRY}/library/ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive
ARG PIP_INDEX=https://pypi.org/simple
ARG APT_MIRROR=mirrors.tuna.tsinghua.edu.cn

# Ubuntu 24.04 uses deb822 sources; point them at the configured mirror.
RUN sed -i "s|//archive.ubuntu.com|//${APT_MIRROR}|g; s|//security.ubuntu.com|//${APT_MIRROR}|g" \
    /etc/apt/sources.list.d/ubuntu.sources \
    && apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-venv ffmpeg curl ca-certificates tini \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:${PATH}"

WORKDIR /app
COPY requirements.txt /app/requirements.txt
# nvidia-cublas-cu12 / nvidia-cudnn-cu12 provide the CUDA runtime libraries
# that CTranslate2 normally gets from the nvidia/cuda base image.
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -i ${PIP_INDEX} -r /app/requirements.txt \
    && python -m pip install --no-cache-dir -i ${PIP_INDEX} nvidia-cublas-cu12 nvidia-cudnn-cu12 \
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
# Let CTranslate2 find the pip-installed cuBLAS/cuDNN shared libraries.
ENV LD_LIBRARY_PATH=/opt/venv/lib/python3.12/site-packages/nvidia/cublas/lib:/opt/venv/lib/python3.12/site-packages/nvidia/cudnn/lib

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1
ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["python", "/app/run.py"]
