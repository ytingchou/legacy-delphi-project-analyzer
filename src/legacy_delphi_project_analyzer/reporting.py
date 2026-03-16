from __future__ import annotations

import json
from html import escape

from legacy_delphi_project_analyzer.models import (
    AnalysisOutput,
    ComplexityReport,
    LoadBundleArtifact,
    ModuleComplexityScore,
)
from legacy_delphi_project_analyzer.models import to_jsonable


def build_complexity_report(output: AnalysisOutput) -> ComplexityReport:
    flow_by_module = {item.module_name: item for item in output.business_flows}
    query_by_name = {item.name: item for item in output.resolved_queries}
    module_scores: list[ModuleComplexityScore] = []

    for module in output.transition_mapping.modules:
        flow = flow_by_module.get(module.name)
        unresolved_placeholders = 0
        for query_name in module.query_artifacts:
            query = query_by_name.get(query_name)
            if query:
                unresolved_placeholders += len(query.unresolved_placeholders)
        event_steps = len(flow.steps) if flow else 0
        raw_score = (
            len(module.forms) * 8
            + len(module.query_artifacts) * 6
            + event_steps * 4
            + unresolved_placeholders * 5
            + len(module.risks) * 6
            + len(module.open_questions) * 4
        )
        score = min(100, raw_score)
        module_scores.append(
            ModuleComplexityScore(
                module_name=module.name,
                score=score,
                level=_complexity_level(score),
                forms=len(module.forms),
                queries=len(module.query_artifacts),
                event_steps=event_steps,
                unresolved_placeholders=unresolved_placeholders,
                risks=module.risks[:5],
                drivers=_module_complexity_drivers(
                    len(module.forms),
                    len(module.query_artifacts),
                    event_steps,
                    unresolved_placeholders,
                    len(module.risks),
                ),
            )
        )

    total_unresolved = sum(item.unresolved_placeholders for item in module_scores)
    avg_module_score = round(
        sum(item.score for item in module_scores) / max(1, len(module_scores))
    )
    project_score = min(
        100,
        avg_module_score
        + len(output.transition_mapping.cross_cutting_concerns) * 5
        + len([item for item in output.diagnostics if item.severity in {"error", "fatal"}]) * 6
        + min(total_unresolved, 10) * 2
        + min(
            len(output.inventory.missing_search_paths) + len(output.inventory.unresolved_search_paths),
            5,
        )
        * 4,
    )
    module_scores.sort(key=lambda item: item.score, reverse=True)
    executive_summary = _executive_summary(output, module_scores, project_score)
    migration_recommendations = _migration_recommendations(output, module_scores)
    return ComplexityReport(
        project_score=project_score,
        level=_complexity_level(project_score),
        total_forms=len(output.forms),
        total_units=len(output.pascal_units),
        total_queries=len(output.resolved_queries),
        total_business_flows=len(output.business_flows),
        total_diagnostics=len(output.diagnostics),
        total_unresolved_placeholders=total_unresolved,
        module_scores=module_scores,
        executive_summary=executive_summary,
        migration_recommendations=migration_recommendations,
    )


def build_boss_summary_markdown(output: AnalysisOutput) -> str:
    report = output.complexity_report
    if report is None:
        raise ValueError("output.complexity_report must be set before building the boss summary.")
    top_modules = report.module_scores[:5]
    ready_specs = [
        item for item in output.transition_specs if item.readiness_level == "ready"
    ]
    blocked_specs = [
        item for item in output.transition_specs if item.readiness_level == "blocked"
    ]
    first_slices = [
        f"{item.module_name}: {item.recommended_first_slice}"
        for item in sorted(output.transition_specs, key=lambda value: value.readiness_score, reverse=True)[:3]
    ]
    prompt_report = output.prompt_effectiveness_report
    prompt_summary = (
        _bullet_lines(prompt_report.management_summary)
        if prompt_report is not None
        else "- No prompt feedback has been imported yet."
    )
    return f"""# Executive Summary

## Project Complexity

- Overall complexity: {report.level.upper()} ({report.project_score}/100)
- Forms: {report.total_forms}
- Pascal units: {report.total_units}
- SQL queries: {report.total_queries}
- Business flows recovered: {report.total_business_flows}
- Diagnostics: {report.total_diagnostics}
- Unresolved placeholders: {report.total_unresolved_placeholders}

## What To Tell Leadership

{_bullet_lines(report.executive_summary)}

## Highest-Complexity Modules

{_bullet_lines([f"{item.module_name}: {item.level} ({item.score}/100)" for item in top_modules])}

## Transition Readiness

- Ready modules: {len(ready_specs)}
- Blocked modules: {len(blocked_specs)}
- Transition specs generated: {len(output.transition_specs)}
- Backend SQL handoff artifacts: {len(output.bff_sql_artifacts)}
- UI pseudo/reference artifacts: {len(output.ui_pseudo_artifacts)}/{len(output.ui_reference_artifacts)}
- UI integration artifacts: {len(output.ui_integration_artifacts)}

## First Slices

{_bullet_lines(first_slices)}

## Recommended Migration Strategy

{_bullet_lines(report.migration_recommendations)}

## Prompt Effectiveness

{prompt_summary}
"""


