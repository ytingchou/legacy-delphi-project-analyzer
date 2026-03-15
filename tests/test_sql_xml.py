from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from legacy_delphi_project_analyzer.analyzers.sql_xml import SqlXmlResolver, parse_sql_xml_file


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "sample_project"


class SqlXmlResolutionTests(unittest.TestCase):
    def test_resolves_internal_and_external_references(self) -> None:
        pricing, pricing_diags = parse_sql_xml_file(FIXTURE_ROOT / "pricing.xml", FIXTURE_ROOT)
        common, common_diags = parse_sql_xml_file(FIXTURE_ROOT / "common.xml", FIXTURE_ROOT)
        self.assertEqual(pricing_diags, [])
        self.assertEqual(common_diags, [])
        assert pricing is not None
        assert common is not None

        diagnostics = []
        resolver = SqlXmlResolver([pricing, common], diagnostics=diagnostics)
        artifacts = {
            (item.kind, item.name): item
            for item in resolver.resolve_all()
        }

        order_lookup = artifacts[("main-query", "OrderLookup")]
        self.assertIn("SELECT o.order_no", order_lookup.expanded_sql)
        self.assertIn("WHERE o.status = :status", order_lookup.expanded_sql)
        self.assertIn("AND o.customer_id = :customerId", order_lookup.expanded_sql)
        self.assertIn("fPriceCheckRule", order_lookup.unresolved_placeholders)

        raw_copy = artifacts[("main-query", "OrderLookupRawCopy")]
        self.assertNotIn("WHERE o.status = :status", raw_copy.expanded_sql)
        self.assertIn("SELECT o.order_no, o.customer_id", raw_copy.expanded_sql)
        self.assertEqual(diagnostics, [])

    def test_reports_duplicate_queries_and_invalid_copy_target(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            duplicate_xml = tmp / "duplicate.xml"
            duplicate_xml.write_text(
                """<sql-mapping>
  <main-query name="Dup">
    <sql-body><![CDATA[
SELECT 1 FROM dual
    ]]></sql-body>
  </main-query>
  <main-query name="Dup">
    <sql-body><![CDATA[
SELECT 2 FROM dual
    ]]></sql-body>
  </main-query>
  <ext-sql-copy xml="other.xml" sub-query="BaseWhere" />
</sql-mapping>
""",
                encoding="utf-8",
            )
            summary, diagnostics = parse_sql_xml_file(duplicate_xml, tmp)
            self.assertIsNotNone(summary)
            codes = {item.code for item in diagnostics}
            self.assertIn("SQL_XML_DUPLICATE_QUERY", codes)

            invalid_copy_xml = tmp / "invalid_copy.xml"
            invalid_copy_xml.write_text(
                """<sql-mapping>
  <main-query name="Wrapped">
    <ext-sql-copy xml="other.xml" sub-query="BaseWhere" />
  </main-query>
</sql-mapping>
""",
                encoding="utf-8",
            )
            _, invalid_diags = parse_sql_xml_file(invalid_copy_xml, tmp)
            self.assertTrue(
                any(item.code == "SQL_XML_COPY_SUBQUERY_UNSUPPORTED" for item in invalid_diags)
            )

    def test_resolves_external_alias_without_extension(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            external_xml = tmp / "shared_pricing.xml"
            external_xml.write_text(
                """<sql-mapping>
  <sub-query name="BaseWhere">
    <sql-body><![CDATA[
WHERE status = :status
    ]]></sql-body>
  </sub-query>
</sql-mapping>
""",
                encoding="utf-8",
            )
            entry_xml = tmp / "entry.xml"
            entry_xml.write_text(
                """<sql-mapping>
  <main-query name="UseAlias">
    <ext-sql-refer-to xml="pricing" sub-query="BaseWhere" />
  </main-query>
</sql-mapping>
""",
                encoding="utf-8",
            )
            shared_summary, shared_diags = parse_sql_xml_file(external_xml, tmp)
            entry_summary, entry_diags = parse_sql_xml_file(entry_xml, tmp)
            self.assertEqual(shared_diags, [])
            self.assertEqual(entry_diags, [])
            assert shared_summary is not None
            assert entry_summary is not None

            diagnostics = []
            resolver = SqlXmlResolver(
                [entry_summary, shared_summary],
                diagnostics=diagnostics,
                xml_aliases={"pricing": "shared_pricing.xml"},
            )
            artifact = resolver.resolve_query(entry_summary.xml_keys[0], "main-query", "usealias", [])
            self.assertIn("WHERE status = :status", artifact.expanded_sql)
            self.assertEqual(diagnostics, [])

    def test_detects_reference_cycles(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            cyclic_xml = tmp / "cyclic.xml"
            cyclic_xml.write_text(
                """<sql-mapping>
  <main-query name="A">
    <sql-refer-to main-query="B" />
  </main-query>
  <main-query name="B">
    <sql-refer-to main-query="A" />
  </main-query>
</sql-mapping>
""",
                encoding="utf-8",
            )
            summary, parse_diags = parse_sql_xml_file(cyclic_xml, tmp)
            self.assertEqual(parse_diags, [])
            assert summary is not None
            diagnostics = []
            resolver = SqlXmlResolver([summary], diagnostics=diagnostics)
            artifacts = resolver.resolve_all()
            self.assertTrue(any(item.code == "SQL_XML_CYCLE" for item in diagnostics))
            self.assertTrue(any("cyclic reference" in artifact.expanded_sql for artifact in artifacts))
            self.assertTrue(any("cycle_chain" in item.details for item in diagnostics))

    def test_parameter_name_is_optional_colon_and_data_type_is_not_validated(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            xml_path = tmp / "params.xml"
            xml_path.write_text(
                """<sql-mapping>
  <main-query name="FlexibleParams">
    <parameter name="status" data_type="CustomStatusType" />
    <parameter name=":customerId" data_type="WhateverType" />
    <sql-body><![CDATA[
SELECT *
FROM orders
WHERE status = :status
  AND customer_id = :customerId
    ]]></sql-body>
  </main-query>
</sql-mapping>
""",
                encoding="utf-8",
            )

            summary, diagnostics = parse_sql_xml_file(xml_path, tmp)
            self.assertEqual(diagnostics, [])
            assert summary is not None

            diagnostics = []
            resolver = SqlXmlResolver([summary], diagnostics=diagnostics)
            artifact = resolver.resolve_query(summary.xml_keys[0], "main-query", "flexibleparams", [])

            self.assertEqual(diagnostics, [])
            self.assertEqual(
                [item.name for item in artifact.parameter_definitions],
                ["status", "customerId"],
            )
            self.assertEqual(artifact.unresolved_placeholders, [])


if __name__ == "__main__":
    unittest.main()
