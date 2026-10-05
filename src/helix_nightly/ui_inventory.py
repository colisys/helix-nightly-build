"""Auditable *candidate* inventory; not a proof of complete localization.

An occurrence of a display API is evidence for manual review, not permission to
translate its arguments. Dynamic results may contain text supplied by LSP/DAP.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from .auto_translate import scan

# Known render call sites; deliberately do not inventory arbitrary Rust literals.
# Counts describe call sites, not a denominator for all Helix-visible UI copy.
CALL = re.compile(r"\b(?:set_status|set_error|Prompt::new|PickerColumn::new|Info::new|format!)\s*\(")
CATEGORIES = {
    "set_status": "status",
    "set_error": "error",
    "Prompt::new": "prompt",
    "PickerColumn::new": "picker_column",
    "Info::new": "info_popup",
    "format!": "format_context_unknown",
}


def inventory(source_dir: Path) -> dict:
    selected = scan(source_dir)
    selected_lines = {(entry["path"], entry["line"]) for entry in selected}
    rows = []
    counts = Counter()
    for crate in ("helix-term", "helix-view", "helix-core", "helix-tui"):
        root = source_dir / crate / "src"
        if not root.is_dir():
            raise ValueError(f"missing Helix source: {root}")
        for file in sorted(root.rglob("*.rs")):
            if file.is_symlink() or not file.resolve().is_relative_to(source_dir.resolve()):
                continue
            path = file.relative_to(source_dir).as_posix()
            for number, line in enumerate(file.read_text(encoding="utf-8").splitlines(), 1):
                for match in CALL.finditer(line):
                    call = match.group().split("(")[0].strip()
                    category = CATEGORIES[call]
                    # A line selected for a different literal may still contain
                    # another unreviewed call; leave it visible in the inventory.
                    status = "selected_line" if (path, line) in selected_lines else "review_needed"
                    counts[(category, status)] += 1
                    rows.append({"path": path, "line_number": number,
                                 "category": category, "status": status, "line": line})
    return {"selected_entries": len(selected),
            "call_site_counts": {category: {status: counts[(category, status)]
                                            for status in ("selected_line", "review_needed")}
                                 for category in CATEGORIES.values()},
            "candidates": rows,
            "note": "Candidate call sites only; dynamic arguments may be external. "
                    "This is not a complete UI coverage metric."}


def main() -> None:
    parser = argparse.ArgumentParser(description="Report unreviewed Helix UI call sites")
    parser.add_argument("--source-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(inventory(args.source_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
