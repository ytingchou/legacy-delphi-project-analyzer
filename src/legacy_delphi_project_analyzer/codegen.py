from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from legacy_delphi_project_analyzer.models import GeneratedCodeArtifact
from legacy_delphi_project_analyzer.orchestrator import rerun_analysis_from_runtime_state
from legacy_delphi_project_analyzer.utils import ensure_directory, slugify, write_json, write_text


def generate_transition_code(
    analysis_dir: Path,
    *,
    output_dir: Path | None = None,
    require_validated: bool = True,
) -> list[GeneratedCodeArtifact]:
    analysis_dir = analysis_dir.resolve()
    output = rerun_analysis_from_runtime_state(analysis_dir)
    validation_results = _load_validation_results(analysis_dir / "runtime" / "validation-results.json")
    validated_modules = {
        str(item.get("module_name") or item.get("subject_name") or "")
        for item in validation_results
        if str(item.get("status") or "") in {"accepted", "accepted_with_warnings"}
    }
    codegen_dir = (output_dir or analysis_dir / "codegen").resolve()
    ensure_directory(codegen_dir)
    generated: list[GeneratedCodeArtifact] = []

    for spec in output.transition_specs:
        if require_validated and spec.module_name not in validated_modules:
            continue
        module_slug = slugify(spec.module_name)
        react_dir = codegen_dir / "react" / module_slug
        spring_dir = codegen_dir / "spring" / module_slug
        ensure_directory(react_dir)
        ensure_directory(spring_dir / "dto")

        for page in spec.frontend_pages:
            page_path = react_dir / f"{page.name}.tsx"
            write_text(page_path, _render_react_page(spec.module_name, page))
            generated.append(
                GeneratedCodeArtifact(
                    module_name=spec.module_name,
                    language="typescript",
                    relative_path=page_path.relative_to(codegen_dir).as_posix(),
                    artifact_kind="react-page",
                    source_spec=f"{spec.module_name}:{page.name}",
                    notes=["Generated from validated transition spec and UI handoff artifacts."],
                )
            )
        api_path = react_dir / f"{module_slug}-api.ts"
        write_text(api_path, _render_react_api(spec))
        generated.append(
            GeneratedCodeArtifact(
                module_name=spec.module_name,
                language="typescript",
                relative_path=api_path.relative_to(codegen_dir).as_posix(),
                artifact_kind="react-api",
                source_spec=spec.module_name,
            )
        )
        types_path = react_dir / f"{module_slug}-types.ts"
        write_text(types_path, _render_react_types(spec))
        generated.append(
            GeneratedCodeArtifact(
                module_name=spec.module_name,
                language="typescript",
                relative_path=types_path.relative_to(codegen_dir).as_posix(),
                artifact_kind="react-types",
                source_spec=spec.module_name,
            )
        )

        controller_path = spring_dir / f"{spec.module_name}Controller.java"
        service_path = spring_dir / f"{spec.module_name}Service.java"
        repository_path = spring_dir / f"{spec.module_name}Repository.java"
        write_text(controller_path, _render_spring_controller(spec))
        write_text(service_path, _render_spring_service(spec))
        write_text(repository_path, _render_spring_repository(spec))
        generated.extend(
            [
                GeneratedCodeArtifact(
                    module_name=spec.module_name,
                    language="java",
                    relative_path=controller_path.relative_to(codegen_dir).as_posix(),
                    artifact_kind="spring-controller",
                    source_spec=spec.module_name,
                ),
                GeneratedCodeArtifact(
                    module_name=spec.module_name,
                    language="java",
                    relative_path=service_path.relative_to(codegen_dir).as_posix(),
                    artifact_kind="spring-service",
                    source_spec=spec.module_name,
                ),
                GeneratedCodeArtifact(
                    module_name=spec.module_name,
                    language="java",
                    relative_path=repository_path.relative_to(codegen_dir).as_posix(),
                    artifact_kind="spring-repository",
                    source_spec=spec.module_name,
                ),
            ]
        )
        for dto in spec.dtos:
            dto_path = spring_dir / "dto" / f"{dto.name}.java"
            write_text(dto_path, _render_spring_dto(dto))
            generated.append(
                GeneratedCodeArtifact(
                    module_name=spec.module_name,
                    language="java",
                    relative_path=dto_path.relative_to(codegen_dir).as_posix(),
                    artifact_kind="spring-dto",
                    source_spec=f"{spec.module_name}:{dto.name}",
                )
            )

    write_json(codegen_dir / "manifest.json", generated)
    write_text(codegen_dir / "README.md", _render_codegen_summary(generated, require_validated))
    return generated


def _load_validation_results(path: Path) -> list[dict]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _render_react_page(module_name: str, page: Any) -> str:
    api_call = page.data_dependencies[0] if page.data_dependencies else "loadData"
    input_state = "\n".join(
        f"  const [{field.name}, set{field.name[:1].upper() + field.name[1:]}] = useState('');"
        for field in page.inputs
    ) or "  const [formState, setFormState] = useState({});"
    return f"""import {{ useState }} from 'react';

export function {page.name}() {{
{input_state}

  async function handlePrimaryAction() {{
    // TODO: Replace with generated API client call for {api_call}.
  }}

  return (
    <section>
      <header>
        <h1>{page.name}</h1>
        <p>{page.purpose}</p>
      </header>
      <div>
        <p>TODO: Build filters, action bar, and result table from the pseudo UI artifact.</p>
      </div>
      <button onClick={{ handlePrimaryAction }}>Run first slice</button>
    </section>
  );
}}
"""


