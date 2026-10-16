"""Generate a contact sheet for visual review; does not assign ground truth."""
import argparse
import json
from pathlib import Path
import sys

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from golftrace.tracking import inspect_video, read_frame

parser = argparse.ArgumentParser()
parser.add_argument("video")
parser.add_argument("output")
args = parser.parse_args()
destination = Path(args.output)
destination.mkdir(parents=True, exist_ok=True)
info = inspect_video(args.video)
indices = np.linspace(0, info.frame_count - 1, 12, dtype=int)
tiles = []
decode_errors = []
for index in indices:
    try:
        frame = read_frame(args.video, int(index))
    except ValueError as error:
        decode_errors.append({"frame": int(index), "error": str(error)})
        tile = np.full((280, 340, 3), 25, np.uint8)
        cv2.putText(tile, f"frame {index}: decode failed", (10, 140), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (80, 80, 255), 1)
        tiles.append(tile)
        continue
    factor = min(320 / info.width, 250 / info.height)
    resized = cv2.resize(frame, (round(info.width * factor), round(info.height * factor)))
    tile = np.full((280, 340, 3), 25, np.uint8)
    x = (340 - resized.shape[1]) // 2
    tile[0:resized.shape[0], x:x + resized.shape[1]] = resized
    cv2.putText(tile, f"frame {index} / {index / info.fps:.2f}s", (10, 269), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (240, 240, 240), 1, cv2.LINE_AA)
    tiles.append(tile)
sheet = np.vstack([np.hstack(tiles[start:start + 4]) for start in (0, 4, 8)])
cv2.imwrite(str(destination / "contact-sheet.jpg"), sheet)
record = {"video": info.path, "width": info.width, "height": info.height, "fps": info.fps,
          "frame_count": info.frame_count, "duration": info.duration,
          "requested_sample_frames": [int(index) for index in indices],
          "decode_errors": decode_errors,
          "purpose": "Real licensed footage decoding and manual visual inspection; no automatic accuracy claim."}
(destination / "footage-inspection.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
print(json.dumps(record, indent=2))
