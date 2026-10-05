"""Conservative, build-time translation of verified Helix editor UI text.

Only explicit display sinks and command help text are scanned. Unknown contexts
are left untouched rather than risking command, protocol or format strings.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from collections import Counter
from pathlib import Path

from . import i18n

# Only contexts with known UI display semantics; no nested/dynamic calls.
SINK = re.compile(
    r'^(?P<indent>\s*)(?:(?:cx\.|self\.)?editor|self)\.set_(?:status|error)\('
    r'(?P<literal>"[^"\\\r\n{}]+")\);\s*$'
)
DOC = re.compile(r'^\s*doc:\s*(?P<literal>"[^"\\\r\n{}]+"),\s*$')
STATIC_DOC = re.compile(r'^\s*[a-zA-Z_][a-zA-Z_0-9]*,\s*(?P<literal>"[^"\\\r\n{}]+"),\s*$')
INFO_TITLE = re.compile(r'^\s*cx\.editor\.autoinfo = Some\(Info::new\((?P<literal>"[^"\\\r\n{}]+"),\s*&SURROUND_HELP_TEXT\)\);\s*$')
INFO_ARG = re.compile(r'^\s*(?P<literal>"[^"\\\r\n{}]+"),\s*$')
PROMPT_ARG = re.compile(r'^\s*(?P<literal>"[^"\\\r\n{}]+")\.into\(\),\s*$')
BATCH_SIZE = 25
MAX_ENTRIES = 2000


def scan(source_dir: Path) -> list[dict]:
    roots = [source_dir / crate / "src" for crate in ("helix-term", "helix-view")]
    if any(not root.is_dir() for root in roots):
        raise ValueError("missing Helix UI source (helix-term/src or helix-view/src)")
    entries: list[dict] = []
    for file in sorted(f for root in roots for f in root.rglob("*.rs")):
        if file.is_symlink() or not file.resolve().is_relative_to(source_dir.resolve()):
            continue
        relative = file.relative_to(source_dir).as_posix()
        lines = file.read_text(encoding="utf-8").splitlines()
        counts = Counter(lines)
        # The static command macro is explicitly the help text displayed for
        # key mappings. Do not treat its command identifier as translatable.
        static_help = False
        for index, line in enumerate(lines):
            if relative == "helix-term/src/commands.rs" and "static_commands!(" in line:
                static_help = True
                continue
            if static_help and line.strip() == ");":
                static_help = False
            match = SINK.fullmatch(line)
            if relative.startswith("helix-term/") and line.lstrip().startswith("self.set_"):
                match = None
            context = "Helix editor status/error message shown to the user"
            if match is None and relative == "helix-term/src/commands/typed.rs":
                match = DOC.fullmatch(line)
                context = "Helix typable command or flag help description (preserve command names)"
            if match is None and static_help:
                match = STATIC_DOC.fullmatch(line)
                context = "Helix keymap command help description (not the command identifier)"
            if match is None and relative == "helix-term/src/commands.rs":
                match = INFO_TITLE.fullmatch(line)
                if (match is None and index > 0
                        and lines[index - 1].strip() == "cx.editor.autoinfo = Some(Info::new("):
                    match = INFO_ARG.fullmatch(line)
                if match is not None:
                    context = "Helix surrounding-pair help popup title"
            if (match is None and relative in ("helix-term/src/commands/dap.rs",
                                               "helix-term/src/commands/lsp.rs")
                    and index > 0 and "Prompt::new(" in lines[index - 1]):
                match = PROMPT_ARG.fullmatch(line)
                if match is not None:
                    context = "Helix built-in prompt label (not user input)"
            if not match or counts[line] != 1:
                continue
            literal = match.group("literal")
            text = json.loads(literal)
            if (not text.strip() or len(text) > 500 or not any(ch.isalpha() for ch in text)
                    or any(ord(ch) < 32 for ch in text)):
                continue
            entry = {"path": relative, "line": line, "source": literal,
                     "context": context}
            i18n._entries({"entries": [entry]})
            entries.append(entry)
            if len(entries) > MAX_ENTRIES:
                raise ValueError("too many UI messages; review scanner scope")
    if not entries:
        raise ValueError("no safe UI messages found; refusing untranslated build")
    return entries


def translate(source_dir: Path, output: Path, language: str = "zh-CN") -> int:
    if output.exists():
        raise FileExistsError(f"refusing to overwrite {output}")
    if not i18n.LANGUAGE.fullmatch(language):
        raise ValueError("invalid language code")
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise ValueError("OPENAI_API_KEY secret is required")
    url = os.environ.get("OPENAI_API_URL") or "https://api.openai.com/v1/chat/completions"
    from urllib.parse import urlsplit
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("OPENAI_API_URL must be HTTPS without credentials in URL")
    model = os.environ.get("OPENAI_MODEL") or "gpt-4o-mini"
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source_dir, text=True).strip()
    if not i18n.COMMIT.fullmatch(sha):
        raise ValueError("could not determine upstream commit")
    entries = scan(source_dir)
    translated: list[dict] = []
    for offset in range(0, len(entries), BATCH_SIZE):
        batch = entries[offset:offset + BATCH_SIZE]
        messages = [{"id": idx, "text": json.loads(e["source"]),
                     "context": e["context"]} for idx, e in enumerate(batch)]
        payload = json.dumps({"model": model, "response_format": {"type": "json_object"},
                              "messages": [
            {"role": "system", "content": (
                f"Translate only these Helix editor UI/help messages to {language}. "
                "Return a JSON object with key 'translations': an array of objects "
                "with integer id and string text in input order. Preserve meaning. "
                "Never translate command names or identifiers.")},
            {"role": "user", "content": json.dumps(messages, ensure_ascii=False)}
        ]}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, payload, {
            "Authorization": f"Bearer {key}", "Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=120) as response:
            body = json.load(response)
        answers = json.loads(body["choices"][0]["message"]["content"])["translations"]
        if (not isinstance(answers, list) or len(answers) != len(batch)
                or any(not isinstance(answer, dict) or type(answer.get("id")) is not int
                       or answer["id"] != idx or not isinstance(answer.get("text"), str)
                       for idx, answer in enumerate(answers))):
            raise ValueError("AI translation count or order does not match input")
        for entry, answer in zip(batch, answers):
            value = answer["text"]
            if (not value.strip() or len(value) > 600 or any(ord(ch) < 32 for ch in value)
                    or any(ch in value for ch in '{}\\') or '\u2028' in value or '\u2029' in value):
                raise ValueError(f"unsafe AI translation for {entry['path']}")
            translated.append({**entry, "translation": value, "approved": False})
    data = {"language": language, "upstream_commit": sha,
            "generated_by": "auto_translate", "entries": translated}
    output.parent.mkdir(parents=True, exist_ok=True)
    # Revalidate source and generated translations before persisting any result.
    temp = output.with_name(output.name + ".tmp")
    if temp.exists():
        raise FileExistsError(f"refusing to overwrite {temp}")
    try:
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        i18n.load(temp, source_dir, approved=True, allow_machine=True)
        temp.rename(output)
    finally:
        temp.unlink(missing_ok=True)
    return len(entries)


def main() -> None:
    parser = argparse.ArgumentParser(description="Translate safe Helix UI messages in batches")
    parser.add_argument("--source-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--language", default="zh-CN")
    args = parser.parse_args()
    try:
        count = translate(args.source_dir, args.output, args.language)
        print(f"Generated {count} {args.language} UI translations")
    except (ValueError, OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
