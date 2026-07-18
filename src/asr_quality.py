from __future__ import annotations

import re
import unicodedata

TIMESTAMP_LINE = re.compile(r"^\d{2}:\d{2}:\d{2}(?:[.,]\d+)?$")
NUMBER_TOKEN = re.compile(
    r"\d+(?:[.,]\d+)*(?:\s*[十百千万亿])?|[零〇一二两三四五六七八九十百千万亿点]+"
)


def extract_transcript_text(content: str) -> str:
    """Remove timestamp-only lines and exporter footers from plain transcripts."""

    lines = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or TIMESTAMP_LINE.fullmatch(line):
            continue
        if line.startswith("由 http") and line.endswith("转录"):
            continue
        lines.append(line)
    return "\n".join(lines)


def normalize_transcript(content: str) -> str:
    normalized = unicodedata.normalize("NFKC", content).casefold()
    return "".join(character for character in normalized if character.isalnum())


def edit_distance(reference: list[str] | str, hypothesis: list[str] | str) -> int:
    if len(reference) < len(hypothesis):
        reference, hypothesis = hypothesis, reference
    previous = list(range(len(hypothesis) + 1))
    for row, reference_item in enumerate(reference, start=1):
        current = [row]
        for column, hypothesis_item in enumerate(hypothesis, start=1):
            substitution = previous[column - 1] + (reference_item != hypothesis_item)
            current.append(
                min(previous[column] + 1, current[column - 1] + 1, substitution)
            )
        previous = current
    return previous[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    normalized_reference = normalize_transcript(reference)
    if not normalized_reference:
        raise ValueError("基准文本为空，无法计算字符错误率。")
    normalized_hypothesis = normalize_transcript(hypothesis)
    return edit_distance(normalized_reference, normalized_hypothesis) / len(
        normalized_reference
    )


def number_token_accuracy(reference: str, hypothesis: str) -> float | None:
    reference_numbers = [
        token.replace(" ", "")
        for token in NUMBER_TOKEN.findall(
            unicodedata.normalize("NFKC", reference).casefold()
        )
    ]
    if not reference_numbers:
        return None
    hypothesis_numbers = [
        token.replace(" ", "")
        for token in NUMBER_TOKEN.findall(
            unicodedata.normalize("NFKC", hypothesis).casefold()
        )
    ]
    errors = edit_distance(reference_numbers, hypothesis_numbers)
    return max(0.0, 1 - errors / len(reference_numbers))


def hotword_recall(
    reference: str, hypothesis: str, hotwords: list[str]
) -> float | None:
    normalized_reference = normalize_transcript(reference)
    normalized_hypothesis = normalize_transcript(hypothesis)
    expected = {
        normalized
        for word in hotwords
        if (normalized := normalize_transcript(word))
        and normalized in normalized_reference
    }
    if not expected:
        return None
    matched = sum(word in normalized_hypothesis for word in expected)
    return matched / len(expected)
