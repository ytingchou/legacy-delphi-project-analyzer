# Quick Start: Analyzer Artifacts + Cline

This guide is the fastest way to use `legacy-delphi-project-analyzer` artifacts with:

- `Cline CLI`
- `VSCode Cline extension`
- weak internal models such as `qwen3`
- practical context limits far below the nominal `128k`

The goal is simple:

1. Analyze the Delphi project.
2. Generate small task packs.
3. Feed only one task pack at a time into Cline.
4. Save JSON output back into the artifact directory.
5. Validate and retry if needed.

Do not load the whole repo or the whole `llm-pack/` into Cline.

## 1. Analyze The Project

Run:

```bash
legacy-delphi-analyzer run-phases /path/to/project --output-dir /path/to/artifacts
legacy-delphi-analyzer build-taskpacks /path/to/artifacts
legacy-delphi-analyzer build-cheatsheet /path/to/artifacts
```

If you are using an OpenAI-compatible provider behind Cline, verify it first:

```bash
legacy-delphi-analyzer validate-provider \
  --provider-base-url http://your-provider-host:8000/v1 \
  --model your-model \
  --verbose
```

## 2. Open The Two Cheat Sheets

These files are now your fastest entry points:

- `llm-pack/cline-cheat-sheet.md`
- `runtime/cline-cheat-sheet.md`

Use:

- `llm-pack/cline-cheat-sheet.md` for the overall workflow and prompt rules
- `runtime/cline-cheat-sheet.md` for the current top blocker tasks and exact commands

## 3. Pick One Task Only

Go to:

```text
/path/to/artifacts/runtime/taskpacks/
```

Pick one `<task-id>` folder.

The only files you should copy into Cline for the first attempt are:

- `agent-task.md`
- `compiled-context.md`
- `agent-expected-output-schema.json`

Do not paste the full `taskpack.json`.
Do not paste unrelated module artifacts.
Do not mix UI and SQL tasks in the same prompt.

## 4. Fastest Workflow: Cline CLI

For `Cline CLI`, paste the contents of:

1. `agent-task.md`
2. `compiled-context.md`
3. `agent-expected-output-schema.json`

Then append this instruction:

```text
只輸出 JSON，不要輸出 markdown，不要解釋，不要加入 schema 外的欄位。
如果不確定，放到 remaining_unknowns 或 missing_assumptions。
```

### Example: Backend SQL

```text
你現在只做一個 bounded task。

任務說明：
<貼上 agent-task.md>

上下文：
<貼上 compiled-context.md>

輸出格式：
<貼上 agent-expected-output-schema.json>

請根據以上 artifacts，產出一個給 Java Spring Boot BFF 使用的 Oracle 19c SQL implementation logic。

限制：
- 只處理一個 endpoint 或一個 query family
- 只使用提供的 evidence
- 不要猜 table、column、join、filter 規則
- 如果不確定，放到 remaining_unknowns 或 missing_assumptions
- 只輸出 JSON，不要輸出 markdown，不要解釋
```

### Example: React UI

```text
你現在只做一個 bounded task。

任務說明：
<貼上 agent-task.md>

上下文：
<貼上 compiled-context.md>

輸出格式：
<貼上 agent-expected-output-schema.json>

請根據以上 artifacts，產出一個 React page 的 pseudo UI 或 reference UI。

限制：
- 只處理一個 page
- 不要設計整個系統
- 不要猜 backend contract
- 如果不確定，放到 remaining_unknowns 或 missing_assumptions
- 只輸出 JSON，不要輸出 markdown，不要解釋
```

### Example: React Integration

If you need to merge the generated page into another React transition project, build target integration artifacts first:

```bash
legacy-delphi-analyzer build-target-pack /path/to/artifacts /path/to/target-react-project
```

Then use the same 3 task-pack files plus the task-specific target integration artifact and prompt:

```text
請根據以上 artifacts，說明如何把這個 page 整合進既有的 React transition project。

限制：
- 只處理一個 page
- 只使用 artifacts 裡已知的 route、feature dir、api client、state file
- 不要猜 project structure
- 只輸出 JSON，不要輸出 markdown，不要解釋
```

