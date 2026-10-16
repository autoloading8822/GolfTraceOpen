"""Exercise actual licensed video I/O; no ground-truth flight-accuracy claim."""
import hashlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from golftrace.render import normalize_video
from golftrace.tracking import inspect_video, read_frame

root = Path(__file__).resolve().parents[1]
source = root / "samples" / "validation" / "golf-swing-cc0.webm"
destination = root / "samples" / "validation" / "golf-swing-cfr.mp4"
before = hashlib.file_digest(source.open("rb"), "sha256").hexdigest()
result = normalize_video(str(source), str(destination))
info = inspect_video(str(destination))
last = read_frame(str(destination), info.frame_count - 1)
assert last.shape[:2] == (1920, 1080)
assert before == hashlib.file_digest(source.open("rb"), "sha256").hexdigest()
record = {"scope": "Real CC0 footage sequential decode, CFR recovery copy, final-frame seeking, and source preservation only.",
          "automatic_tracking_accuracy": "not_measured_no_ground_truth_annotations",
          "source_sha256_unchanged": before, "normalization": result,
          "normalized_last_frame_readable": True,
          "license_source": "https://commons.wikimedia.org/wiki/File:Suvichaya_Vinijchaitham_Golf_Swing_Slow_Mo_2026.webm"}
output = root / "artifacts" / "real-footage" / "normalization.json"
output.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps(record, ensure_ascii=False, indent=2))
