# Helix nightly build

This project builds the upstream [Helix](https://github.com/helix-editor/helix) repository and publishes platform packages as a GitHub Release.

## Formats and targets

The workflow builds:

- Linux `x86_64-unknown-linux-gnu`: `.deb`, `.rpm`, `.tar.gz`
- Linux `aarch64-unknown-linux-gnu` (arm64): `.deb`, `.rpm`, `.tar.gz`
- Windows `x86_64-pc-windows-msvc`: Inno Setup `.exe` installer, WiX `.msi` installer, `.zip`

Each output also gets a `.sha256` checksum. The Windows `.exe` is an Inno Setup installer, while the `.msi` is a WiX installer. Both include the native `hx.exe` and the `runtime` directory. Linux packages are produced with `nfpm`.

## Build-time grammar generation

Every workflow build runs:

```text
hx grammar fetch
hx grammar build
```

The commands run after compilation and before packaging, so generated grammar artifacts are included in the package. The aarch64 Linux job prefixes both commands with:

```text
qemu-aarch64 -L /usr/aarch64-linux-gnu
```

This is necessary because GitHub-hosted runners are x86_64 while the produced binary is aarch64. The ARM job uses Cargo's `aarch64-linux-gnu-gcc` linker and QEMU user-mode emulation. More architecture-specific post-build commands can be added to `run_grammar()` in `src/helix_nightly/cli.py`.

## Local usage

Requirements: Python 3.10+, Git, Rust/Cargo, and packaging tools as needed.

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

## GitHub Actions

Push this project to GitHub, then use `Actions -> Build and release Helix -> Run workflow`. The scheduled workflow runs daily and publishes a prerelease tagged `nightly-YYYYMMDD`. Manual runs can select the Helix ref and release tag, or set `publish` to false to only build artifacts.

The ARM64 Linux build is cross-compiled and uses QEMU for target-architecture grammar generation. On current Ubuntu releases, `qemu-user-static` is a virtual package; install its concrete provider `qemu-user-binfmt` (or `qemu-user-binfmt-hwe` on HWE systems). QEMU user-mode emulation is not a full ARM virtual machine; if future Helix build steps require kernel features or services unavailable under user-mode QEMU, use an ARM64 self-hosted runner instead.
