from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.orchestrator import run_phases
from legacy_delphi_project_analyzer.workspace_graph import build_workspace_graph


class WorkspaceGraphTests(unittest.TestCase):
    def test_build_workspace_graph_tracks_external_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            project_root = root / "main_project"
            common_root = root / "PDSS_Common"
            project_root.mkdir(parents=True, exist_ok=True)
            common_root.mkdir(parents=True, exist_ok=True)

            (project_root / "entry.pas").write_text(
                """unit entry;
interface
uses System.SysUtils, SharedTypes;
implementation
end.
""",
                encoding="utf-8",
            )
            (common_root / "SharedTypes.pas").write_text(
                """unit SharedTypes;
interface
implementation
end.
""",
                encoding="utf-8",
            )
            workspace_config = project_root / "workspace.json"
            workspace_config.write_text(
                json.dumps(
                    {
                        "scan_roots": ["$(PDSS_COMMON)"],
                        "search_paths": ["$(PDSS_COMMON)"],
                        "path_variables": {
                            "PDSS_COMMON": "../PDSS_Common",
                        },
                    }
                ),
                encoding="utf-8",
            )

            output = run_phases(
                project_root=project_root,
                output_dir=root / "artifacts",
                workspace_config_path=workspace_config,
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            graph = build_workspace_graph(Path(output.output_dir))
            self.assertGreaterEqual(graph["summary"]["root_count"], 2)
            self.assertGreaterEqual(graph["summary"]["cross_root_edges"], 1)
            self.assertTrue((Path(output.output_dir) / "llm-pack" / "workspace-graph" / "workspace-graph.json").exists())


if __name__ == "__main__":
    unittest.main()
