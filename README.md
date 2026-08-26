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

Each output also gets a `.sha256` checksum. The Windows `.exe` is an Inno Setup installer containing the native `hx.exe` and the `runtime` directory. Linux packages are produced with `nfpm`; the workflow follows the official installation method `go install github.com/goreleaser/nfpm/v2/cmd/nfpm@latest` and invokes `nfpm pkg --packager deb|rpm|apk`. See the [nfpm Quick Start](https://nfpm.goreleaser.com/docs/quick-start/).

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

Push this project to GitHub, then use `Actions -> Build and release Helix -> Run workflow`. The scheduled workflow runs daily at 03:17 UTC, checks the upstream Helix commit, and publishes an unofficial prerelease using an automatically generated short tag such as `helix-079a789e8cb0`. The `helix-` prefix is required because GitHub rejects tags consisting solely of 40 or 64 hexadecimal characters. The tag uses the first 12 characters of the upstream commit; no manual tag input is required. The Release body identifies the upstream Helix ref and full commit; automatic notes from this builder repository are disabled. Manual runs only need to select the Helix ref and platform. Set `platform` to `all` for the complete matrix or choose one target to run only that platform; scheduled runs always build the complete matrix. Once all build matrix jobs succeed, the Release job runs automatically; if an identical upstream commit is rebuilt, the workflow reuses its generated tag and updates the Release assets without force-moving the tag.

Every run first checks whether the selected upstream Helix ref resolves successfully and whether its SHA has already been built. On scheduled runs, a matching cached marker skips the build to save CI minutes. Manual `workflow_dispatch` runs always proceed with the selected platform, even when the SHA already has a cached marker. On scheduled runs, the upstream-built marker is saved by a separate job that depends on the entire build matrix, so a partial or failed matrix never marks the upstream SHA as successfully built. Manual single-platform runs do not write this full-matrix marker.

The workflow caches the Cargo registry, git downloads, each target's `.helix-source/target` directory, and fetched/compiled grammars under `.helix-source/runtime/grammars`. The cache key is separated by runner OS, target triple, Cargo.lock, Rust toolchain, and `languages.toml`; the restore key allows dependency, build, and grammar reuse after upstream source changes. A `Report build cache` step prints whether the current run had an exact build-cache hit. Helix's automatic grammar fetch/build is disabled during Cargo compilation, then the explicit post-build grammar step runs `hx --grammar fetch` and `hx --grammar build`. Grammar failures are logged as warnings and do not discard the binary or successfully built grammars; a nightly can be published with a partial or empty grammar set when upstream grammar hosts are unavailable. The build reports how many compiled grammar libraries were found before creating packages. Grammar source checkouts are excluded from final packages; only the compiled grammar libraries and runtime files are shipped. A `Report package sizes` step prints the exact files uploaded from `dist/`.

The ARM64 Linux build is cross-compiled and uses QEMU for target-architecture grammar generation. The ARM64 musl build also runs grammar generation under `qemu-aarch64`. If Zig provides `ld-musl-aarch64.so.1`, the workflow sets `QEMU_LD_PREFIX`; otherwise it still attempts grammar execution because the musl binary may be statically linked, and the actual QEMU command result determines whether grammar generation succeeds. The RISC-V 64 Linux build is cross-compiled with Debian's `riscv64-linux-gnu` toolchain and uses `qemu-riscv64` for grammar generation. The RISC-V musl build follows the same conditional loader strategy. The ARM64 Windows build is cross-compiled on the x86_64 Windows runner. Because an ARM64 `hx.exe` cannot run natively there, the workflow first builds an x86_64 host `hx.exe`, temporarily enables Helix's grammar CLI to accept `HELIX_GRAMMAR_TARGET`, and uses the host process with the ARM64 MSVC C/C++ environment to produce ARM64 grammar DLLs. The upstream source is restored before the final ARM64 binary build. If this optional grammar host path fails, the nightly still packages the binary and any grammars already available. On current Ubuntu releases, `qemu-user-static` is a virtual package; install its concrete provider `qemu-user-binfmt` (or `qemu-user-binfmt-hwe` on HWE systems). QEMU user-mode emulation is not a full ARM virtual machine; if future Helix build steps require kernel features or services unavailable under user-mode QEMU, use an ARM64 self-hosted runner instead.
