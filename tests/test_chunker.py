from app.models import Transcript, TranscriptSegment
from app.summary.chunker import chunk_transcript


def test_chunk_transcript_preserves_content():
    transcript = Transcript(
        source="test",
        text="a b c",
        segments=[
            TranscriptSegment(start=0, end=1, text="a"),
            TranscriptSegment(start=2, end=3, text="b"),
            TranscriptSegment(start=4, end=5, text="c"),
        ],
    )
    chunks = chunk_transcript(transcript, 20)
    joined = "\n".join(chunks)
    assert "a" in joined and "b" in joined and "c" in joined