## 5. Fastest Workflow: VSCode Cline Extension

For the VSCode extension, the simplest workflow is also manual:

1. Open the analyzer artifacts in VSCode.
2. Open one `runtime/taskpacks/<task-id>/` directory.
3. Copy the contents of:
   - `agent-task.md`
   - `compiled-context.md`
   - `agent-expected-output-schema.json`
4. Start a new Cline chat for that single task.
5. Paste the bounded prompt.
6. Save the returned JSON into:

```text
runtime/taskpacks/<task-id>/agent-response.json
```

Do not use one Cline chat for multiple task IDs.
Open a fresh chat per task.

## 6. Save The Response In The Right Shape

The easiest response wrapper is:

```json
{
  "task_id": "<task-id>",
  "status": "completed",
  "result": {},
  "supported_claims": [],
  "unsupported_claims": [],
  "remaining_unknowns": [],
  "recommended_next_task": ""
}
```

If your Cline output already matches the expected schema, place that schema output under `result`.

## 7. Validate Immediately

After saving `agent-response.json`, run:

```bash
legacy-delphi-analyzer validate-response /path/to/artifacts <task-id>
```

If validation fails, run:

```bash
legacy-delphi-analyzer retry-plan /path/to/artifacts <task-id>
```

Then feed `retry-plan.md` back into Cline and ask for a corrected JSON response.

## 8. Task Order That Usually Works Best

When time is short, do tasks in this order:

1. `infer_placeholder_meaning`
2. `classify_query_intent`
3. `validate_transition_spec`
4. backend SQL tasks
5. UI pseudo/reference tasks
6. UI integration tasks

This keeps the weak model on small, grounded tasks first.

## 9. Recommended Artifact Families

### For Spring Boot BFF + Oracle 19c

Use:

- `llm-pack/backend-sql-manifest.json`
- `llm-pack/backend-sql-guide.md`
- `llm-pack/bff-sql/*.md`
- `llm-pack/bff-sql-compiler/*`

Always keep it to one endpoint or one query family per prompt.

### For React UI

Use:

- `llm-pack/ui-pseudo/*.md`
- `llm-pack/ui-reference/*.md`
- `llm-pack/ui-reference/*.html`

Always keep it to one page per prompt.

### For React Target Integration

Use:

- `llm-pack/target-integration/*.md`
- `llm-pack/target-integration/target-integration-manifest.json`
- `llm-pack/ui-integration/*.md`

Always keep it to one page integration step per prompt.

## 10. Common Failure Modes

### Model returned markdown or prose instead of JSON

Use this repair prompt:

```text
請把下面內容轉成合法 JSON。

規則：
- 保留原本意思
- 不要加說明
- 不要加 markdown fence
- 不確定的內容放到 remaining_unknowns
- 只輸出 JSON

原始內容：
<貼上 Cline 原始輸出>
```

### Response keeps failing validation

Do not add more context.
Use the generated retry plan instead:

```bash
legacy-delphi-analyzer retry-plan /path/to/artifacts <task-id>
```

### Context is still too large

Use only:

- `agent-task.md`
- `compiled-context.md`
- `agent-expected-output-schema.json`

Do not add the larger bundle or the full business dossier unless the validator tells you the evidence is missing.

## 11. Do Not Do These Things

- Do not paste the whole repo into Cline.
- Do not paste the whole `llm-pack/`.
- Do not ask qwen3 to design an entire module in one shot.
- Do not mix backend SQL and UI work in one chat.
- Do not accept non-JSON output as the final result.

## 12. Minimal Team SOP

If your team needs the shortest operational path, use this every time:

```bash
legacy-delphi-analyzer run-phases /path/to/project --output-dir /path/to/artifacts
legacy-delphi-analyzer build-taskpacks /path/to/artifacts
legacy-delphi-analyzer build-cheatsheet /path/to/artifacts
```

Then:

1. open `runtime/cline-cheat-sheet.md`
2. pick the first blocker task
3. copy the 3 task-pack files into Cline
4. save JSON to `agent-response.json`
5. run `validate-response`
6. if needed, run `retry-plan`

That is the fastest reliable workflow.
