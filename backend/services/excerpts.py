"""Deterministic source excerpts. Offsets refer to the exact extracted page text."""
import re
from dataclasses import dataclass

@dataclass(frozen=True)
class Excerpt:
    id: str
    source_id: str
    start: int
    end: int
    text: str


def source_excerpts(source):
    """Prefer sentences; split long sentences into overlapping <=400-char windows.

    Segmentation is not a claim of sentence understanding. Attribution still
    checks the complete source, including qualifications outside an excerpt.
    """
    text = source.text
    boundaries = [0] + [m.end() for m in re.finditer(r'(?<=[.!?])\s+', text)] + [len(text)]
    result = []
    for start, end in zip(boundaries, boundaries[1:]):
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        while start < end:
            stop = min(start + 400, end)
            if stop < end:
                space = text.rfind(' ', start + 200, stop)
                if space > start:
                    stop = space
            result.append(Excerpt(f'{source.id}:E{len(result) + 1}', source.id, start, stop, text[start:stop]))
            if stop == end:
                break
            start = stop - 80  # Preserve context at long-sentence boundaries.
    return result