def _render_react_api(spec: Any) -> str:
    functions = []
    for endpoint in spec.backend_endpoints:
        function_name = endpoint.name[:1].lower() + endpoint.name[1:]
        functions.append(
            f"""export async function {function_name}(payload: {endpoint.request_dto or 'Record<string, unknown>'}) {{
  const response = await fetch('{endpoint.path}', {{
    method: '{endpoint.method}',
    headers: {{ 'Content-Type': 'application/json' }},
    body: { 'undefined' if endpoint.method == 'GET' else 'JSON.stringify(payload)' },
  }});
  return response.json() as Promise<{endpoint.response_dto or 'unknown'}>;
}}"""
        )
    return "\n\n".join(functions) + "\n"


def _render_react_types(spec: Any) -> str:
    parts = []
    for dto in spec.dtos:
        fields = "\n".join(f"  {field.name}: { _ts_type(field.data_type) };" for field in dto.fields) or "  // TODO: add fields"
        parts.append(f"export interface {dto.name} {{\n{fields}\n}}")
    return "\n\n".join(parts) + "\n"


def _render_spring_controller(spec: Any) -> str:
    methods = []
    for endpoint in spec.backend_endpoints:
        annotation = {
            "GET": "@GetMapping",
            "POST": "@PostMapping",
            "PUT": "@PutMapping",
            "DELETE": "@DeleteMapping",
        }.get(endpoint.method, "@PostMapping")
        request_param = "" if endpoint.method == "GET" else f"@RequestBody {endpoint.request_dto or 'Object'} request"
        service_method = endpoint.name[:1].lower() + endpoint.name[1:]
        methods.append(
            f"""  {annotation}(\"{endpoint.path}\")
  public {endpoint.response_dto or 'Object'} {service_method}({request_param}) {{
    // TODO: For GET endpoints, map request params explicitly from the validated request DTO fields.
    return service.{service_method}({ 'request' if request_param else '' });
  }}"""
        )
    return f"""package generated.spring.{slugify(spec.module_name).replace('-', '')};

import org.springframework.web.bind.annotation.*;

@RestController
public class {spec.module_name}Controller {{
  private final {spec.module_name}Service service;

  public {spec.module_name}Controller({spec.module_name}Service service) {{
    this.service = service;
  }}

{chr(10).join(methods)}
}}
"""


def _render_spring_service(spec: Any) -> str:
    methods = []
    for endpoint in spec.backend_endpoints:
        service_method = endpoint.name[:1].lower() + endpoint.name[1:]
        methods.append(
            f"""  public {endpoint.response_dto or 'Object'} {service_method}({endpoint.request_dto or 'Object'} request) {{
    // TODO: Apply validated orchestration steps and placeholder handling from the BFF SQL artifact.
    return repository.{service_method}(request);
  }}"""
        )
    return f"""package generated.spring.{slugify(spec.module_name).replace('-', '')};

public class {spec.module_name}Service {{
  private final {spec.module_name}Repository repository;

  public {spec.module_name}Service({spec.module_name}Repository repository) {{
    this.repository = repository;
  }}

{chr(10).join(methods)}
}}
"""


def _render_spring_repository(spec: Any) -> str:
    methods = []
    for endpoint in spec.backend_endpoints:
        method_name = endpoint.name[:1].lower() + endpoint.name[1:]
        methods.append(
            f"""  {endpoint.response_dto or 'Object'} {method_name}({endpoint.request_dto or 'Object'} request);"""
        )
    return f"""package generated.spring.{slugify(spec.module_name).replace('-', '')};

public interface {spec.module_name}Repository {{
{chr(10).join(methods)}
}}
"""


def _render_spring_dto(dto: Any) -> str:
    fields = "\n".join(f"  private { _java_type(field.data_type) } {field.name};" for field in dto.fields) or "  // TODO: add fields"
    return f"""package generated.dto;

public class {dto.name} {{
{fields}
}}
"""


def _render_codegen_summary(generated: list[GeneratedCodeArtifact], require_validated: bool) -> str:
    return "\n".join(
        [
            "# Generated Code Skeletons",
            "",
            f"- Files: {len(generated)}",
            f"- Validation required: {str(require_validated).lower()}",
            "",
            "These files are generated skeletons. They are intended as a bounded starting point for later implementation, not as production-complete code.",
            "",
        ]
    )


def _ts_type(value: str) -> str:
    mapping = {
        "number": "number",
        "number[]": "number[]",
        "string[]": "string[]",
        "datetime": "string",
    }
    return mapping.get(value, "string")


def _java_type(value: str) -> str:
    mapping = {
        "number": "BigDecimal",
        "number[]": "List<BigDecimal>",
        "string[]": "List<String>",
        "datetime": "OffsetDateTime",
        "string": "String",
    }
    return mapping.get(value, "String")
