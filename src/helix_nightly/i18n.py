"""Compile-time translations of explicitly reviewed, user-facing Rust literals.

Selections are curated, not discovered automatically: Rust literals also contain
protocol identifiers, commands and other strings which must never be translated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit


LANGUAGE = re.compile(r"[a-z]{2,3}(?:-[A-Z]{2})?\Z")
COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def _entries(data: dict) -> list[dict]:
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("expected nonempty entries list of manually selected UI strings")
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("entries must be objects")
        path = entry.get("path")
        if not isinstance(path, str):
            raise ValueError("entry path must be a Rust source path")
        parts = path.split("/")
        if (len(parts) < 3 or parts[0] not in ("helix-term", "helix-view", "helix-core", "helix-tui")
                or parts[1] != "src" or not parts[-1].endswith(".rs")
                or any(part in ("", ".", "..") for part in parts) or "\\" in path):
            raise ValueError(f"invalid Rust source path: {path}")
        line, source = entry.get("line"), entry.get("source")
        if not isinstance(line, str) or "\n" in line or "\r" in line:
            raise ValueError("entry line must be one complete source line")
        if not isinstance(source, str) or not source.startswith('"'):
            raise ValueError("source must be a JSON-compatible Rust string literal")
        try:
            decoded = json.loads(source)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid source literal: {source}") from error
        # Escape and format strings need Rust-aware parsing; refuse them for now.
        if (not isinstance(decoded, str) or not decoded or decoded != source[1:-1]
                or any(c in decoded for c in "{}\\") or line.count(source) != 1):
            raise ValueError("source literal must occur exactly once in its line")
        key = (path, line)
        if key in seen:
            raise ValueError(f"duplicate selection: {path}: {line}")
        seen.add(key)
        if not isinstance(entry.get("context"), str) or not entry["context"].strip():
            raise ValueError("each UI selection requires a human-written context")
    return entries


def load(path: Path, source_dir: Path, *, approved: bool, allow_machine: bool = False) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("translation file must contain a JSON object")
    if allow_machine and data.get("generated_by") != "auto_translate":
        raise ValueError("machine translation table must identify its generator")
    language = data.get("language")
    if not isinstance(language, str) or not LANGUAGE.fullmatch(language):
        raise ValueError("invalid language code")
    commit = data.get("upstream_commit")
    if not isinstance(commit, str) or not COMMIT.fullmatch(commit):
        raise ValueError("upstream_commit must be a full 40-character Git SHA")
    actual = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=source_dir, text=True
    ).strip()
    if actual != commit:
        raise ValueError(f"translation targets {commit}, but upstream is {actual}")
    entries = _entries(data)
    for entry in entries:
        relative = entry["path"]
        file = source_dir / relative
        if not file.is_file() or not file.resolve().is_relative_to(source_dir.resolve()):
            raise ValueError(f"unsafe or missing source path: {relative}")
        content = file.read_text(encoding="utf-8")
        if content.splitlines().count(entry["line"]) != 1:
            raise ValueError(f"source line absent or ambiguous: {relative}: {entry['line']}")
        if approved:
            translation = entry.get("translation")
            authorization = entry.get("approved") is (False if allow_machine else True)
            if (not authorization or not isinstance(translation, str)
                    or not translation.strip() or any(ord(c) < 32 for c in translation)
                    or any(c in translation for c in "{}\\")
                    or "\u2028" in translation or "\u2029" in translation):
                raise ValueError(f"translation must be nonempty, approved and single-line: {relative}")
    return data


def apply(source_dir: Path, data: dict) -> dict[Path, tuple[bytes, bytes]]:
    """Validate all replacements before writing; return originals and applied bytes."""
    replacements: dict[Path, tuple[bytes, bytes]] = {}
    for entry in _entries(data):
        path = source_dir / entry["path"]
        if path not in replacements:
            original = path.read_bytes()
            replacements[path] = (original, original)
        old, new = replacements[path]
        text = new.decode("utf-8")
        # Match the entire line, so a moved or modified UI string fails closed.
        lines = text.splitlines(keepends=True)
        matches = [i for i, line in enumerate(lines) if line.rstrip("\r\n") == entry["line"]]
        if len(matches) != 1:
            raise ValueError(f"ambiguous source line in {entry['path']}")
        i = matches[0]
        lines[i] = lines[i].replace(
            entry["source"], json.dumps(entry["translation"], ensure_ascii=False), 1
        )
        replacements[path] = (old, "".join(lines).encode("utf-8"))
    written: dict[Path, tuple[bytes, bytes]] = {}
    try:
        for path, (old, new) in replacements.items():
            path.write_bytes(new)
            written[path] = (old, new)
    except OSError:
        restore(written)
        raise
    return replacements


def restore(originals: dict[Path, tuple[bytes, bytes]]) -> None:
    for path, (original, applied) in originals.items():
        if hashlib.sha256(path.read_bytes()).digest() != hashlib.sha256(applied).digest():
            raise RuntimeError(f"Refusing to overwrite source changed during build: {path}")
        path.write_bytes(original)


def extract(source_dir: Path, output: Path, path: str, text: str, context: str) -> None:
    """Select an exact UI literal from an explicitly named source file."""
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if not context.strip():
        raise ValueError("describe why this string is user-facing UI")
    # Validate path before joining it to the checkout.
    literal = json.dumps(text, ensure_ascii=False)
    _entries({"entries": [{"path": path, "line": f"x({literal})",
                          "source": literal, "context": context}]})
    file = source_dir / path
    if not file.is_file() or not file.resolve().is_relative_to(source_dir.resolve()):
        raise ValueError(f"unsafe or missing source path: {path}")
    lines = [line for line in file.read_text(encoding="utf-8").splitlines()
             if line.count(literal) == 1]
    if len(lines) != 1:
        raise ValueError("literal must occur on exactly one line in selected file")
    entry = {"path": path, "line": lines[0], "source": literal, "context": context}
    _entries({"entries": [entry]})
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                     cwd=source_dir, text=True).strip()
    if not COMMIT.fullmatch(commit):
        raise ValueError("upstream checkout has no full commit SHA")
    output.write_text(json.dumps({"language": "zh-CN", "upstream_commit": commit,
                                  "entries": [entry]}, ensure_ascii=False, indent=2) + "\n",
                      encoding="utf-8")


def generate(selection: Path, source_dir: Path, output: Path, language: str) -> None:
    """Ask an OpenAI-compatible endpoint for draft translations; never approve them."""
    if not LANGUAGE.fullmatch(language):
        raise ValueError("invalid language code")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    data = load(selection, source_dir, approved=False)
    data["language"] = language
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY is required for draft generation")
    url = os.environ.get("OPENAI_API_URL", "https://api.openai.com/v1/chat/completions")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("OPENAI_API_URL must use HTTPS")
    model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
    result = {"language": language, "upstream_commit": data["upstream_commit"], "entries": []}
    for entry in data["entries"]:
        original = json.loads(entry["source"])
        payload = json.dumps({"model": model, "messages": [
            {"role": "system", "content": (
                f"Translate this Helix editor user-facing UI text into {language}. "
                "Return only the translated text. Preserve placeholders, key labels, "
                "newlines and formatting. Do not add explanations.")},
            {"role": "user", "content": f"Context: {entry['context']}\nText: {original}"},
        ]}).encode("utf-8")
        request = urllib.request.Request(url, payload, {
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"
        })
        with urllib.request.urlopen(request, timeout=60) as response:
            answer = json.load(response)["choices"][0]["message"]["content"]
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError("AI returned an empty translation")
        result["entries"].append({**entry, "translation": answer, "approved": False})
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate reviewable Helix UI translation drafts")
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--language", default="zh-CN")
    parser.add_argument("--extract-path", help="explicit Rust source path in upstream checkout")
    parser.add_argument("--extract-text", help="exact unescaped user-facing UI text")
    parser.add_argument("--context", help="human explanation of where UI text is displayed")
    args = parser.parse_args()
    try:
        if args.extract_path:
            if args.selection or args.extract_text is None or not args.context:
                parser.error("extraction requires --extract-text and --context, not --selection")
            extract(args.source_dir, args.output, args.extract_path, args.extract_text, args.context)
        else:
            if not args.selection:
                parser.error("generation requires --selection")
            generate(args.selection, args.source_dir, args.output, args.language)
    except (ValueError, OSError, KeyError, TypeError) as error:
        parser.exit(1, f"error: {error}\n")


if __name__ == "__main__":
    main()
