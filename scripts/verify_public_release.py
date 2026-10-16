"""Download public release bytes without authentication and compare SHA-256."""
import argparse
import hashlib
import json
from pathlib import Path
import time
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    args = parser.parse_args()
    if not args.base_url.startswith("https://github.com/"):
        parser.error("Expected an HTTPS GitHub release URL prefix")
    root = Path(__file__).resolve().parents[1]
    expected_files = []
    for line in (root / "release" / "SHA256SUMS.txt").read_text(encoding="ascii").splitlines():
        digest, name = line.split(None, 1)
        expected_files.append((name.strip(), digest))
    results = []
    for name, expected in expected_files:
        url = args.base_url.rstrip("/") + "/" + name
        for attempt in range(3):
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "GolfTraceOpen-release-verification"})
                with urllib.request.urlopen(request, timeout=45) as response:
                    hasher = hashlib.sha256()
                    byte_count = 0
                    while chunk := response.read(1024 * 1024):
                        hasher.update(chunk)
                        byte_count += len(chunk)
                    actual = hasher.hexdigest()
                if actual != expected:
                    raise RuntimeError(f"Public download checksum mismatch: {name}")
                results.append({"name": name, "url": url, "bytes": byte_count, "sha256": actual, "matches": True})
                print(f"Verified public download: {name} ({byte_count} bytes)", flush=True)
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2)
    record = {"success": True, "authentication_used": False, "files": results}
    target = root / "artifacts" / "public-download-verification.json"
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
