"""Contract tests for automatic translation fallback in the CI workflow."""

import os
import re
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch


WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/release.yml"


def step(text, name):
    match = re.search(r"(?ms)^      - name: " + re.escape(name) + r"\n(.*?)(?=^      - name: |^  [a-z]+:|\Z)", text)
    if match is None:
        raise AssertionError(f"Missing workflow step: {name}")
    return match.group(1)


class FallbackWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_translation_gate_needs_all_three_values_and_does_not_print_them(self):
        config = step(self.text, "Check translation configuration")
        self.assertIn("secrets.OPENAI_API_KEY", config)
        self.assertIn("vars.OPENAI_API_URL", config)
        self.assertIn("vars.OPENAI_MODEL", config)
        self.assertIn('[[ -n "$OPENAI_API_KEY" && -n "$OPENAI_API_URL" && -n "$OPENAI_MODEL" ]]', config)
        self.assertIn('echo "enabled=false" >> "$GITHUB_OUTPUT"', config)
        self.assertNotIn('echo "$OPENAI_API_KEY"', config)
        generate = step(self.text, "Generate translations")
        self.assertIn("OPENAI_MODEL: ${{ vars.OPENAI_MODEL }}", generate)
        self.assertIn("if: steps.config.outputs.enabled == 'true'", generate)

    def test_translation_steps_and_artifact_require_enabled(self):
        self.assertIn("enabled: ${{ steps.config.outputs.enabled }}", self.text)
        translate_job = self.text.split("  translate:\n", 1)[1].split("  select-platform:\n", 1)[0]
        self.assertIn("    needs: [check]\n", translate_job)
        self.assertIn("github.event_name == 'schedule' && needs.check.outputs.build_needed == 'true'", translate_job)
        self.assertIn("ref: ${{ needs.check.outputs.upstream_sha }}", translate_job)
        select_job = self.text.split("  select-platform:\n", 1)[1].split("  build:\n", 1)[0]
        self.assertIn("    needs: [check, translate]", select_job)
        self.assertIn("needs.translate.result == 'success'", select_job)
        self.assertIn("needs.translate.result == 'skipped'", select_job)
        self.assertIn("TRANSLATION_ENABLED: ${{ needs.translate.outputs.enabled }}", select_job)
        self.assertIn("REQUESTED_LANGUAGE: ${{ inputs.language || 'original' }}", select_job)
        for name in ("Check out Helix source", "Generate translations",
                     "Upload shared translation table"):
            body = step(self.text, name)
            self.assertIn("if: steps.config.outputs.enabled == 'true'", body)
        for name in ("Download shared translation table", "Verify upstream commit for translation"):
            self.assertIn("if: matrix.language != ''", step(self.text, name))

    def test_build_flags_and_architecture_follow_actual_translation(self):
        build = step(self.text, "Build and package")
        arch = step(self.text, "Verify Linux binary architecture")
        self.assertIn("BUILD_LANGUAGE: ${{ matrix.language }}", self.text)
        self.assertIn('if [[ -n "$BUILD_LANGUAGE" ]]; then', build)
        self.assertIn('if [[ -n "$BUILD_LANGUAGE" ]]; then', arch)
        self.assertIn('language_args=(--language "$BUILD_LANGUAGE" --translations "translations/ci-${BUILD_LANGUAGE}.json")', build)
        self.assertIn('target_dir="$target_dir/i18n-${BUILD_LANGUAGE}"', arch)
        self.assertIn("needs.translate.result == 'success'", self.text)
        self.assertIn("needs.translate.outputs.upstream_sha || needs.check.outputs.upstream_sha", build)
        self.assertIn("needs.select-platform.result == 'success'", self.text)
        self.assertIn('Join-Path $targetDir "i18n-$env:BUILD_LANGUAGE"', step(self.text, "Verify Windows binary architecture"))
        self.assertIn("env.BUILD_LANGUAGE || 'original'", self.text)
        self.assertIn("name: helix-${{ matrix.target }}-${{ matrix.language || 'original' }}",
                      step(self.text, "Upload packages"))
        download = step(self.text, "Download packages")
        self.assertIn("pattern: helix-*", download)
        self.assertIn("merge-multiple: true", download)
        self.assertNotIn("ci-zh-CN-translation-table", download)

    def test_schedule_release_uses_original_tag_and_waits_for_all_builds(self):
        release = self.text.split("  release:\n", 1)[1]
        self.assertIn("needs.build.result == 'success'", release)
        self.assertIn("github.event_name", step(self.text, "Generate release tag"))
        self.assertIn('"workflow_dispatch" && "$TRANSLATED" == "true"',
                      step(self.text, "Generate release tag"))
        marker = self.text.split("  mark-upstream-built:\n", 1)[1].split("  release:\n", 1)[0]
        self.assertIn("needs.build.result == 'success'", marker)
        self.assertIn("helix-built-i18n-v2-${{ steps.resolve.outputs.sha }}", self.text)
        self.assertIn("helix-built-i18n-v2-${{ needs.check.outputs.upstream_sha }}", marker)

    def test_packaged_runtime_uses_matching_binary_for_each_language(self):
        from helix_nightly import cli

        body = step(self.text, "Verify packaged runtime")
        script = body.split("PYTHONPATH=src python - <<'PY'\n", 1)[1].split("\n          PY", 1)[0]
        script = textwrap.dedent(script)
        for language in ("zh-CN", "fr-FR", ""):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as folder:
                source = Path(folder) / ".helix-source"
                source.mkdir()
                staging = Path(folder) / "staging"
                staging.mkdir()
                original_cwd = Path.cwd()
                try:
                    os.chdir(folder)
                    with patch.object(cli, "stage", return_value=staging) as stage_mock, \
                         patch.object(cli, "smoke_test") as smoke_mock, \
                         patch.dict(os.environ, {"BUILD_LANGUAGE": language}):
                        exec(compile(script.replace("${{ matrix.target }}", "x86_64-unknown-linux-gnu")
                                     .replace("${{ matrix.qemu }}", ""),
                                     "workflow runtime check", "exec"), {})
                    expected = source / "target"
                    if language:
                        expected /= f"i18n-{language}"
                    expected /= "x86_64-unknown-linux-gnu/release/hx"
                    self.assertEqual(stage_mock.call_args.args[1], expected)
                    smoke_mock.assert_called_once()
                finally:
                    os.chdir(original_cwd)


if __name__ == "__main__":
    unittest.main()
