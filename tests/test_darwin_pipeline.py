"""Offline contracts for native macOS build and package validation."""

import ast
import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from helix_nightly import cli
from test_workflow_fallback import WORKFLOW, step


class DarwinWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_platform_selection_and_runners(self):
        matrix_step = step(self.workflow, "Select matrix")
        source = matrix_step.split("          platforms = [", 1)[1].split("          ]", 1)[0] + "          ]"
        platforms = ast.literal_eval("[" + source.strip())
        options = self.workflow.split("      platform:\n", 1)[1].split("  schedule:\n", 1)[0]
        for target, runner in (("x86_64-apple-darwin", "macos-15-intel"),
                               ("aarch64-apple-darwin", "macos-15")):
            with self.subTest(target=target):
                self.assertIn(f"          - {target}\n", options)
                matches = [item for item in platforms if item["target"] == target]
                self.assertEqual(len(matches), 1)
                self.assertEqual(matches[0], {"os": runner, "target": target,
                                              "formats": "archive", "grammar": True, "qemu": ""})
        self.assertEqual(len(platforms), len({item["target"] for item in platforms}))

    def test_selection_excludes_macos_from_schedule(self):
        source = step(self.workflow, "Select matrix")
        script = source.split("          python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        script = "\n".join(line[10:] for line in script.splitlines())
        for event, platform, requested, enabled, expected_count in (
            ("workflow_dispatch", "all", "original", "false", 10),
            ("workflow_dispatch", "all", "zh-CN", "true", 10),
            ("workflow_dispatch", "all", "zh-CN", "false", 10),
            ("schedule", "all", "original", "true", 16),
            ("schedule", "all", "original", "false", 8),
            ("workflow_dispatch", "aarch64-apple-darwin", "original", "false", 1),
            ("workflow_dispatch", "x86_64-apple-darwin", "original", "false", 1),
        ):
            with self.subTest(event=event, platform=platform, enabled=enabled), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / "output"
                with patch.dict(os.environ, {"EVENT_NAME": event, "PLATFORM": platform,
                                             "REQUESTED_LANGUAGE": requested,
                                             "TRANSLATION_ENABLED": enabled,
                                             "GITHUB_OUTPUT": str(output)}), \
                     contextlib.redirect_stdout(io.StringIO()):
                    exec(compile(script, "select matrix", "exec"), {})
                matrix = json.loads(output.read_text().split("matrix=", 1)[1])
                self.assertEqual(len(matrix["include"]), expected_count)
                self.assertEqual(len({(item["target"], item["language"])
                                      for item in matrix["include"]}), expected_count)
                languages = {item["language"] for item in matrix["include"]}
                self.assertEqual(languages, {"", "zh-CN"} if event == "schedule" and enabled == "true"
                                 else {"zh-CN"} if requested == "zh-CN" and enabled == "true" else {""})
                if event == "schedule":
                    self.assertTrue(all(not item["target"].endswith("-apple-darwin")
                                        for item in matrix["include"]))

        # A stale PLATFORM value must not bypass the scheduled-run filter.
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "output"
            with patch.dict(os.environ, {"EVENT_NAME": "schedule",
                                         "PLATFORM": "aarch64-apple-darwin",
                                         "REQUESTED_LANGUAGE": "original",
                                         "TRANSLATION_ENABLED": "true",
                                         "GITHUB_OUTPUT": str(output)}):
                with self.assertRaisesRegex(SystemExit, "Unknown platform"):
                    exec(compile(script, "select matrix", "exec"), {})
            self.assertFalse(output.exists())

    def test_macos_checks_only_run_on_macos(self):
        verify = step(self.workflow, "Verify macOS binary architecture")
        self.assertIn("if: runner.os == 'macOS'", verify)
        self.assertIn('target_dir="$target_dir/i18n-${BUILD_LANGUAGE}"', verify)
        self.assertIn('otool -hv "$binary"', verify)
        self.assertIn("aarch64-apple-darwin", verify)
        self.assertIn("arm64", verify)
        self.assertIn("x86_64", verify)
        runtime = step(self.workflow, "Verify packaged runtime")
        self.assertIn("runner.os == 'Linux' || runner.os == 'macOS'", runtime)
        grammar = step(self.workflow, "Report macOS grammar libraries")
        self.assertIn("if: runner.os == 'macOS'", grammar)
        self.assertIn("*.dylib", grammar)
        self.assertIn("python - <<'PY'", grammar)
        for name in ("Install Linux cross compiler and QEMU", "Install nfpm",
                     "Build nfpm from the official Go module", "Verify Linux binary architecture",
                     "Install Inno Setup", "Verify Windows binary architecture"):
            self.assertNotIn("runner.os == 'macOS'", step(self.workflow, name))

    def test_smoke_test_uses_native_macho_inspection(self):
        for target, header_tool in (("x86_64-apple-darwin", "otool"),
                                    ("aarch64-apple-darwin", "otool"),
                                    ("x86_64-unknown-linux-gnu", "readelf")):
            with self.subTest(target=target), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp) / "staged"
                package = root / "helix-test"
                package.mkdir(parents=True)
                with patch.object(cli, "run", return_value="") as run_mock:
                    cli.smoke_test(root, target, None)
                commands = [call.args[0] for call in run_mock.call_args_list]
                self.assertEqual([command[0] for command in commands],
                                 ["file", header_tool, str(package / "hx"), str(package / "hx")])
                self.assertEqual(commands[2][-1], "--version")
                self.assertEqual(commands[3][-2:], ["--health", "languages"])


if __name__ == "__main__":
    unittest.main()
