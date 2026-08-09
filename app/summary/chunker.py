from app.models import Transcript, TranscriptSegment


def format_segment(seg: TranscriptSegment) -> str:
    minutes = int(seg.start // 60)
    seconds = int(seg.start % 60)
    return f"[{minutes:02d}:{seconds:02d}] {seg.text}"


def chunk_transcript(transcript: Transcript, max_chars: int) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for seg in transcript.segments:
        line = format_segment(seg)
        if current and current_len + len(line) + 1 > max_chars:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        if len(line) > max_chars:
            # Defensive split for pathological subtitle segments.
            for i in range(0, len(line), max_chars):
                piece = line[i:i + max_chars]
                if current:
                    chunks.append("\n".join(current))
                    current = []
                    current_len = 0
                chunks.append(piece)
            continue
        current.append(line)
        current_len += len(line) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks
