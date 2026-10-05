# Helix nightly build

[![Build and release Helix nightly](https://github.com/colisys/helix-nightly-build/actions/workflows/release.yml/badge.svg)](https://github.com/colisys/helix-nightly-build/actions/workflows/release.yml)

This project builds the upstream [Helix](https://github.com/helix-editor/helix) repository and publishes platform packages as a GitHub Release.

## Formats and targets

The workflow builds:

- Linux `x86_64-unknown-linux-gnu`: `.deb`, `.rpm`, `.tar.gz`
- Linux `aarch64-unknown-linux-gnu` (arm64): `.deb`, `.rpm`, `.tar.gz`
- Windows `x86_64-pc-windows-msvc`: Inno Setup `.exe` installer, WiX `.msi` installer, `.zip`

Each output also gets a `.sha256` checksum. The Windows `.exe` is an Inno Setup installer, while the `.msi` is a WiX installer. Both include the native `hx.exe` and the `runtime` directory. Linux packages are produced with `nfpm`; the workflow follows the official installation method `go install github.com/goreleaser/nfpm/v2/cmd/nfpm@latest` and invokes `nfpm pkg --packager deb|rpm`. See the [nfpm Quick Start](https://nfpm.goreleaser.com/docs/quick-start/).

## Build-time grammar generation

Every workflow build runs:

```text
hx --grammar fetch
hx --grammar build
```

The commands `hx --grammar fetch` and `hx --grammar build` run after compilation and before packaging, so generated grammar artifacts are included in the package. The aarch64 Linux job prefixes both commands with:

```text
qemu-aarch64 -L /usr/aarch64-linux-gnu
```

This is necessary because GitHub-hosted runners are x86_64 while the produced binary is aarch64. The ARM job uses Cargo's `aarch64-linux-gnu-gcc` linker, `aarch64-linux-gnu-g++` for Helix's Tree-sitter C/C++ grammar build, and QEMU user-mode emulation. The Rust toolchain name may contain `x86_64-unknown-linux-gnu` because the compiler runs on the x86_64 GitHub runner; the actual output target is selected by `--target aarch64-unknown-linux-gnu` and is verified with `file`. More architecture-specific post-build commands can be added to `run_grammar()` in `src/helix_nightly/cli.py`.

## Local usage

Requirements: Python 3.10+, Git, Rust/Cargo, and packaging tools as needed. For the Linux aarch64 cross-build, install `gcc-aarch64-linux-gnu`, `g++-aarch64-linux-gnu`, `binutils-aarch64-linux-gnu`, `libc6-dev-arm64-cross`, and `qemu-user-binfmt`.

```bash
python -m pip install -e .
PYTHONPATH=src python -m helix_nightly \
  --ref master \
  --target x86_64-unknown-linux-gnu \
  --formats archive,deb,rpm \
  --grammar \
  --output-dir dist
```

Options:

- `--repo`: upstream repository URL
- `--ref`: branch, tag, or commit
- `--target`: Rust target triple
- `--formats`: comma-separated `archive`, `deb`, `rpm`, `exe`, `msi`
- `--grammar`: run `hx grammar fetch` and `hx grammar build`
- `--qemu`: command prefix for emulated post-build commands
- `--output-dir`: output directory
- `--source-dir`: reuse an existing checkout

## Compile-time UI translations (experimental)

Each language is compiled into a separate `hx`; there is no runtime translation
table, AI request, or language switch. The offline table uses **human-selected,
user-facing editor UI copy**, while CI scans a restricted set of verified UI
contexts. Do not translate Rust literals indiscriminately: commands, protocol
identifiers, paths, and data can look like prose. The starter pinned `zh-CN`
table translates only three search/jump-list messages; CI covers more, but is
not a complete Chinese interface.

The reviewed table `translations/zh-CN.json` is pinned to upstream commit
`ba40e547426b0f9896c8bdc699a4ab11f2b37dbc`. Use that exact `--ref` with
`--language zh-CN`; a different upstream SHA or missing/ambiguous source line
fails before compilation. The ordinary build without `--language` is unchanged.

```bash
PYTHONPATH=src python -m helix_nightly \
  --ref ba40e547426b0f9896c8bdc699a4ab11f2b37dbc \
  --language zh-CN --target x86_64-unknown-linux-gnu \
  --formats archive --output-dir dist
```

Language builds use a separate Cargo target directory and write distinct
`helix-<commit>-zh-CN-<target>.*` packages and `.sha256` files directly to
`dist/`, alongside unlocalized packages. To add UI text, identify where it is
displayed and explicitly extract a literal from a checked-out Helix source:

```bash
PYTHONPATH=src python -m helix_nightly.i18n \
  --source-dir .helix-source --extract-path helix-term/src/commands.rs \
  --extract-text 'No more matches' --context 'Search error shown to users' \
  --output /tmp/ui-selection.json
OPENAI_API_KEY=... OPENAI_MODEL=gpt-4o-mini \
  PYTHONPATH=src python -m helix_nightly.i18n \
  --source-dir .helix-source --selection /tmp/ui-selection.json \
  --output /tmp/zh-CN-draft.json
```

The extractor deliberately handles one exact, unescaped literal at a time; merge
reviewed entries for larger tables. The draft is **not approved**: inspect each
meaning and context, then set `approved: true` only after review. The builder
rejects escapes, backslashes and `{}` format strings rather than risk changing
Rust semantics; add Rust-aware validation before expanding that scope. Configure
`OPENAI_API_URL` for an HTTPS OpenAI-compatible endpoint; keep API keys out of
version control. Run offline checks with `python3 -m unittest discover -s tests`.

### Automatic CI translation

In the repository's **Settings → Secrets and variables → Actions**, configure
both the repository secret `OPENAI_API_KEY` and the Actions *variable*
`OPENAI_API_URL` (HTTPS OpenAI-compatible chat-completions endpoint). Optionally
set `OPENAI_MODEL` (default `gpt-4o-mini`). Never put an API key into a variable,
workflow input, translation file or source control. Providers receive the scanned
source strings; review provider data-handling policies before enabling the job.

Start **Run workflow** with `language: zh-CN` and `publish: false` on first use.
`ref` may be `master` or a pinned Helix SHA. One translation job scans the
selected upstream checkout, translates in batches of 25, and uploads a validated
JSON table for all three build jobs. The build jobs use the exact upstream SHA
from the translation job, so a moving branch cannot mix revisions. If either
the endpoint or API key is absent, automatic translation is skipped and the
workflow builds and labels **original** Helix, without a `zh-CN` suffix or
translation artifact, even when `zh-CN` was requested. Once both are configured,
malformed endpoints, API failures or source drift fail the translation job;
they never trigger an English fallback. Local builds using an explicit,
reviewed translation table are independent of these API credentials.

**Scope is intentionally conservative:** simple, single-line literals passed
directly to `editor.set_status(...)` / `editor.set_error(...)`, static keymap
command descriptions, `typed.rs` command/flag `doc` fields, and four verified
surrounding-pair popup titles in `helix-term/src/**/*.rs` are eligible. Direct
status/error literals in `helix-view/src/**/*.rs` and three Helix-authored DAP/LSP
prompt labels are also eligible. On the pinned upstream commit this scanner
selects 457 strings (36 status/error, 312 keymap descriptions, 102 typed/flag
descriptions, 4 popup titles and 3 prompt labels); this is a
count of *selected strings*, **not a percentage of all Helix UI text**. Escapes,
format placeholders, duplicate source lines and dynamic expressions are skipped.
Other prompt/picker labels, formatted errors, other crates and runtime-supplied
messages remain untranslated; LSP/DAP responses, plugins and document content
are outside the localization boundary. In particular, some picker column names
are also internal lookup keys and must not be blindly replaced. This is **not
full Helix localization**; AI output is validated structurally but neither
semantically reviewed nor guaranteed to be idiomatic. CI-generated entries have
`generated_by: auto_translate` and `approved: false`; only the explicit CI build
table path accepts machine drafts. This is not a human approval marker. Inspect
the translation artifact before publishing. Scheduled builds and `original`
manual builds do not call the AI API. The existing pinned table remains available
for offline `zh-CN` builds using `--translations translations/zh-CN.json`.

To audit work still needing classification, run
`PYTHONPATH=src python -m helix_nightly.ui_inventory --source-dir .helix-source`.
It emits JSON candidate call sites with file/line and selected-vs-review-needed
labels, including calls whose arguments may come from LSP/DAP. It does not send
anything to the AI provider and its call-site counts are **not** a complete-UI
coverage percentage or permission to translate dynamic values.

## GitHub Actions

Push this project to GitHub, then use `Actions -> Build and release Helix -> Run workflow`. The scheduled workflow runs weekly on Mondays and Thursdays at 03:17 UTC, creates an annotated Git tag such as `nightly-YYYYMMDD`, and publishes a prerelease using that tag. Manual runs can select the Helix ref and release tag, or set `publish` to false to only build artifacts. If a selected tag already exists, the workflow reuses it and updates the Release assets without force-moving the tag.

The scheduled run first checks whether Helix's upstream HEAD has changed since the last build. If the upstream commit SHA matches a cached marker (stored via GitHub Actions cache), the build is skipped entirely — saving CI minutes when there are no new commits. To force a fresh build from a scheduled run, trigger a `workflow_dispatch` run instead.

The workflow caches Cargo registry and git downloads, but deliberately does not cache Rust's `target` directory or generated grammar tree. This avoids accumulating multi-gigabyte incremental build artifacts in GitHub Actions storage. Helix's automatic grammar fetch/build is disabled during Cargo compilation because some upstream grammar hosts can be temporarily unreachable. The optional post-build grammar fetch/build logs a warning and continues when a grammar cannot be fetched, so a network outage does not consume a full failed run.

The ARM64 Linux build is cross-compiled and uses QEMU for target-architecture grammar generation. On current Ubuntu releases, `qemu-user-static` is a virtual package; install its concrete provider `qemu-user-binfmt` (or `qemu-user-binfmt-hwe` on HWE systems). QEMU user-mode emulation is not a full ARM virtual machine; if future Helix build steps require kernel features or services unavailable under user-mode QEMU, use an ARM64 self-hosted runner instead.
