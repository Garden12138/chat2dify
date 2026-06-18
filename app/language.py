from __future__ import annotations

import re


LANGUAGE_RESPONSE_INSTRUCTION = (
    "语言策略：如果用户输入主要为中文，请用中文回复；如果用户输入主要为英文，请用英文回复。"
)

TECHNICAL_LATIN_TERMS = re.compile(
    r"\b(?:agent-chat|app_id|chatbot|chatflow|completion|workflow|agent|dify|hash|json|app)\b",
    flags=re.IGNORECASE,
)


def ensure_language_response_instruction(prompt: str) -> str:
    text = str(prompt or "").strip()
    if not text:
        return LANGUAGE_RESPONSE_INSTRUCTION
    if _has_language_response_instruction(text):
        return text
    return f"{text.rstrip()}\n{LANGUAGE_RESPONSE_INSTRUCTION}"


def _has_language_response_instruction(text: str) -> bool:
    compact = "".join(str(text or "").split())
    if "语言策略" in compact:
        return True
    return "中文" in compact and "英文" in compact and "用户输入" in compact


def detect_primary_language(text: str) -> str:
    cleaned = TECHNICAL_LATIN_TERMS.sub(" ", str(text or ""))
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", cleaned))
    latin_chars = sum(len(word) for word in re.findall(r"[A-Za-z]+", cleaned))
    if chinese_chars and not latin_chars:
        return "zh"
    if latin_chars and not chinese_chars:
        return "en"
    if not chinese_chars and not latin_chars:
        return "zh"
    return "zh" if chinese_chars * 2 >= latin_chars else "en"
