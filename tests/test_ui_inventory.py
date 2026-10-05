import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from helix_nightly.ui_inventory import inventory


class InventoryTests(unittest.TestCase):
    def test_reports_candidates_without_translating_external_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for crate in ("helix-term", "helix-view", "helix-core", "helix-tui"):
                (root / crate / "src").mkdir(parents=True)
            (root / "helix-term/src/commands.rs").write_text(
                'editor.set_error("No more matches");\n'
                'editor.set_error(message); // supplied by LSP\n'
                'editor.set_status(format!("Saved: {}", file));\n'
                'PickerColumn::new("name", |item, _| item.name)\n', encoding="utf-8")
            report = inventory(root)
            self.assertEqual(report["selected_entries"], 1)
            self.assertEqual(report["call_site_counts"]["error"],
                             {"selected_line": 1, "review_needed": 1})
            self.assertEqual(report["call_site_counts"]["status"]["review_needed"], 1)
            self.assertEqual(report["call_site_counts"]["picker_column"]["review_needed"], 1)
            self.assertIn("not a complete UI coverage", report["note"])
            self.assertTrue(any("supplied by LSP" in item["line"] and
                                item["status"] == "review_needed"
                                for item in report["candidates"]))


if __name__ == "__main__":
    unittest.main()
