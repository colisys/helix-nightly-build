import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from helix_nightly import auto_translate

SHA = "a" * 40


class Response:
    def __init__(self, content):
        self.content = content

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps({"choices": [{"message": {"content": self.content}}]}).encode()


class BatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        folder = self.root / "helix-term/src"
        folder.mkdir(parents=True)
        self.file = folder / "commands.rs"
        self.file.write_text('    editor.set_error("No more matches");\n'
                             '    cx.editor.set_status("Wrapped around document");\n'
                             '    editor.set_error(format!("danger {}", thing));\n'
                             '    let protocol = "No more matches";\n'
                             '    editor.set_error("duplicate");\n'
                             '    editor.set_error("duplicate");\n', encoding="utf-8")
        view = self.root / "helix-view/src"
        view.mkdir(parents=True)
        (view / "action.rs").write_text(
            '    editor.set_error("Language Server disappeared");\n'
            '    self.set_status("Debugger initialized...");\n', encoding="utf-8")
        (folder / "commands").mkdir()
        (folder / "commands" / "typed.rs").write_text(
            '    doc: "Write current file.",\n    name: "write",\n', encoding="utf-8")
        self.file.write_text(self.file.read_text(encoding="utf-8") +
                             '    static_commands!(\n        move_left, "Move left",\n    );\n',
                             encoding="utf-8")
        self.out = self.root / "out.json"

    def test_only_explicit_ui_sinks(self):
        entries = auto_translate.scan(self.root)
        self.assertEqual([json.loads(e["source"]) for e in entries],
                         ["Write current file.", "No more matches", "Wrapped around document", "Move left",
                          "Language Server disappeared", "Debugger initialized..."])
        self.assertNotIn('name: "write"', str(entries))

    def test_help_context_and_command_id_are_distinct(self):
        entries = auto_translate.scan(self.root)
        self.assertEqual(len(entries), 6)
        self.assertEqual(entries[0]["context"],
                         "Helix typable command or flag help description (preserve command names)")
        self.assertIn('"Move left"', [entry["source"] for entry in entries])
        self.assertNotIn('"move_left"', str(entries))

    def test_help_popup_titles_only_in_known_context(self):
        self.file.write_text(self.file.read_text(encoding="utf-8") +
                             '    cx.editor.autoinfo = Some(Info::new(\n'
                             '        "Surround selections with",\n'
                             '        &SURROUND_HELP_TEXT[1..],\n    ));\n'
                             '    cx.editor.autoinfo = Some(Info::new('
                             '"Delete surrounding pair of", &SURROUND_HELP_TEXT));\n'
                             '    let protocol = "Do not translate";\n', encoding="utf-8")
        entries = auto_translate.scan(self.root)
        self.assertEqual([e["source"] for e in entries if "popup title" in e["context"]],
                         ['"Surround selections with"', '"Delete surrounding pair of"'])
        self.assertNotIn('Do not translate', str(entries))

    def test_prompt_label_excludes_dynamic_and_external(self):
        commands = self.root / "helix-term/src/commands"
        (commands / "dap.rs").write_text(
            '    let prompt = Prompt::new(\n'
            '        "condition:".into(),\n'
            '        "protocol-name".into(),\n'
            '    );\n', encoding="utf-8")
        (commands / "lsp.rs").write_text(
            '    let prompt = ui::Prompt::new(\n'
            '        "rename-to:".into(),\n'
            '        external_message.into(),\n'
            '    );\n', encoding="utf-8")
        entries = auto_translate.scan(self.root)
        self.assertEqual([e["source"] for e in entries if "prompt label" in e["context"]],
                         ['"condition:"', '"rename-to:"'])
        self.assertNotIn('protocol-name', str(entries))

    def test_missing_secret_rejected(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(ValueError, "secret"):
                auto_translate.translate(self.root, self.out)
        self.assertFalse(self.out.exists())

    def test_valid_batch_and_source_anchor(self):
        answer = json.dumps({"translations": [
            {"id": 0, "text": "写入当前文件"}, {"id": 1, "text": "没有更多匹配项"},
            {"id": 2, "text": "已从文档另一端继续搜索"}, {"id": 3, "text": "向左移动"},
            {"id": 4, "text": "语言服务器已断开"}, {"id": 5, "text": "调试器已初始化"}]})
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test", "OPENAI_API_URL": "",
                                         "OPENAI_MODEL": ""}), \
             patch.object(auto_translate.subprocess, "check_output", return_value=SHA), \
             patch.object(auto_translate.urllib.request, "urlopen", return_value=Response(answer)) as request:
            self.assertEqual(auto_translate.translate(self.root, self.out), 6)
        self.assertTrue(request.called)
        data = json.loads(self.out.read_text())
        self.assertEqual(data["upstream_commit"], SHA)
        self.assertEqual(data["generated_by"], "auto_translate")
        self.assertEqual(len(data["entries"]), 6)
        self.assertTrue(all(entry["approved"] is False for entry in data["entries"]))

    def test_bad_batch_fails_without_output(self):
        for answer in ('{"translations": [{"id": 1, "text": "错"}]}',
                       json.dumps({"translations": [{"id": idx, "text": "{oops}" if idx == 0 else "对"}
                                                    for idx in range(6)]})):
            with self.subTest(answer=answer), patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), \
                 patch.object(auto_translate.subprocess, "check_output", return_value=SHA), \
                 patch.object(auto_translate.urllib.request, "urlopen", return_value=Response(answer)):
                with self.assertRaises(ValueError):
                    auto_translate.translate(self.root, self.out)
                self.assertFalse(self.out.exists())

    def test_source_drift_fails_before_output(self):
        def response(_request, timeout):
            self.file.write_text('    editor.set_error("changed");\n')
            return Response(json.dumps({"translations": [{"id": idx, "text": "译文"}
                                                         for idx in range(6)]}))
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test"}), \
             patch.object(auto_translate.subprocess, "check_output", return_value=SHA), \
             patch.object(auto_translate.urllib.request, "urlopen", side_effect=response):
            with self.assertRaises(ValueError):
                auto_translate.translate(self.root, self.out)
        self.assertFalse(self.out.exists())


if __name__ == "__main__":
    unittest.main()
