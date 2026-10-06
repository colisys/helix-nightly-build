"""Contract tests for automatic translation fallback in the CI workflow."""

import re
import unittest
from pathlib import Path


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
        for name in ("Check out Helix source", "Generate translations",
                     "Upload shared translation table", "Download shared translation table",
                     "Verify upstream commit for translation"):
            body = step(self.text, name)
            self.assertIn("if: " + ("steps.config" if name in (
                "Check out Helix source", "Generate translations", "Upload shared translation table"
            ) else "needs.translate") + ".outputs.enabled == 'true'", body)

    def test_build_flags_and_architecture_follow_actual_translation(self):
        build = step(self.text, "Build and package")
        arch = step(self.text, "Verify Linux binary architecture")
        self.assertIn('if [[ "${{ needs.translate.outputs.enabled }}" == "true" ]]', build)
        self.assertIn('if [[ "${{ needs.translate.outputs.enabled }}" == "true" ]]', arch)
        self.assertIn('language_args=(--language zh-CN --translations translations/ci-zh-CN.json)', build)
        self.assertIn("needs.translate.result == 'success'", self.text)
        self.assertIn("needs.translate.outputs.upstream_sha || needs.check.outputs.upstream_sha", build)
        self.assertIn("needs.select-platform.result == 'success'", self.text)
        self.assertIn("$targetDir\\i18n-zh-CN", step(self.text, "Verify Windows binary architecture"))


if __name__ == "__main__":
    unittest.main()
