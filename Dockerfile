# syntax=docker/dockerfile:1.7
FROM golang:1.23-bookworm AS builder
WORKDIR /src
COPY go.mod go.sum* ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/bilinote ./cmd/server

FROM debian:bookworm-slim
ARG YTDLP_VERSION=latest
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl ffmpeg tini \
    && if [ "$YTDLP_VERSION" = "latest" ]; then \
         curl -fL --retry 3 https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp; \
       else \
         curl -fL --retry 3 "https://github.com/yt-dlp/yt-dlp/releases/download/${YTDLP_VERSION}/yt-dlp" -o /usr/local/bin/yt-dlp; \
       fi \
    && chmod +x /usr/local/bin/yt-dlp \
    && yt-dlp --version \
    && ffmpeg -version >/dev/null \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /out/bilinote /usr/local/bin/bilinote
COPY config.yaml ./config.yaml
COPY migrations ./migrations
COPY prompts ./prompts
RUN useradd --system --uid 10001 --create-home bilinote \
    && mkdir -p /tmp/bilinote \
    && chown -R bilinote:bilinote /app /tmp/bilinote
USER bilinote
EXPOSE 8080
ENTRYPOINT ["/usr/bin/tini","--","/usr/local/bin/bilinote"]
