from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class LlmTextResult:
    translated_title: str
    summary: str
    model: str
    prompt_version: str
    generated_at: datetime
    source_revision_id: int


LLM_ALLOWED_TASKS = frozenset({"translate", "summarize", "classify_topic"})
LLM_FORBIDDEN_AUTHORITATIVE_TASKS = frozenset(
    {"calculate_metric", "rank", "forecast_number", "source_status", "provenance"}
)


def assert_non_authoritative_task(task: str) -> None:
    if task not in LLM_ALLOWED_TASKS:
        raise ValueError(f"LLM task is outside the non-authoritative boundary: {task}")
