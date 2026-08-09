import asyncio
import random

import httpx

from app.config.schema import LLMConfig


class OpenAICompatibleClient:
    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    def validate(self) -> None:
        if not self.cfg.base_url:
            raise RuntimeError("llm.base_url is not configured")
        if not self.cfg.model:
            raise RuntimeError("llm.model is not configured")

    async def chat(self, system: str, user: str, *, max_tokens: int | None = None) -> str:
        self.validate()
        headers = {"Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        payload = {
            "model": self.cfg.model,
            "temperature": self.cfg.temperature,
            "max_tokens": max_tokens or self.cfg.max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        url = self.cfg.base_url.rstrip("/") + "/chat/completions"
        last_exc: Exception | None = None
        async with httpx.AsyncClient(timeout=self.cfg.timeout_seconds) as client:
            for attempt in range(self.cfg.retries + 1):
                try:
                    response = await client.post(url, headers=headers, json=payload)
                    if response.status_code == 429 or response.status_code >= 500:
                        raise httpx.HTTPStatusError(
                            f"retryable LLM HTTP {response.status_code}", request=response.request, response=response
                        )
                    response.raise_for_status()
                    data = response.json()
                    try:
                        content = data["choices"][0]["message"]["content"]
                    except Exception as exc:
                        raise RuntimeError(f"unexpected LLM response shape: {data}") from exc
                    text = str(content or "").strip()
                    if not text:
                        raise RuntimeError("LLM returned empty content")
                    return text
                except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as exc:
                    last_exc = exc
                    if attempt >= self.cfg.retries:
                        break
                    await asyncio.sleep(self.cfg.retry_backoff_seconds * (2 ** attempt) + random.uniform(0, 0.25))
        raise RuntimeError(f"LLM request failed after retries: {last_exc}")

    async def health(self) -> dict:
        self.validate()
        text = await self.chat("只回答 OK。", "连通性测试", max_tokens=16)
        return {"ok": True, "response": text[:100], "model": self.cfg.model}