def build_web_report_html(output: AnalysisOutput) -> str:
    report = output.complexity_report
    if report is None:
        raise ValueError("output.complexity_report must be set before rendering the web report.")
    payload = {
        "complexity_report": report,
        "transition_mapping": output.transition_mapping,
        "transition_specs": output.transition_specs,
        "bff_sql_artifacts": output.bff_sql_artifacts,
        "ui_pseudo_artifacts": output.ui_pseudo_artifacts,
        "ui_reference_artifacts": output.ui_reference_artifacts,
        "ui_integration_artifacts": output.ui_integration_artifacts,
        "load_bundles": output.load_bundles,
        "prompt_packs": output.prompt_packs,
        "failure_triage": output.failure_triage,
        "prompt_effectiveness_report": output.prompt_effectiveness_report,
        "runtime_state": output.runtime_state,
        "blocking_unknowns": output.blocking_unknowns,
        "artifact_completeness": output.artifact_completeness,
        "runtime_error_summary": output.runtime_error_summary,
        "provider_health": output.provider_health,
        "review_summary": output.review_summary,
        "diagnostic_count": len(output.diagnostics),
    }
    data_json = json.dumps(to_jsonable(payload), ensure_ascii=False)
    summary_cards = [
        ("Project Score", f"{report.project_score}/100", report.level.upper()),
        ("Forms", str(report.total_forms), "UI surfaces"),
        ("Queries", str(report.total_queries), "SQL artifacts"),
        ("Flows", str(report.total_business_flows), "Recovered flows"),
        ("Transition Specs", str(len(output.transition_specs)), "Module-ready plans"),
        (
            "Ready Modules",
            str(len([item for item in output.transition_specs if item.readiness_level == 'ready'])),
            "Low-friction first slices",
        ),
        ("External Roots", str(len(output.inventory.external_roots)), "Shared legacy repos"),
        ("Missing Paths", str(len(output.inventory.missing_search_paths)), "Workspace gaps"),
        ("Prompt Packs", str(len(output.prompt_packs)), "Model-ready tasks"),
        ("BFF SQL", str(len(output.bff_sql_artifacts)), "Oracle handoff slices"),
        ("Pseudo UI", str(len(output.ui_pseudo_artifacts)), "Page-level UI plans"),
        ("UI Integration", str(len(output.ui_integration_artifacts)), "React project handoff"),
        (
            "Runtime Blockers",
            str(len(output.blocking_unknowns)),
            "Current task queue",
        ),
        (
            "Runtime Errors",
            str((output.runtime_error_summary or {}).get("item_count", 0)),
            "Recovery hints",
        ),
        (
            "Prompt Success",
            (
                f"{int(round((output.prompt_effectiveness_report.accepted_entries / max(1, output.prompt_effectiveness_report.total_feedback_entries)) * 100))}%"
                if output.prompt_effectiveness_report and output.prompt_effectiveness_report.total_feedback_entries
                else "N/A"
            ),
            "Accepted prompt outcomes",
        ),
        ("Failure Cases", str(len(output.failure_triage)), "Minimal repro bundles"),
        ("Diagnostics", str(report.total_diagnostics), "Warnings + errors"),
        ("Unresolved", str(report.total_unresolved_placeholders), "Legacy placeholders"),
    ]
    summary_markup = "\n".join(
        f"""
        <article class="metric-card">
          <div class="metric-label">{escape(label)}</div>
          <div class="metric-value">{escape(value)}</div>
          <div class="metric-note">{escape(note)}</div>
        </article>
        """
        for label, value, note in summary_cards
    )
    module_rows = "\n".join(
        f"""
        <tr>
          <td>{escape(item.module_name)}</td>
          <td><span class="level level-{item.level}">{escape(item.level.upper())}</span></td>
          <td>{item.score}</td>
          <td>{item.forms}</td>
          <td>{item.queries}</td>
          <td>{item.event_steps}</td>
          <td>{item.unresolved_placeholders}</td>
          <td>{escape(', '.join(item.drivers) or 'None')}</td>
        </tr>
        """
        for item in report.module_scores
    )
    bundle_cards = "\n".join(
        _bundle_card_markup(bundle)
        for bundle in output.load_bundles
    )
    recommendations = "\n".join(
        f"<li>{escape(item)}</li>" for item in report.migration_recommendations
    )
    leadership_points = "\n".join(
        f"<li>{escape(item)}</li>" for item in report.executive_summary
    )
    transition_spec_rows = "\n".join(
        f"""
        <tr>
          <td>{escape(item.module_name)}</td>
          <td><span class="level level-{_readiness_badge(item.readiness_level)}">{escape(item.readiness_level.upper())}</span></td>
          <td>{item.readiness_score}</td>
          <td>{escape(item.migration_strategy)}</td>
          <td>{escape(item.recommended_first_slice)}</td>
        </tr>
        """
        for item in sorted(output.transition_specs, key=lambda value: value.readiness_score, reverse=True)
    )
    bff_rows = "\n".join(
        f"""
        <tr>
          <td>{escape(item.module_name)}</td>
          <td>{escape(item.endpoint_name)}</td>
          <td>{escape(item.query_name)}</td>
          <td>{escape(f"{item.http_method} {item.route_path}")}</td>
          <td>{escape(item.request_dto or 'None')}</td>
          <td>{escape(item.response_dto or 'None')}</td>
        </tr>
        """
        for item in output.bff_sql_artifacts
    )
    ui_rows = "\n".join(
        f"""
        <tr>
          <td>{escape(item.module_name)}</td>
          <td>{escape(item.page_name)}</td>
          <td>{escape(item.route_path)}</td>
          <td>{escape(', '.join(item.api_dependencies) or 'None')}</td>
          <td>{escape(item.target_feature_dir)}</td>
        </tr>
        """
        for item in output.ui_integration_artifacts
    )
    prompt_effectiveness = output.prompt_effectiveness_report
    runtime_state = output.runtime_state
    blocking_unknowns = list(output.blocking_unknowns or [])
    runtime_errors = output.runtime_error_summary or {"items": []}
    provider_health = output.provider_health or {}
    review_summary = output.review_summary or {"total_reviews": 0, "counts_by_decision": {}, "recent_reviews": []}
    prompt_rows = ""
    prompt_summary_markup = "<li>No prompt feedback has been imported yet.</li>"
    if prompt_effectiveness is not None:
        prompt_rows = "\n".join(
            f"""
            <tr>
              <td>{escape(item.prompt_name)}</td>
              <td>{escape(item.goal)}</td>
              <td>{item.attempts}</td>
              <td>{item.accepted}</td>
              <td>{item.rejected}</td>
              <td>{item.needs_follow_up}</td>
              <td>{item.fallback_uses}</td>
              <td>{item.success_rate:.3f}</td>
            </tr>
            """
            for item in prompt_effectiveness.top_failing_prompts + [
                item
                for item in prompt_effectiveness.top_successful_prompts
                if item.prompt_name not in {row.prompt_name for row in prompt_effectiveness.top_failing_prompts}
            ]
        )
        prompt_summary_markup = "\n".join(
            f"<li>{escape(item)}</li>" for item in prompt_effectiveness.management_summary
        ) or "<li>No prompt feedback has been imported yet.</li>"
    task_queue_rows = "\n".join(
        f"""
        <tr>
          <td>{escape(item.task_id)}</td>
          <td>{escape(item.task_type)}</td>
          <td>{escape(item.module_name or 'None')}</td>
          <td>{escape(item.subject_name or 'None')}</td>
          <td>{item.priority}</td>
          <td>{escape(item.reason or 'None')}</td>
        </tr>
        """
        for item in blocking_unknowns[:12]
    ) or """
        <tr><td colspan="6">No active blockers.</td></tr>
    """
    runtime_error_rows = "\n".join(
        f"""
        <tr>
          <td>{escape(str(item.get('code') or 'UNKNOWN'))}</td>
          <td>{escape(str(item.get('severity') or 'unknown'))}</td>
          <td>{escape(str(item.get('task_id') or 'n/a'))}</td>
          <td>{escape(str(item.get('title') or 'Unknown'))}</td>
          <td>{escape(str(item.get('next_best_action') or 'None'))}</td>
        </tr>
        """
        for item in runtime_errors.get("items", [])[:12]
        if isinstance(item, dict)
    ) or """
        <tr><td colspan="5">No runtime errors recorded.</td></tr>
    """
    provider_health_markup = "\n".join(
        [
            f"<li>Models OK: {escape(str(provider_health.get('models_ok')).lower())}</li>",
            f"<li>Completion OK: {escape(str(provider_health.get('completion_ok')).lower())}</li>",
            f"<li>Selected model: {escape(str(provider_health.get('selected_model') or 'None'))}</li>",
            f"<li>Response format: {escape(str(provider_health.get('response_format') or 'unknown'))}</li>",
            f"<li>Content type: {escape(str(provider_health.get('response_content_type') or 'unknown'))}</li>",
        ]
    ) if provider_health else "<li>No provider health probe recorded yet.</li>"
    review_markup = "\n".join(
        f"<li>{escape(str(key))}: {value}</li>"
        for key, value in sorted((review_summary.get("counts_by_decision") or {}).items())
    ) or "<li>No human reviews recorded yet.</li>"
    runtime_summary_markup = "\n".join(
        [
            f"<li>Status: {escape(runtime_state.status)}</li>" if runtime_state else "<li>Status: unknown</li>",
            f"<li>Current phase: {escape(runtime_state.current_phase)}</li>" if runtime_state else "<li>Current phase: unknown</li>",
            f"<li>Dispatch mode: {escape(runtime_state.dispatch_mode)}</li>" if runtime_state else "<li>Dispatch mode: unknown</li>",
            f"<li>Blocking task: {escape(runtime_state.blocking_task_id or 'None')}</li>" if runtime_state else "<li>Blocking task: None</li>",
        ]
    )
    return f"""<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Legacy Delphi Complexity Report</title>
    <style>
      :root {{
        --bg: #f4efe5;
        --panel: rgba(255, 252, 246, 0.88);
        --panel-strong: rgba(250, 244, 234, 0.96);
        --ink: #172026;
        --muted: #59656e;
        --line: rgba(23, 32, 38, 0.12);
        --accent: #0c7c59;
        --warn: #d17a22;
        --critical: #ad343e;
        --shadow: 0 20px 60px rgba(23, 32, 38, 0.12);
      }}
      * {{ box-sizing: border-box; }}
      body {{
        margin: 0;
        color: var(--ink);
        background:
          radial-gradient(circle at top left, rgba(12, 124, 89, 0.18), transparent 28%),
          radial-gradient(circle at top right, rgba(173, 52, 62, 0.1), transparent 26%),
          linear-gradient(135deg, #f7f1e7 0%, #efe4d2 48%, #f2ebdf 100%);
        font-family: "Avenir Next", "Trebuchet MS", "Segoe UI", sans-serif;
      }}
      .shell {{
        max-width: 1280px;
        margin: 0 auto;
        padding: 32px 24px 56px;
      }}
      .hero {{
        padding: 28px;
        border: 1px solid var(--line);
        border-radius: 28px;
        background: linear-gradient(135deg, rgba(12, 124, 89, 0.1), rgba(255, 252, 246, 0.92));
        box-shadow: var(--shadow);
      }}
      .eyebrow {{
        margin: 0 0 10px;
        color: var(--accent);
        letter-spacing: 0.18em;
        font-size: 12px;
        text-transform: uppercase;
      }}
      h1, h2 {{
        font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
        margin: 0 0 12px;
      }}
      h1 {{ font-size: clamp(36px, 6vw, 64px); line-height: 0.95; }}
      h2 {{ font-size: 28px; }}
      .hero p {{
        max-width: 760px;
        font-size: 17px;
        line-height: 1.6;
        color: var(--muted);
      }}
      .metric-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 16px;
        margin-top: 28px;
      }}
      .metric-card, .panel {{
        border: 1px solid var(--line);
        background: var(--panel);
        border-radius: 22px;
        padding: 18px 18px 20px;
        box-shadow: var(--shadow);
        backdrop-filter: blur(12px);
      }}
      .metric-label {{
        font-size: 12px;
        letter-spacing: 0.1em;
        color: var(--muted);
        text-transform: uppercase;
      }}
      .metric-value {{
        margin-top: 10px;
        font-size: 32px;
        font-weight: 700;
      }}
      .metric-note {{
        margin-top: 6px;
        color: var(--muted);
      }}
      .grid {{
        display: grid;
        grid-template-columns: 1.15fr 0.85fr;
        gap: 20px;
        margin-top: 24px;
      }}
      .panel ul {{
        margin: 0;
        padding-left: 18px;
        line-height: 1.7;
      }}
      .table-wrap {{
        overflow-x: auto;
      }}
      table {{
        width: 100%;
        border-collapse: collapse;
        font-size: 14px;
      }}
      th, td {{
        text-align: left;
        padding: 12px 10px;
        border-bottom: 1px solid var(--line);
        vertical-align: top;
      }}
      th {{
        color: var(--muted);
        font-weight: 600;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        font-size: 11px;
      }}
      .level {{
        display: inline-flex;
        border-radius: 999px;
        padding: 4px 10px;
        font-size: 12px;
        letter-spacing: 0.08em;
      }}
      .level-low {{ background: rgba(12, 124, 89, 0.12); color: var(--accent); }}
      .level-medium {{ background: rgba(209, 122, 34, 0.16); color: #8a5418; }}
      .level-high {{ background: rgba(173, 52, 62, 0.14); color: #8d2630; }}
      .level-critical {{ background: rgba(23, 32, 38, 0.14); color: var(--ink); }}
      .bundle-grid {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
        gap: 16px;
      }}
      .bundle-card {{
        border: 1px solid var(--line);
        border-radius: 20px;
        background: var(--panel-strong);
        padding: 18px;
      }}
      .bundle-card h3 {{
        margin: 0 0 10px;
        font-size: 20px;
        font-family: "Iowan Old Style", "Palatino Linotype", serif;
      }}
      .bundle-card p {{
        margin: 8px 0;
        color: var(--muted);
        line-height: 1.5;
      }}
      .stack {{
        display: grid;
        gap: 20px;
        margin-top: 24px;
      }}
      footer {{
        margin-top: 28px;
        color: var(--muted);
        font-size: 13px;
        text-align: center;
      }}
      @media (max-width: 960px) {{
        .grid {{ grid-template-columns: 1fr; }}
      }}
    </style>
  </head>
  <body>
    <div class="shell">
      <section class="hero">
        <p class="eyebrow">Legacy Delphi Transition Report</p>
        <h1>Complexity Dashboard</h1>
        <p>
          This report summarizes migration complexity, the most expensive legacy modules,
          and the smallest first slices to move toward a React + Spring Boot transition.
        </p>
        <div class="metric-grid">
          {summary_markup}
        </div>
      </section>

      <section class="grid">
        <article class="panel">
          <h2>Leadership Summary</h2>
          <ul>{leadership_points}</ul>
        </article>
        <article class="panel">
          <h2>Migration Recommendations</h2>
          <ul>{recommendations}</ul>
        </article>
      </section>

      <section class="grid">
        <article class="panel">
          <h2>Prompt Effectiveness Summary</h2>
          <ul>{prompt_summary_markup}</ul>
        </article>
        <article class="panel">
          <h2>Prompt Closure Health</h2>
          <ul>
            <li>Total prompt packs: {len(output.prompt_packs)}</li>
            <li>Feedback entries: {prompt_effectiveness.total_feedback_entries if prompt_effectiveness else 0}</li>
            <li>Fallback uses: {prompt_effectiveness.fallback_entries if prompt_effectiveness else 0}</li>
          </ul>
        </article>
      </section>

      <section class="grid">
        <article class="panel">
          <h2>Runtime Workbench</h2>
          <ul>{runtime_summary_markup}</ul>
          <ul>
            <li>Fast path: open <code>runtime/cline-cheat-sheet.md</code>.</li>
            <li>Per task, use only <code>agent-task.md</code>, <code>compiled-context.md</code>, and <code>agent-expected-output-schema.json</code>.</li>
            <li>Save JSON to <code>agent-response.json</code>, then run <code>validate-response</code>.</li>
          </ul>
        </article>
        <article class="panel">
          <h2>Provider Health</h2>
          <ul>{provider_health_markup}</ul>
          <ul>
            <li>Use <code>validate-provider --analysis-dir ... --verbose</code> after provider changes.</li>
            <li>If the provider reports SSE, prefer the bundled wrapper streaming mode.</li>
          </ul>
        </article>
      </section>

      <section class="panel stack">
        <div>
          <h2>Task Queue</h2>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Task ID</th>
                  <th>Type</th>
                  <th>Module</th>
                  <th>Subject</th>
                  <th>Priority</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {task_queue_rows}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section class="grid">
        <article class="panel">
          <h2>Runtime Errors</h2>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Severity</th>
                  <th>Task</th>
                  <th>Title</th>
                  <th>Next Action</th>
                </tr>
              </thead>
              <tbody>
                {runtime_error_rows}
              </tbody>
            </table>
          </div>
        </article>
        <article class="panel">
          <h2>Human Review</h2>
          <ul>{review_markup}</ul>
          <ul>
            <li>Use <code>review-task</code> to accept, reject, trim, or escalate a task output.</li>
            <li>Accepted reviews can be folded back into feedback learning for later runs.</li>
          </ul>
        </article>
      </section>

      <section class="panel stack">
        <div>
          <h2>Module Complexity</h2>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Module</th>
                  <th>Level</th>
                  <th>Score</th>
                  <th>Forms</th>
                  <th>Queries</th>
                  <th>Flow Steps</th>
                  <th>Unresolved</th>
                  <th>Drivers</th>
                </tr>
              </thead>
              <tbody>
                {module_rows}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section class="panel stack">
        <div>
          <h2>Transition Specs</h2>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Module</th>
                  <th>Readiness</th>
                  <th>Score</th>
                  <th>Strategy</th>
                  <th>First Slice</th>
                </tr>
              </thead>
              <tbody>
                {transition_spec_rows or '<tr><td colspan="5">No transition specs generated yet.</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <section class="grid">
        <article class="panel">
          <h2>Backend SQL Handoff</h2>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Module</th>
                  <th>Endpoint</th>
                  <th>Query</th>
                  <th>Route</th>
                  <th>Request DTO</th>
                  <th>Response DTO</th>
                </tr>
              </thead>
              <tbody>
                {bff_rows or '<tr><td colspan="6">No backend SQL handoff artifacts generated yet.</td></tr>'}
              </tbody>
            </table>
          </div>
        </article>
        <article class="panel">
          <h2>UI Integration Handoff</h2>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Module</th>
                  <th>Page</th>
                  <th>Route</th>
                  <th>APIs</th>
                  <th>Feature Dir</th>
                </tr>
              </thead>
              <tbody>
                {ui_rows or '<tr><td colspan="5">No UI integration artifacts generated yet.</td></tr>'}
              </tbody>
            </table>
          </div>
        </article>
      </section>

      <section class="panel stack">
        <div>
          <h2>LLM Work Bundles</h2>
          <div class="bundle-grid">
            {bundle_cards}
          </div>
        </div>
      </section>

      <section class="panel stack">
        <div>
          <h2>Prompt Effectiveness</h2>
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Prompt</th>
                  <th>Goal</th>
                  <th>Attempts</th>
                  <th>Accepted</th>
                  <th>Rejected</th>
                  <th>Follow-up</th>
                  <th>Fallback</th>
                  <th>Success Rate</th>
                </tr>
              </thead>
              <tbody>
                {prompt_rows or '<tr><td colspan="8">No prompt feedback has been imported yet.</td></tr>'}
              </tbody>
            </table>
          </div>
        </div>
      </section>

      <footer>
        Generated by legacy-delphi-project-analyzer
      </footer>
    </div>
    <script id="report-data" type="application/json">{escape(data_json)}</script>
  </body>
</html>
"""


