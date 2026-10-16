"""Check final release bytes and portable ZIP against the tested bundle."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import struct
import zipfile

root = Path(__file__).resolve().parents[1]
release = root / "release"
expected_names = {"GolfTraceOpen-Setup-0.1.0-win-x64.exe", "GolfTraceOpen-0.1.0-win-x64.zip"}
checksums = {}
for line in (release / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines():
    digest, name = line.split(None, 1)
    name = name.strip()
    if name not in expected_names or len(digest) != 64:
        raise RuntimeError("Unexpected checksum manifest entry")
    checksums[name] = digest
if set(checksums) != expected_names:
    raise RuntimeError("Incomplete checksum manifest")
files = []
for name, expected in checksums.items():
    path = release / name
    with path.open("rb") as source:
        actual = hashlib.file_digest(source, "sha256").hexdigest()
    if actual != expected:
        raise RuntimeError(f"SHA-256 mismatch: {name}")
    files.append({"name": name, "bytes": path.stat().st_size, "sha256": actual})
bundle = root / "dist" / "GolfTraceOpen"
archive = release / "GolfTraceOpen-0.1.0-win-x64.zip"
compared = 0
with zipfile.ZipFile(archive) as packaged:
    if packaged.testzip() is not None:
        raise RuntimeError("Portable ZIP CRC mismatch")
    names = set(packaged.namelist())
    for item in packaged.infolist():
        relative = PurePosixPath(item.filename)
        if relative.is_absolute() or ".." in relative.parts or relative.parts[0] != "GolfTraceOpen":
            raise RuntimeError("Unsafe or unexpected ZIP path")
        if item.is_dir():
            continue
        if any(part in {".env", "config.toml", "PROJECT_MEMORY.md"} for part in relative.parts):
            raise RuntimeError("Private configuration included")
        original = bundle.joinpath(*relative.parts[1:])
        if not original.is_file():
            raise RuntimeError(f"ZIP file missing from tested bundle: {relative}")
        with packaged.open(item) as archived, original.open("rb") as source:
            if hashlib.file_digest(archived, "sha256").digest() != hashlib.file_digest(source, "sha256").digest():
                raise RuntimeError(f"Portable bytes differ from tested bundle: {relative}")
        compared += 1
    if not any(name.endswith("PYTHON-LICENSE.txt") for name in names) or not any("LGPL" in name.upper() for name in names):
        raise RuntimeError("Required runtime license notices are absent")
    if "GolfTraceOpen/GolfTraceOpen.exe" not in names:
        raise RuntimeError("Main executable absent from portable ZIP")
with (bundle / "GolfTraceOpen.exe").open("rb") as source:
    source.seek(0x3C)
    offset = struct.unpack("<I", source.read(4))[0]
    source.seek(offset)
    if source.read(4) != b"PE\0\0" or struct.unpack("<H", source.read(2))[0] != 0x8664:
        raise RuntimeError("Main executable is not an AMD64 PE")
result = {"success": True, "release_files": files, "portable_crc": "passed",
          "portable_files_compared_to_bundle": compared, "main_machine": "AMD64",
          "runtime_execution": "tracked separately in frozen and installed exercise records"}
output = root / "artifacts" / "release-integrity.json"
output.write_text(json.dumps(result, indent=2), encoding="utf-8")
print(json.dumps(result, indent=2))
