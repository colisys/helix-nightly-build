import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from helix_nightly import i18n

SHA = "a" * 40


class TranslationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.file = self.root / "helix-term/src/commands.rs"
        self.file.parent.mkdir(parents=True)
        self.lines = ['    editor.set_status("Ready");', '    editor.set_error("Oops");']
        self.original = "\n".join(self.lines) + "\n"
        self.file.write_text(self.original, encoding="utf-8")
        self.entries = [self.entry(line, word, translated) for line, word, translated in
                        zip(self.lines, ("Ready", "Oops"), ("就绪", "错误"))]
        self.data = {"language": "zh-CN", "upstream_commit": SHA, "entries": self.entries}
        self.table = self.root / "table.json"
        self.save()

    def entry(self, line, word, translated):
        return {"path": "helix-term/src/commands.rs", "line": line,
                "source": json.dumps(word), "context": "editor UI status message",
                "translation": translated, "approved": True}

    def save(self):
        self.table.write_text(json.dumps(self.data), encoding="utf-8")

    def load(self):
        with patch.object(i18n.subprocess, "check_output", return_value=SHA):
            return i18n.load(self.table, self.root, approved=True)

    def test_multiple_entries_restore_and_isolation(self):
        originals = i18n.apply(self.root, self.load())
        self.assertIn("就绪", self.file.read_text())
        self.assertIn("错误", self.file.read_text())
        i18n.restore(originals)
        self.assertEqual(self.file.read_text(), self.original)
        self.data["language"] = "fr-FR"
        self.data["entries"][0]["translation"] = "Prêt"
        self.save()
        originals = i18n.apply(self.root, self.load())
        self.assertIn("Prêt", self.file.read_text())
        i18n.restore(originals)
        self.assertEqual(self.file.read_text(), self.original)

    def test_modified_during_build_not_overwritten(self):
        originals = i18n.apply(self.root, self.load())
        self.file.write_text("user edit\n")
        with self.assertRaises(RuntimeError):
            i18n.restore(originals)
        self.assertEqual(self.file.read_text(), "user edit\n")

    def test_missing_or_duplicate_line_rejected(self):
        self.file.write_text(self.original + self.lines[0] + "\n")
        with self.assertRaises(ValueError):
            self.load()
        self.file.write_text("other code\n")
        with self.assertRaises(ValueError):
            self.load()

    def test_path_traversal_rejected(self):
        for path in ("helix-term/src/../commands.rs", "helix-term/src//commands.rs",
                     "/helix-term/src/commands.rs", "helix-term/src/./commands.rs"):
            self.data["entries"][0]["path"] = path
            self.save()
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.load()

    def test_approval_and_format_rejected(self):
        self.data["entries"][0]["approved"] = False
        self.save()
        with self.assertRaises(ValueError):
            self.load()
        self.data["entries"][0]["approved"] = True
        self.data["entries"][0]["translation"] = "{oops}"
        self.save()
        with self.assertRaises(ValueError):
            self.load()

    def test_machine_table_is_not_human_approved(self):
        self.data["generated_by"] = "auto_translate"
        for entry in self.data["entries"]:
            entry["approved"] = False
        self.save()
        with self.assertRaises(ValueError):
            self.load()
        with patch.object(i18n.subprocess, "check_output", return_value=SHA):
            machine = i18n.load(self.table, self.root, approved=True, allow_machine=True)
        self.assertTrue(all(entry["approved"] is False for entry in machine["entries"]))
        self.data.pop("generated_by")
        self.save()
        with patch.object(i18n.subprocess, "check_output", return_value=SHA):
            with self.assertRaises(ValueError):
                i18n.load(self.table, self.root, approved=True, allow_machine=True)
        self.data["generated_by"] = "auto_translate"
        self.data["entries"][0]["approved"] = True
        self.save()
        with patch.object(i18n.subprocess, "check_output", return_value=SHA):
            with self.assertRaises(ValueError):
                i18n.load(self.table, self.root, approved=True, allow_machine=True)

    def test_commit_mismatch_rejected(self):
        with patch.object(i18n.subprocess, "check_output", return_value="b" * 40):
            with self.assertRaises(ValueError):
                i18n.load(self.table, self.root, approved=True)

    def test_extract_from_explicit_file(self):
        dest = self.root / "selected.json"
        with patch.object(i18n.subprocess, "check_output", return_value=SHA):
            i18n.extract(self.root, dest, "helix-term/src/commands.rs", "Ready", "editor status")
        self.assertEqual(json.loads(dest.read_text())["entries"][0]["line"], self.lines[0])
        with self.assertRaises(FileExistsError):
            i18n.extract(self.root, dest, "helix-term/src/commands.rs", "Ready", "editor status")


if __name__ == "__main__":
    unittest.main()