def _complexity_level(score: int) -> str:
    if score >= 75:
        return "critical"
    if score >= 50:
        return "high"
    if score >= 25:
        return "medium"
    return "low"


def _module_complexity_drivers(
    forms: int,
    queries: int,
    event_steps: int,
    unresolved_placeholders: int,
    risks: int,
) -> list[str]:
    drivers = []
    if queries >= 3:
        drivers.append("heavy SQL surface")
    if event_steps >= 3:
        drivers.append("event-driven UI flow")
    if unresolved_placeholders > 0:
        drivers.append("runtime SQL mutation")
    if forms > 1:
        drivers.append("multi-form coupling")
    if risks > 1:
        drivers.append("multiple migration risks")
    return drivers or ["limited surface area"]


def _executive_summary(
    output: AnalysisOutput,
    module_scores: list[ModuleComplexityScore],
    project_score: int,
) -> list[str]:
    points = [
        f"Overall migration complexity is {_complexity_level(project_score)} at {project_score}/100.",
        f"The project contains {len(output.forms)} forms, {len(output.resolved_queries)} SQL artifacts, and {len(output.business_flows)} recovered business flows.",
    ]
    if output.inventory.external_roots:
        points.append(
            f"The analyzer depends on {len(output.inventory.external_roots)} external scan roots beyond the main repo."
        )
    if output.inventory.missing_search_paths or output.inventory.unresolved_search_paths:
        points.append(
            "Workspace path gaps remain: "
            f"{len(output.inventory.missing_search_paths)} missing and "
            f"{len(output.inventory.unresolved_search_paths)} unresolved search paths."
        )
    if module_scores:
        highest = module_scores[0]
        lowest = module_scores[-1]
        points.append(
            f"The hardest module is {highest.module_name} ({highest.score}/100), driven by {', '.join(highest.drivers[:2])}."
        )
        if len(module_scores) == 1:
            points.append(
                f"The first migration slice should still start with {highest.module_name}, but only after documenting its runtime SQL replacement assumptions."
            )
        else:
            points.append(
                f"The most practical first migration slice is {lowest.module_name} ({lowest.score}/100) if business priority allows it."
            )
    if output.transition_specs:
        best_ready = max(output.transition_specs, key=lambda item: item.readiness_score)
        points.append(
            f"The strongest transition candidate today is {best_ready.module_name} ({best_ready.readiness_level}, {best_ready.readiness_score}/100)."
        )
    if output.transition_mapping.cross_cutting_concerns:
        points.append(
            "Cross-cutting legacy concerns still visible: "
            + ", ".join(output.transition_mapping.cross_cutting_concerns[:3])
            + "."
        )
    return points


def _migration_recommendations(
    output: AnalysisOutput,
    module_scores: list[ModuleComplexityScore],
) -> list[str]:
    recommendations = []
    if module_scores:
        lowest = module_scores[-1]
        recommendations.append(
            f"Start with module {lowest.module_name} to establish the React/Spring migration template."
        )
        highest = module_scores[0]
        if len(module_scores) > 1 and highest.module_name != lowest.module_name:
            recommendations.append(
                f"Treat module {highest.module_name} as a later-phase migration and isolate its SQL/runtime replacement rules first."
            )
    if any(item.unresolved_placeholders for item in module_scores):
        recommendations.append(
            "Prioritize documenting Delphi-side placeholder replacement before locking backend API contracts."
        )
    if output.inventory.missing_search_paths or output.inventory.unresolved_search_paths:
        recommendations.append(
            "Stabilize the Delphi workspace first by resolving external search paths before estimating module-level migration scope."
        )
    if output.diagnostics:
        recommendations.append(
            "Use diagnostics and prompt recipes as a required review gate before estimating delivery dates."
        )
    ready_specs = [item for item in output.transition_specs if item.readiness_level == "ready"]
    if ready_specs:
        recommendations.append(
            f"Use the generated transition spec for {ready_specs[0].module_name} as the baseline template for later modules."
        )
    return recommendations


def _bundle_card_markup(bundle: LoadBundleArtifact) -> str:
    return f"""
    <article class="bundle-card">
      <h3>{escape(bundle.name)}</h3>
      <p><strong>Category:</strong> {escape(bundle.category)}</p>
      <p><strong>Estimated Tokens:</strong> {bundle.estimated_tokens}</p>
      <p><strong>Prompt:</strong> {escape(bundle.recommended_prompt or 'None')}</p>
      <p><strong>Artifacts:</strong> {len(bundle.artifact_paths)}</p>
    </article>
    """


def _bullet_lines(values: list[str]) -> str:
    if not values:
        return "- None"
    return "\n".join(f"- {item}" for item in values)


def _readiness_badge(value: str) -> str:
    mapping = {
        "ready": "low",
        "needs-clarification": "medium",
        "blocked": "high",
    }
    return mapping.get(value, "critical")
