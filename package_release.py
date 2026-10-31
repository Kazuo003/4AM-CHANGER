"""Create a complete offline distribution without copying local runtime data."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import tempfile
import zipfile

from core import VERSION, read_json, write_json

ROOT = Path(__file__).resolve().parent
NAME = f"4AM-Changer-{VERSION}-Windows-x64-full.zip"
SOURCE_FILES = (
    ".gitattributes", ".gitignore", "4AM-Changer.spec", "README.md", "app.py", "core.py",
    "build.ps1", "import_assets.py", "package_release.py", "requirements-build.txt",
    "run.bat", "tests/test_core.py",
)
ASSET_FILES = (
    "countries.json", "devices.json", "networks.json", "provenance.json",
    "adb/adb.exe", "adb/AdbWinApi.dll", "adb/AdbWinUsbApi.dll", "adb/libwinpthread-1.dll",
    "adb/NOTICE.txt", "adb/source.properties",
)
PRIVATE_PARTS = {"userdata", ".git", "__pycache__", ".venv", "venv", ".aws", ".codex", ".agents"}


def safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    parts = [part.casefold() for part in path.parts]
    return (not path.is_absolute() and ".." not in parts
            and not any(part in PRIVATE_PARTS for part in parts)
            and not any(part.startswith(".env") for part in parts)
            and not path.name.casefold().endswith((".log", ".rar"))
            and path.name.casefold() not in {"cookies.txt", "settings.json"})


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def package(dist: Path) -> Path:
    dist = dist.resolve()
    if not (dist / "4AM-Changer.exe").is_file():
        raise RuntimeError(f"Build the Windows executable first: {dist}")
    members = {}
    for path in sorted(dist.rglob("*")):
        if path.is_symlink():
            raise RuntimeError(f"Symlink is not permitted in the distribution: {path}")
        if not path.is_file():
            continue
        relative = path.relative_to(dist).as_posix()
        name = "4AM-Changer/" + relative
        if safe_member(name):
            members[name] = path
    for relative in SOURCE_FILES:
        members["4AM-Changer/source/" + relative] = ROOT / relative
    for relative in ASSET_FILES:
        source = ROOT / "assets" / relative
        members["4AM-Changer/source/assets/" + relative] = source
        bundled = dist / "_internal" / "assets" / relative
        if not bundled.is_file() or digest(bundled) != digest(source):
            raise RuntimeError(f"Bundled asset differs from the source asset: {relative}")
    for name, path in members.items():
        if not safe_member(name) or path.is_symlink() or not path.is_file():
            raise RuntimeError(f"Unsafe or missing packaged file: {name}")
    manifest = {
        "schema": "4am.distribution.v1", "version": VERSION, "archive": NAME,
        "platform": "Windows x64", "phone_write_tests": "not performed",
        "excluded": ["local settings", "cookies/tokens", "device backups", "activity logs", "original changer applications"],
        "files": {name: {"bytes": path.stat().st_size, "sha256": digest(path)}
                  for name, path in sorted(members.items())},
    }
    intro = (
        "4AM CHANGER - FULL OFFLINE PACKAGE\n\n"
        "Extract the entire ZIP and open 4AM-Changer.exe. Keep _internal beside it.\n"
        "Includes executable, Python/Tk, ADB, all reference catalogs and source.\n"
        "No Python installation or original Pchanger/Gchanger app is required.\n"
        "Source and detailed documentation are in the source folder.\n"
        "Phone apply requires root + resetprop. Real-phone apply/restore is untested.\n"
        "No personal settings, cookies, device backups or activity logs are included.\n"
    ).encode("utf-8")
    generated = {
        "4AM-Changer/START-HERE.txt": intro,
        "4AM-Changer/release-manifest.json": (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    }
    output_dir = ROOT / "packages"
    output_dir.mkdir(exist_ok=True)
    output = output_dir / NAME
    with tempfile.TemporaryDirectory(dir=output_dir, prefix=".packing-") as temporary:
        pending = Path(temporary) / NAME
        with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for name, path in sorted(members.items()):
                archive.write(path, name)
            for name, content in generated.items():
                archive.writestr(name, content)
        with zipfile.ZipFile(pending) as archive:
            if archive.testzip() is not None:
                raise RuntimeError("ZIP CRC verification failed.")
            if set(archive.namelist()) != set(members) | set(generated):
                raise RuntimeError("ZIP file inventory differs from the selected files.")
            for name, item in manifest["files"].items():
                with archive.open(name) as stream:
                    if hashlib.file_digest(stream, "sha256").hexdigest() != item["sha256"]:
                        raise RuntimeError(f"ZIP content hash mismatch: {name}")
        # Keep repository artifacts comfortably below the per-file upload cap.
        if pending.stat().st_size > 90_000_000:
            raise RuntimeError("Package exceeds the selected 90 MB repository artifact cap.")
        pending.replace(output)
    write_json(output_dir / "release-manifest.json", manifest)
    (output_dir / "SHA256SUMS.txt").write_text(f"{digest(output)}  {NAME}\n", encoding="utf-8", newline="\n")
    print(f"Verified {len(members) + len(generated)} ZIP entries, {output.stat().st_size:,} bytes: {output}")
    print("Excluded personal runtime data. All packaged file hashes match.")
    return output


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dist", type=Path, default=ROOT / "dist" / "4AM-Changer")
    package(parser.parse_args().dist)
