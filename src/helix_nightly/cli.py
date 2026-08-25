from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import shlex
import subprocess
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path


DEFAULT_REPO = "https://github.com/helix-editor/helix.git"


def run(
    command: Sequence[str],
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> str:
    print("+", " ".join(command))
    process_env = os.environ.copy()
    if env:
        process_env.update(env)
    result = subprocess.run(
        command,
        cwd=cwd,
        env=process_env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout)
    return result.stdout.strip()


def checkout(repo: str, ref: str, source_dir: Path) -> None:
    if source_dir.exists() and (source_dir / ".git").exists():
        run(["git", "fetch", "--depth", "1", "origin", ref], cwd=source_dir)
        run(["git", "checkout", "--force", "FETCH_HEAD"], cwd=source_dir)
        return
    if source_dir.exists():
        shutil.rmtree(source_dir)
    source_dir.parent.mkdir(parents=True, exist_ok=True)
    run(["git", "clone", "--depth", "1", repo, str(source_dir)])
    run(["git", "fetch", "--depth", "1", "origin", ref], cwd=source_dir)
    run(["git", "checkout", "--force", "FETCH_HEAD"], cwd=source_dir)


def host_target() -> str:
    output = subprocess.check_output(["rustc", "-vV"], text=True)
    for line in output.splitlines():
        if line.startswith("host:"):
            return line.split(":", 1)[1].strip()
    raise RuntimeError("Could not determine the Rust host target")


def run_grammar(source_dir: Path, binary: Path, qemu: str | None) -> None:
    prefix = shlex.split(qemu) if qemu else []
    for action in ("fetch", "build"):
        try:
            run(prefix + [str(binary), "--grammar", action], cwd=source_dir)
        except (subprocess.CalledProcessError, OSError) as error:
            print(f"warning: grammar {action} failed; continuing: {error}", file=sys.stderr)


def _skip_symlinks(_directory: str, names: list[str]) -> set[str]:
    """Ignore function for ``shutil.copytree`` that skips all symlinks.

    Helix's grammar tree contains dangling directory symlinks (e.g.
    ``move/queries``) and recursive self-referencing symlinks (e.g.
    ``rpmspec/queries/rpmspec/rpmspec/…``).  ``copytree`` follows these
    when ``symlinks=False`` and raises ``Errno 40`` (too many levels) or
    ``Errno 2`` (file not found).  Skipping symlinks entirely avoids both
    errors.  This is safe because the symlinked query directories only
    contain Tree-sitter query files that are regenerated or not needed at
    runtime for the affected languages.
    """
    directory = Path(_directory)
    return {name for name in names if (directory / name).is_symlink()}


def stage(source_dir: Path, binary: Path, version: str, target: str) -> Path:
    staging = Path(tempfile.mkdtemp(prefix="helix-package-"))
    root = staging / f"helix-{version}-{target}"
    root.mkdir()
    shutil.copy2(binary, root / ("hx.exe" if binary.suffix.lower() == ".exe" else "hx"))
    runtime = source_dir / "runtime"
    if not runtime.is_dir():
        raise FileNotFoundError(f"Helix runtime directory not found: {runtime}")
    # Skip problematic symlinks so copytree succeeds on both Linux and Windows.
    shutil.copytree(runtime, root / "runtime", symlinks=True, ignore=_skip_symlinks)
    return staging


def make_archive(staging: Path, output_dir: Path, target: str, version: str) -> Path:
    root = next(staging.iterdir())
    output_dir.mkdir(parents=True, exist_ok=True)
    if target.endswith("-windows-msvc"):
        path = output_dir / f"helix-{version}-{target}.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive_file:
            for item in root.rglob("*"):
                if item.is_file():
                    archive_file.write(item, item.relative_to(staging))
    else:
        path = output_dir / f"helix-{version}-{target}.tar.gz"
        with tarfile.open(path, "w:gz") as archive_file:
            archive_file.add(root, arcname=root.name)
    return path


def make_nfpm(staging: Path, output_dir: Path, target: str, version: str, fmt: str) -> Path:
    if shutil.which("nfpm") is None:
        raise RuntimeError("nfpm is required to create deb/rpm packages")
    root = next(staging.iterdir())
    binary = root / "hx"
    arch = "arm64" if target.startswith(("aarch64", "arm64")) else "amd64"
    path = output_dir / f"helix-{version}-{target}.{fmt}"
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as config:
        config.write(
            f"name: helix\narch: {arch}\nplatform: linux\n"
            f"version: 0.0.0\nversion_metadata: {version}\n"
        )
        config.write(
            "maintainer: Helix nightly builder\n"
            "description: Helix editor nightly build\n"
            "homepage: https://helix-editor.com\n"
            "license: MPL-2.0\n"
            "contents:\n"
        )
        config.write(
            f"  - src: {binary}\n"
            "    dst: /usr/bin/hx\n"
            "    file_info:\n"
            "      mode: 0755\n"
        )
        config.write(
            f"  - src: {root / 'runtime'}\n"
            "    dst: /usr/lib/helix/runtime\n"
            "    type: tree\n"
        )
        config_path = Path(config.name)
    try:
        run(["nfpm", "pkg", "--packager", fmt, "--target", str(path), "--config", str(config_path)])
    finally:
        config_path.unlink(missing_ok=True)
    return path


def make_exe(staging: Path, output_dir: Path, target: str, version: str) -> Path:
    compiler = shutil.which("iscc") or shutil.which("ISCC.exe")
    if compiler is None:
        raise RuntimeError("Inno Setup is required to create an EXE installer")
    root = next(staging.iterdir())
    script = output_dir / "helix.iss"
    installer = output_dir / f"helix-{version}-{target}-setup.exe"
    script.write_text(f'''[Setup]
AppName=Helix
AppVersion=1.0.0
DefaultDirName={{autopf}}\\Helix
OutputBaseFilename=helix-{version}-{target}-setup
OutputDir={output_dir}
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Files]
Source: "{root / 'hx.exe'}"; DestDir: "{{app}}"; Flags: ignoreversion
Source: "{root / 'runtime'}\\*"; DestDir: "{{app}}\\runtime"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{{group}}\\Helix"; Filename: "{{app}}\\hx.exe"
''', encoding="utf-8")
    run([compiler, "/Q", str(script)])
    script.unlink(missing_ok=True)
    if not installer.is_file():
        raise FileNotFoundError(f"Inno Setup installer not found: {installer}")
    return installer



def checksum(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(f"{path.name}.sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")


def build(args: argparse.Namespace) -> list[Path]:
    output_dir = Path(args.output_dir).resolve()
    source_dir = Path(args.source_dir).resolve() if args.source_dir else Path(".helix-source").resolve()
    checkout(args.repo, args.ref, source_dir)
    version = run(["git", "rev-parse", "--short=12", "HEAD"], cwd=source_dir)
    target = args.target or host_target()
    toolchain: str | None = None
    if args.target:
        # Helix may provide rust-toolchain.toml. Install the target in the
        # toolchain selected from the source directory, not the builder root.
        active = run(["rustup", "show", "active-toolchain"], cwd=source_dir)
        toolchain = active.split()[0]
        print(f"Using Rust toolchain {toolchain} for target {target}")
        run(["rustup", "target", "add", "--toolchain", toolchain, target], cwd=source_dir)
        sysroot = Path(run(["rustc", "+" + toolchain, "--print", "sysroot"], cwd=source_dir))
        target_lib = sysroot / "lib" / "rustlib" / target / "lib"
        if not any(target_lib.glob("libcore-*.rlib")):
            raise RuntimeError(
                f"Rust target {target} is not installed for {toolchain} in {sysroot}"
            )
    cargo_args = ["cargo"]
    if toolchain:
        cargo_args.append("+" + toolchain)
    cargo_args += ["build", "--release", "--locked", "--package", "helix-term"]
    if args.target:
        cargo_args += ["--target", target]
    build_env: dict[str, str] = {"HELIX_DISABLE_AUTO_GRAMMAR_BUILD": "1"}
    if target.endswith("-linux-gnu"):
        build_env["HELIX_DEFAULT_RUNTIME"] = "/usr/lib/helix/runtime"
    if target == "aarch64-unknown-linux-gnu":
        build_env.update(
            {
                "CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER": "aarch64-linux-gnu-gcc",
                "CC_aarch64_unknown_linux_gnu": "aarch64-linux-gnu-gcc",
                "CXX_aarch64_unknown_linux_gnu": "aarch64-linux-gnu-g++",
                "AR_aarch64_unknown_linux_gnu": "aarch64-linux-gnu-ar",
            }
        )
    print(f"Building for target {target}; host toolchain is {toolchain or host_target()}")
    run(cargo_args, cwd=source_dir, env=build_env)
    binary_name = "hx.exe" if target.endswith("-windows-msvc") else "hx"
    binary_dir = source_dir / "target" / target / "release" if args.target else source_dir / "target" / "release"
    binary = binary_dir / binary_name
    if not binary.is_file():
        raise FileNotFoundError(f"Built Helix binary not found: {binary}")

    print("=" * 72)
    print("Helix compilation completed successfully")
    print(f"Target architecture: {target}")
    print(f"Built binary: {binary}")
    if args.grammar:
        print("Starting grammar fetch/build")
        print(f"Grammar command prefix: {args.qemu or '(none; native execution)'}")
        print("=" * 72)
        run_grammar(source_dir, binary, args.qemu)
    else:
        print("Grammar fetch/build skipped")
    print("=" * 72)
    staging = stage(source_dir, binary, version, target)
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        formats = set(args.formats.split(","))
        if "archive" in formats:
            paths.append(make_archive(staging, output_dir, target, version))
        if target.endswith("-linux-gnu"):
            for fmt in ("deb", "rpm"):
                if fmt in formats:
                    paths.append(make_nfpm(staging, output_dir, target, version, fmt))
        if target.endswith("-windows-msvc"):
            if "exe" in formats:
                paths.append(make_exe(staging, output_dir, target, version))

        normalized: list[Path] = []
        for path in paths:
            if path.parent != output_dir:
                copied = output_dir / f"helix-{version}-{target}.exe"
                shutil.copy2(path, copied)
                path = copied
            checksum(path)
            normalized.append(path)
        return normalized
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Build and package Helix")
    result.add_argument("--repo", default=DEFAULT_REPO)
    result.add_argument("--ref", default=os.environ.get("HELIX_REF", "master"))
    result.add_argument("--target", help="Rust target triple")
    result.add_argument("--output-dir", default="dist")
    result.add_argument("--source-dir")
    result.add_argument("--grammar", action="store_true", help="run hx --grammar fetch/build")
    result.add_argument("--qemu", help="QEMU executable to prefix grammar commands")
    result.add_argument("--formats", default="archive", help="comma-separated: archive,deb,rpm,exe")
    return result


def main() -> None:
    try:
        build(parser().parse_args())
    except (subprocess.CalledProcessError, OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
