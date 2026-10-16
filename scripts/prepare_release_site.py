"""Create public-only release metadata from actual artifact bytes."""
import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument("--base-url", help="Verified public asset URL prefix; omit to retain unpublished state")
parser.add_argument("--source-url")
args = parser.parse_args()
for url in (args.base_url, args.source_url):
    if url and urlparse(url).scheme != "https":
        parser.error("Public URLs must use HTTPS")
files = []
for kind, name in (("installer", "GolfTraceOpen-Setup-0.1.0-win-x64.exe"), ("portable", "GolfTraceOpen-0.1.0-win-x64.zip")):
    artifact = root / "release" / name
    if not artifact.is_file():
        raise SystemExit(f"Missing release artifact: {name}")
    with artifact.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    files.append({"kind": kind, "name": name, "bytes": artifact.stat().st_size,
                  "sha256": digest, "url": f"{args.base_url.rstrip('/')}/{name}" if args.base_url else None})
record = {"published": bool(args.base_url), "version": "0.1.0", "files": files, "source_url": args.source_url}
(root / "website" / "dist" / "release.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({"published": record["published"], "files": len(files)}, ensure_ascii=False))
