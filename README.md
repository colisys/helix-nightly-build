# Helix nightly build

[![Build and release Helix nightly](https://github.com/colisys/helix-nightly-build/actions/workflows/release.yml/badge.svg)](https://github.com/colisys/helix-nightly-build/actions/workflows/release.yml)

This project builds the upstream [Helix](https://github.com/helix-editor/helix) repository and publishes platform packages as a GitHub Release.

## Formats and targets

The workflow builds:

- Linux `x86_64-unknown-linux-gnu`: `.deb`, `.rpm`, `.apk`, `.tar.gz`
- Linux `x86_64-unknown-linux-musl` (musl): `.deb`, `.rpm`, `.apk`, `.tar.gz`
- Linux `aarch64-unknown-linux-gnu` (arm64): `.deb`, `.rpm`, `.apk`, `.tar.gz`
- Linux `aarch64-unknown-linux-musl` (arm64 musl): `.deb`, `.rpm`, `.apk`, `.tar.gz`
- Linux `riscv64gc-unknown-linux-gnu` (RISC-V 64): `.deb`, `.rpm`, `.apk`, `.tar.gz`
- Linux `riscv64gc-unknown-linux-musl` (RISC-V 64 musl): `.deb`, `.rpm`, `.apk`, `.tar.gz`
- Windows `x86_64-pc-windows-msvc`: Inno Setup `.exe` installer, `.zip`
- Windows `aarch64-pc-windows-msvc` (ARM64): Inno Setup `.exe` installer, `.zip` (an x86_64 host `hx.exe` drives grammar generation while MSVC cross-compiles the grammar DLLs for ARM64)
- macOS `x86_64-apple-darwin`: `.tar.gz` (native Intel runner)
- macOS `aarch64-apple-darwin`: `.tar.gz` (native Apple Silicon runner)

Each output also gets a `.sha256` checksum. The Windows `.exe` is an Inno Setup installer containing the native `hx.exe` and the `runtime` directory. Linux packages are produced with `nfpm`; the workflow follows the official installation method `go install github.com/goreleaser/nfpm/v2/cmd/nfpm@latest` and invokes `nfpm pkg --packager deb|rpm|apk`. See the [nfpm Quick Start](https://nfpm.goreleaser.com/docs/quick-start/).

macOS archives contain `hx` and the runtime directory; they are not signed or notarized, so macOS Gatekeeper may require manual approval. Select either Darwin target (or `all`) with **Run workflow** to build on native macOS runners; scheduled builds retain the existing Linux/Windows matrix. macOS runners may have different availability or billing than Linux runners; check repository runner access before selecting `all`.

## Build-time grammar generation

Every workflow build runs:

```text
hx --grammar fetch
hx --grammar build
```

The commands `hx --grammar fetch` and `hx --grammar build` run after compilation and before packaging, so successfully generated grammar artifacts are included in the package. The aarch64 Linux job prefixes both commands with:

```text
qemu-aarch64 -L /usr/aarch64-linux-gnu
```

This is necessary because GitHub-hosted runners are x86_64 while the produced binary is aarch64. The ARM job uses Cargo's `aarch64-linux-gnu-gcc` linker, `aarch64-linux-gnu-g++` for Helix's Tree-sitter C/C++ grammar build, and QEMU user-mode emulation. The RISC-V job uses `riscv64-linux-gnu-gcc`, `riscv64-linux-gnu-g++`, and `qemu-riscv64` in the same way. The x86_64 musl job uses Debian's `musl-gcc`; the aarch64 and RISC-V musl jobs use the `ziglang` PyPI package as a locally installed cross compiler, avoiding a dependency on musl.cc. They produce statically linked x86_64, aarch64, and RISC-V Linux binaries. The Rust toolchain name may contain `x86_64-unknown-linux-gnu` because the compiler runs on the x86_64 GitHub runner; the actual output target is selected by each matrix target and is verified with `file`. More architecture-specific post-build commands can be added to `run_grammar()` in `src/helix_nightly/cli.py`.

## Local usage

Requirements: Python 3.10+, Git, Rust/Cargo, and packaging tools as needed. For the Linux aarch64 cross-build, install `gcc-aarch64-linux-gnu`, `g++-aarch64-linux-gnu`, `binutils-aarch64-linux-gnu`, `libc6-dev-arm64-cross`, and `qemu-user-binfmt`. For the RISC-V cross-build, install `gcc-riscv64-linux-gnu`, `g++-riscv64-linux-gnu`, `binutils-riscv64-linux-gnu`, `libc6-dev-riscv64-cross`, and `qemu-user-binfmt`. For the x86_64 musl build, install `musl-tools`. The aarch64 and RISC-V musl workflow jobs install `ziglang` from PyPI and use Zig's bundled musl sysroots.

```bash
python -m pip install -e .
PYTHONPATH=src python -m helix_nightly \
  --ref master \
  --target x86_64-unknown-linux-gnu \
  --formats archive,deb,rpm,apk \
  --grammar \
  --output-dir dist
```

Options:

- `--repo`: upstream repository URL
- `--ref`: branch, tag, or commit
- `--target`: Rust target triple
- `--formats`: comma-separated `archive`, `deb`, `rpm`, `apk`, `exe`
- `--grammar`: run `hx --grammar fetch` and `hx --grammar build`
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
the repository secret `OPENAI_API_KEY` and the Actions *variables*
`OPENAI_API_URL` (HTTPS OpenAI-compatible chat-completions endpoint) and
`OPENAI_MODEL` (your provider's model ID). All three must be nonempty; otherwise
automatic i18n is skipped. Never put an API key into a variable,
workflow input, translation file or source control. Providers receive the scanned
source strings; review provider data-handling policies before enabling the job.

Start **Run workflow** with `language: zh-CN` and choose a single `platform` for
the first run. The workflow publishes successful builds automatically; there is
no dry-run switch. Inspect the generated translation artifact and Release assets.
`ref` may be `master` or a pinned Helix SHA. One translation job scans the
selected upstream checkout, translates in batches of 25, and uploads a validated
JSON table for the selected platform jobs (up to eight). The jobs use the exact
upstream SHA from the translation job. If the endpoint, API key, or model is
absent, automatic translation is skipped and the workflow builds and labels
**original** Helix, without a `zh-CN` suffix or translation artifact, even when
`zh-CN` was requested. Once all three are configured,
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

To run one platform locally with [act](https://github.com/nektos/act), use the workflow dispatch input, for example:

```bash
act workflow_dispatch \\
  -W .github/workflows/release.yml \\
  -j build \\
  -P ubuntu-22.04=catthehacker/ubuntu:act-22.04 \\
  --input ref=master \\
  --input platform=x86_64-unknown-linux-gnu
```

`platform` is resolved by a small `select-platform` job before the build matrix is expanded. This keeps the workflow valid for both GitHub Actions and `act`; do not reference `matrix.*` from a job-level `if` condition.

## GitHub Actions

Push this project to GitHub, then use `Actions -> Build and release Helix -> Run workflow`. The scheduled workflow runs daily at 03:17 UTC, checks the upstream Helix commit, and publishes an unofficial prerelease using an automatically generated short tag such as `helix-079a789e8cb0`. The `helix-` prefix is required because GitHub rejects tags consisting solely of 40 or 64 hexadecimal characters. The tag uses the first 12 characters of the upstream commit; manually selected translated builds use a separate `-zh-CN` tag, while scheduled runs publish original and translated assets together under the base tag. The Release body identifies the upstream Helix ref and full commit; automatic notes from this builder repository are disabled. Manual runs select the Helix ref, platform, and optional language. Set `platform` to `all` for the complete matrix or choose one target to run only that platform; scheduled runs use the Linux/Windows platform matrix (macOS remains manual-only). Once all selected build matrix jobs succeed, the Release job runs automatically; if an identical upstream commit is rebuilt, the workflow reuses its generated tag and updates the Release assets without force-moving the tag.

Every run first checks whether the selected upstream Helix ref resolves successfully and whether its SHA has already been built with the current i18n matrix. On scheduled runs, a matching cached marker skips the build to save CI minutes; the marker key is versioned so older original-only runs do not suppress the first translated run for the same SHA. Manual `workflow_dispatch` runs always proceed with the selected platform, even when the SHA already has a cached marker. On scheduled runs, the upstream-built marker is saved by a separate job that depends on the entire build matrix, so a partial or failed matrix never marks the upstream SHA as successfully built. Manual single-platform runs do not write this full-matrix marker.

For scheduled runs with a new upstream SHA, the workflow generates the shared `zh-CN` translation table once, then builds the original and `zh-CN` variants for each scheduled platform in parallel. If the translation API is not configured, it builds only the original variant; a translation generation failure prevents publication and the upstream-built marker rather than releasing an incomplete set. This workflow currently provides one automated i18n variant (`zh-CN`); additional languages require their own translation pipeline before they can join the matrix. Each platform/language pair has a distinct artifact name, and each language has its own Cargo target directory and release filename. Manual runs still build only the requested language (or original if translation is unavailable).

The workflow caches the Cargo registry, git downloads, each target's `.helix-source/target` directory, and fetched/compiled grammars under `.helix-source/runtime/grammars`. The cache key is separated by runner OS, target triple, Cargo.lock, Rust toolchain, and `languages.toml`; the restore key allows dependency, build, and grammar reuse after upstream source changes. A `Report build cache` step prints whether the current run had an exact build-cache hit. Helix's automatic grammar fetch/build is disabled during Cargo compilation, then the explicit post-build grammar step runs `hx --grammar fetch` and `hx --grammar build`. Grammar failures are logged as warnings and do not discard the binary or successfully built grammars; a nightly can be published with a partial or empty grammar set when upstream grammar hosts are unavailable. The build reports how many compiled grammar libraries were found before creating packages. Grammar source checkouts are excluded from final packages; only the compiled grammar libraries and runtime files are shipped. A `Report package sizes` step prints the exact files uploaded from `dist/`.

The ARM64 Linux build is cross-compiled and uses QEMU for target-architecture grammar generation. The ARM64 musl build also runs grammar generation under `qemu-aarch64`. If Zig provides `ld-musl-aarch64.so.1`, the workflow sets `QEMU_LD_PREFIX`; otherwise it still attempts grammar execution because the musl binary may be statically linked, and the actual QEMU command result determines whether grammar generation succeeds. The RISC-V 64 Linux build is cross-compiled with Debian's `riscv64-linux-gnu` toolchain and uses `qemu-riscv64` for grammar generation. The RISC-V musl build follows the same conditional loader strategy. The ARM64 Windows build is cross-compiled on the x86_64 Windows runner. Because an ARM64 `hx.exe` cannot run natively there, the workflow first builds an x86_64 host `hx.exe`, temporarily enables Helix's grammar CLI to accept `HELIX_GRAMMAR_TARGET`, and uses the host process with the ARM64 MSVC C/C++ environment to produce ARM64 grammar DLLs. The upstream source is restored before the final ARM64 binary build. If this optional grammar host path fails, the nightly still packages the binary and any grammars already available. On current Ubuntu releases, `qemu-user-static` is a virtual package; install its concrete provider `qemu-user-binfmt` (or `qemu-user-binfmt-hwe` on HWE systems). QEMU user-mode emulation is not a full ARM virtual machine; if future Helix build steps require kernel features or services unavailable under user-mode QEMU, use an ARM64 self-hosted runner instead.
