import asyncio
from pathlib import Path

from app.config.schema import SummaryConfig
from app.llm.openai import OpenAICompatibleClient
from app.models import Transcript
from .chunker import chunk_transcript


class VideoSummarizer:
    def __init__(self, cfg: SummaryConfig, llm: OpenAICompatibleClient):
        self.cfg = cfg
        self.llm = llm

    def _prompt(self) -> str:
        path = Path(self.cfg.prompt_file)
        if not path.exists():
            raise FileNotFoundError(f"summary prompt not found: {path}")
        return path.read_text(encoding="utf-8")

    async def summarize(self, transcript: Transcript, title: str) -> tuple[str, int]:
        chunks = chunk_transcript(transcript, self.cfg.chunk_chars)
        if not chunks:
            raise RuntimeError("transcript is empty")
        system = self._prompt()
        sem = asyncio.Semaphore(self.cfg.parallel)

        async def summarize_chunk(index: int, text: str) -> str:
            async with sem:
                user = f"视频标题：{title}\n这是第 {index + 1}/{len(chunks)} 段字幕：\n\n{text}"
                return await self.llm.chat(system, user)

        partials = await asyncio.gather(*(summarize_chunk(i, c) for i, c in enumerate(chunks)))
        if len(partials) == 1:
            return partials[0], 1
        result = await self._reduce(title, partials)
        return result, len(chunks)

    async def _reduce(self, title: str, partials: list[str]) -> str:
        merge_prompt = (
            "你负责合并多个分段视频总结。去重、保留事实和重要技术细节，输出统一 Markdown。"
            "不得添加输入总结里没有的信息。结构包含：概要、关键要点、操作步骤（如有）、注意事项（如有）。"
        )
        current = partials
        # Hierarchical reduce prevents a long video from creating an oversized final request.
        while len(current) > 1:
            groups: list[list[str]] = []
            group: list[str] = []
            size = 0
            budget = max(4000, self.cfg.chunk_chars)
            for text in current:
                addition = len(text) + 80
                if group and size + addition > budget:
                    groups.append(group)
                    group = []
                    size = 0
                group.append(text)
                size += addition
            if group:
                groups.append(group)
            # Guarantee that each reduce round shrinks the list, even if a single
            # partial summary is larger than the nominal character budget.
            if len(groups) == len(current):
                groups = [current[i:i + 2] for i in range(0, len(current), 2)]
            async def merge_group(items: list[str]) -> str:
                merged_input = "\n\n".join(f"## 分段总结 {i + 1}\n{x}" for i, x in enumerate(items))
                return await self.llm.chat(merge_prompt, f"视频标题：{title}\n\n{merged_input}")
            current = await asyncio.gather(*(merge_group(g) for g in groups))
        return current[0]
