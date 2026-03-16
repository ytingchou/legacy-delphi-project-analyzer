from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.orchestrator import run_phases
from legacy_delphi_project_analyzer.oracle_bff import compile_oracle_bff_sql


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class OracleBffCompilerTests(unittest.TestCase):
    def test_compile_oracle_bff_sql_writes_manifest_and_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output = run_phases(
                project_root=FIXTURE_ROOT,
                output_dir=Path(tmpdir) / "artifacts",
                target_model_profile="qwen3_128k_weak",
                dispatch_mode="manual",
            )
            manifest = compile_oracle_bff_sql(Path(output.output_dir))
            self.assertEqual(manifest["summary"]["entry_count"], 1)
            entry = manifest["entries"][0]
            self.assertEqual(entry["operation_kind"], "read")
            self.assertTrue(entry["semantic_checks"])
            self.assertTrue((Path(output.output_dir) / "llm-pack" / "bff-sql-compiler" / "oracle-bff-manifest.json").exists())


if __name__ == "__main__":
    unittest.main()
