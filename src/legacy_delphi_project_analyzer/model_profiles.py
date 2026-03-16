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
    "qwen3_sql": ModelProfile(
        name="qwen3_sql",
        max_input_tokens=9000,
        max_output_tokens=900,
        max_context_paths=3,
        temperature=0.0,
        strict_json=True,
        allow_open_ended_design=False,
        preferred_task_types=[
            "classify_query_intent",
            "generate_bff_oracle_sql_logic",
            "infer_placeholder_meaning",
        ],
    ),
    "qwen3_ui": ModelProfile(
        name="qwen3_ui",
        max_input_tokens=9000,
        max_output_tokens=1000,
        max_context_paths=3,
        temperature=0.1,
        strict_json=True,
        allow_open_ended_design=False,
        preferred_task_types=[
            "generate_react_pseudo_ui",
            "generate_react_reference_ui",
        ],
    ),
    "qwen3_integration": ModelProfile(
        name="qwen3_integration",
        max_input_tokens=8000,
        max_output_tokens=900,
        max_context_paths=3,
        temperature=0.1,
        strict_json=True,
        allow_open_ended_design=False,
        preferred_task_types=[
            "integrate_react_transition_ui",
        ],
    ),
    "qwen3_validation": ModelProfile(
        name="qwen3_validation",
        max_input_tokens=6000,
        max_output_tokens=700,
        max_context_paths=2,
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


def get_task_specific_profile(default_name: str, task_type: str) -> ModelProfile:
    if default_name.startswith("qwen3"):
        if task_type in {"generate_bff_oracle_sql_logic", "classify_query_intent", "infer_placeholder_meaning"}:
            return MODEL_PROFILES["qwen3_sql"]
        if task_type in {"generate_react_pseudo_ui", "generate_react_reference_ui"}:
            return MODEL_PROFILES["qwen3_ui"]
        if task_type in {"integrate_react_transition_ui"}:
            return MODEL_PROFILES["qwen3_integration"]
        if task_type in {"validate_transition_spec"}:
            return MODEL_PROFILES["qwen3_validation"]
    return get_model_profile(default_name)
