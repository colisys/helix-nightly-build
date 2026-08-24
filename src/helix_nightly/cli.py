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
from collections.abc import Sequence
from pathlib import Path

DEFAULT_REPO = "https://github.com/helix-editor/helix.git"


def run(command: Sequence[str], cwd: Path | None = None) -> str:
    print("+", " ".join(command))
    result = subprocess.run(
        command, cwd=cwd, check=True, text=True, stdout=subprocess.PIPE
    )
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
    run(prefix + [str(binary), "grammar", "fetch"], cwd=source_dir)
    run(prefix + [str(binary), "grammar", "build"], cwd=source_dir)


def stage(source_dir: Path, binary: Path, version: str, target: str) -> Path:
    staging = Path(tempfile.mkdtemp(prefix="helix-package-"))
    root = staging / f"helix-{version}-{target}"
    root.mkdir()
    shutil.copy2(binary, root / ("hx.exe" if binary.suffix.lower() == ".exe" else "hx"))
    runtime = source_dir / "runtime"
    if not runtime.is_dir():
        raise FileNotFoundError(f"Helix runtime directory not found: {runtime}")
    shutil.copytree(runtime, root / "runtime")
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
        config.write(f"name: helix\narch: {arch}\nplatform: linux\nversion: 0.0.0-{version}\n")
        config.write("maintainer: Helix nightly builder\ndescription: Helix editor nightly build\n")
        config.write("contents:\n")
        config.write(f"  - src: {binary}\n    dst: /usr/bin/hx\n    file_info:\n      mode: 0755\n")
        config.write(f"  - src: {root / 'runtime'}\n    dst: /usr/lib/helix/runtime\n")
        config_path = Path(config.name)
    try:
        run(["nfpm", "package", "--packager", fmt, "--target", str(path), "--config", str(config_path)])
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


def make_msi(staging: Path, output_dir: Path, target: str, version: str) -> Path:
    if any(shutil.which(tool) is None for tool in ("candle", "light", "heat")):
        raise RuntimeError("WiX candle, heat, and light are required to create an MSI")
    root = next(staging.iterdir())
    source = output_dir / "helix.wxs"
    msi = output_dir / f"helix-{version}-{target}.msi"
    source.write_text(f'''<?xml version="1.0"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">
  <Product Name="Helix" Manufacturer="Helix" Version="1.0.0" Id="*" UpgradeCode="12345678-1234-1234-1234-123456789012">
    <Package InstallerVersion="500" Compressed="yes" />
    <MediaTemplate />
    <Directory Id="TARGETDIR" Name="SourceDir"><Directory Id="ProgramFilesFolder"><Directory Id="INSTALLFOLDER" Name="Helix">
      <Component Id="HelixBinary" Guid="*"><File Source="{root / 'hx.exe'}" KeyPath="yes" /></Component>
    </Directory></Directory></Directory>
    <Feature Id="MainFeature" Title="Helix" Level="1"><ComponentRef Id="HelixBinary" /><ComponentGroupRef Id="Runtime" /></Feature>
  </Product>
</Wix>''', encoding="utf-8")
    fragment = output_dir / "runtime.wxs"
    run(["heat", "dir", str(root / "runtime"), "-cg", "Runtime", "-dr", "INSTALLFOLDER", "-gg", "-sfrag", "-out", str(fragment)])
    candle = output_dir / "helix.wixobj"
    runtime_obj = output_dir / "runtime.wixobj"
    run(["candle", "-out", str(candle), str(source)])
    run(["candle", "-out", str(runtime_obj), str(fragment)])
    run(["light", "-out", str(msi), str(candle), str(runtime_obj)])
    source.unlink(missing_ok=True)
    fragment.unlink(missing_ok=True)
    candle.unlink(missing_ok=True)
    runtime_obj.unlink(missing_ok=True)
    return msi


def checksum(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(f"{path.name}.sha256").write_text(f"{digest}  {path.name}\n", encoding="utf-8")


def build(args: argparse.Namespace) -> list[Path]:
    output_dir = Path(args.output_dir).resolve()
    source_dir = Path(args.source_dir).resolve() if args.source_dir else Path(".helix-source").resolve()
    checkout(args.repo, args.ref, source_dir)
    version = run(["git", "rev-parse", "--short=12", "HEAD"], cwd=source_dir)
    target = args.target or host_target()
    cargo_args = ["cargo", "build", "--release", "--locked", "--package", "helix-term"]
    if args.target:
        cargo_args += ["--target", target]
    run(cargo_args, cwd=source_dir)
    binary_name = "hx.exe" if target.endswith("-windows-msvc") else "hx"
    binary_dir = source_dir / "target" / target / "release" if args.target else source_dir / "target" / "release"
    binary = binary_dir / binary_name
    if not binary.is_file():
        raise FileNotFoundError(f"Built Helix binary not found: {binary}")
    if args.grammar:
        run_grammar(source_dir, binary, args.qemu)
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
            if "msi" in formats:
                paths.append(make_msi(staging, output_dir, target, version))
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
    result.add_argument("--grammar", action="store_true", help="run hx grammar fetch/build")
    result.add_argument("--qemu", help="QEMU executable to prefix grammar commands")
    result.add_argument("--formats", default="archive", help="comma-separated: archive,deb,rpm,exe,msi")
    return result


def main() -> None:
    try:
        build(parser().parse_args())
    except (subprocess.CalledProcessError, OSError, RuntimeError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
