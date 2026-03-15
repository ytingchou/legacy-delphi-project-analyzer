from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ModelProfile:
    name: str
    max_input_tokens: int
    max_output_tokens: int
    max_context_paths: int
    temperature: float
    strict_json: bool
    allow_open_ended_design: bool
    preferred_task_types: list[str] = field(default_factory=list)


MODEL_PROFILES = {
    "qwen3_128k_weak": ModelProfile(
        name="qwen3_128k_weak",
        max_input_tokens=12000,
        max_output_tokens=1000,
        max_context_paths=4,
        temperature=0.1,
        strict_json=True,
        allow_open_ended_design=False,
        preferred_task_types=[
            "resolve_search_path",
            "infer_placeholder_meaning",
            "classify_query_intent",
            "validate_transition_spec",
            "summarize_form_behavior",
        ],
    ),
    "qwen3_128k_validate": ModelProfile(
        name="qwen3_128k_validate",
        max_input_tokens=8000,
        max_output_tokens=800,
        max_context_paths=3,
        temperature=0.0,
        strict_json=True,
        allow_open_ended_design=False,
        preferred_task_types=[
            "validate_transition_spec",
            "classify_query_intent",
            "infer_placeholder_meaning",
        ],
    ),
    "strong_reasoning": ModelProfile(
        name="strong_reasoning",
        max_input_tokens=24000,
        max_output_tokens=2000,
        max_context_paths=6,
        temperature=0.1,
        strict_json=True,
        allow_open_ended_design=True,
        preferred_task_types=[],
    ),
}


def get_model_profile(name: str) -> ModelProfile:
    return MODEL_PROFILES.get(name, MODEL_PROFILES["qwen3_128k_weak"])
