"""Re-estimate only squat knee flexion from denser pose samples.

One JSON checkpoint per clip makes a full 621-video run resumable. The v2 CSV,
frame metrics, and human reviews are read-only inputs. All derived data must
stay in an authorized local dataset environment.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import median

import cv2
import mediapipe as mp

from autolabel_squat_v2 import ANGLE_CONVENTION, FIELDS, angle_3d, angle_bin


METHOD_VERSION = "squat-angle-v3-10fps-supported-peak"
SIDE_JOINTS = ((23, 25, 27), (24, 26, 28))
ANGLE_FIELDS = ("angle_label", "max_knee_flexion_deg", "min_knee_internal_angle_deg", "angle_confidence")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sample_video(path: Path, target_fps: float):
    """Decode sequentially and sample by source frame index; no seek per frame."""
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise OSError(f"Cannot open video: {path}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not math.isfinite(fps) or fps <= 0 or fps > 240:
        fps = 30.0
    step = max(1, round(fps / target_fps))
    index = 0
    try:
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            if index % step == 0:
                yield round(index / fps, 3), bgr
            index += 1
    finally:
        cap.release()


def pose_frame(points, world, seconds: float) -> dict:
    """Retain side-specific angles and the three visibility values per side."""
    frame = {"t": seconds, "pose_detected": True}
    for side, joints in enumerate(SIDE_JOINTS):
        visibility = [round(points[i].visibility, 4) for i in joints]
        frame[f"visibility_{side}"] = visibility
        if min(visibility) < 0.55:
            continue
        internal = angle_3d(*(world[i] for i in joints))
        if internal is not None and math.isfinite(internal):
            frame[f"angle_{side}"] = round(internal, 2)
    return frame


def angle_result(frames: list[dict], camera_view: str) -> dict:
    """Select the deepest temporally or bilaterally supported observed frame."""
    series = []
    for frame in frames:
        sides = [180.0 - frame[key] for key in ("angle_0", "angle_1") if key in frame]
        if sides:
            series.append({"t": float(frame["t"]), "flexion": median(sides), "sides": sides})
    candidates = []
    for index, item in enumerate(series):
        temporal = any(
            other_index != index
            and 0 < abs(other["t"] - item["t"]) <= 0.201
            and abs(other["flexion"] - item["flexion"]) <= 20.0
            for other_index, other in enumerate(series)
        )
        bilateral = len(item["sides"]) == 2 and abs(item["sides"][0] - item["sides"][1]) <= 20.0
        if temporal or bilateral:
            candidates.append({**item, "temporal_support": temporal, "bilateral_support": bilateral})
    answer = {
        "angle_label": "unknown",
        "max_knee_flexion_deg": "",
        "min_knee_internal_angle_deg": "",
        "angle_confidence": "unknown",
        "angle_evidence_time_sec": "",
        "angle_valid_frames": len(series),
        "angle_sampled_frames": len(frames),
        "raw_peak_flexion_deg": round(max((item["flexion"] for item in series), default=0.0), 1) if series else "",
        "supported_peak_flexion_deg": "",
        "temporal_support": False,
        "bilateral_support": False,
        "abstention_reason": "no_valid_angle" if not series else "no_supported_peak",
    }
    if not candidates:
        return answer
    best = max(candidates, key=lambda item: (item["flexion"], item["bilateral_support"], item["temporal_support"], -item["t"]))
    flexion = round(best["flexion"], 1)
    answer.update(
        supported_peak_flexion_deg=flexion,
        angle_evidence_time_sec=best["t"],
        temporal_support=best["temporal_support"],
        bilateral_support=best["bilateral_support"],
    )
    margin = min(abs(flexion - 45.0), abs(flexion - 90.0))
    if margin <= 5.0 and not (best["temporal_support"] and best["bilateral_support"]):
        answer["abstention_reason"] = "boundary_weak_support"
        return answer
    answer.update(
        angle_label=angle_bin(flexion),
        max_knee_flexion_deg=flexion,
        min_knee_internal_angle_deg=round(180.0 - flexion, 1),
        angle_confidence="medium" if camera_view == "side" and best["temporal_support"] and best["bilateral_support"] and margin >= 8.0 else "low",
        abstention_reason="",
    )
    return answer


def infer_clip(path: Path, model: Path, target_fps: float, camera_view: str) -> dict:
    options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=0.5,
        min_pose_presence_confidence=0.5,
        min_tracking_confidence=0.5,
    )
    frames = []
    with mp.tasks.vision.PoseLandmarker.create_from_options(options) as landmarker:
        for t, bgr in sample_video(path, target_fps):
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))
            result = landmarker.detect_for_video(image, int(t * 1000) + 1)
            if result.pose_landmarks and result.pose_world_landmarks:
                frames.append(pose_frame(result.pose_landmarks[0], result.pose_world_landmarks[0], t))
            else:
                frames.append({"t": t, "pose_detected": False})
    if not frames:
        raise ValueError("No decoded frames")
    return {"result": angle_result(frames, camera_view), "frames": frames}


def merged_row(baseline: dict, result: dict) -> dict:
    row = dict(baseline)
    for field in ANGLE_FIELDS:
        row[field] = result[field]
    unknowns = sum(row[key] == "unknown" for key in ("width_label", "angle_label", "knees_over_toes", "plie", "hold"))
    deep = row["max_knee_flexion_deg"] != "" and float(row["max_knee_flexion_deg"]) > 135
    rare = row["hold"] == "yes" or row["plie"] == "yes" or deep
    row["review_priority"] = "high" if unknowns >= 3 or int(row["valid_frames"]) < 3 or rare else "medium" if unknowns else "low"
    return row


def atomic_json(path: Path, data: dict):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def publish(output: Path, baselines: list[dict]):
    csv_temp = output / "autolabels.csv.tmp"
    metric_temp = output / "angle_frame_metrics.jsonl.tmp"
    evidence_temp = output / "angle_evidence.jsonl.tmp"
    errors = []
    with csv_temp.open("w", encoding="utf-8-sig", newline="") as csv_stream, \
            metric_temp.open("w", encoding="utf-8") as metrics_stream, \
            evidence_temp.open("w", encoding="utf-8") as evidence_stream:
        writer = csv.DictWriter(csv_stream, fieldnames=FIELDS)
        writer.writeheader()
        for baseline in baselines:
            video_id = baseline["video_id"]
            item = json.loads((output / "clips" / f"{video_id}.json").read_text(encoding="utf-8"))
            result = item["result"]
            writer.writerow(merged_row(baseline, result))
            metrics_stream.write(json.dumps({"video_id": video_id, "frames": item["frames"]}, ensure_ascii=False) + "\n")
            evidence_stream.write(json.dumps({"video_id": video_id, **result}, ensure_ascii=False) + "\n")
            if item.get("error"):
                errors.append({"video_id": video_id, "error": item["error"]})
    os.replace(csv_temp, output / "autolabels.csv")
    os.replace(metric_temp, output / "angle_frame_metrics.jsonl")
    os.replace(evidence_temp, output / "angle_evidence.jsonl")
    atomic_json(output / "run_summary.json", {
        "method_version": METHOD_VERSION,
        "video_count": len(baselines),
        "angle_convention": ANGLE_CONVENTION,
        "errors": errors,
        "source_fields_preserved_except": list(ANGLE_FIELDS) + ["review_priority"],
    })


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-fps", type=float, default=10.0)
    parser.add_argument("--workers", type=int, default=4, help="Independent video workers; pose trackers are never shared")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--limit", type=int, default=0, help="Smoke test only; not for final 621-video run")
    args = parser.parse_args()
    if args.sample_fps <= 0 or not 1 <= args.workers <= 8:
        raise ValueError("sample-fps must be positive and workers must be between 1 and 8")
    baselines = list(csv.DictReader(args.baseline.open(encoding="utf-8-sig", newline="")))
    if len(baselines) != 621 or len({row["video_id"] for row in baselines}) != 621:
        raise ValueError("Expected 621 unique v2 baseline rows")
    if args.limit:
        baselines = baselines[:args.limit]
    config = {
        "method_version": METHOD_VERSION,
        "target_fps": args.sample_fps,
        "model_sha256": sha256(args.model),
        "baseline_sha256": sha256(args.baseline),
        "dataset": str(args.dataset.resolve()),
        "limit": args.limit,
    }
    if args.resume:
        if json.loads((args.output / "run_config.json").read_text(encoding="utf-8")) != config:
            raise ValueError("Resume configuration differs from initial run")
    else:
        args.output.mkdir(parents=True, exist_ok=False)
        (args.output / "clips").mkdir()
        atomic_json(args.output / "run_config.json", config)
    pending = [baseline for baseline in baselines if not (args.output / "clips" / f"{baseline['video_id']}.json").exists()]
    completed = len(baselines) - len(pending)
    print(f"Starting with {completed}/{len(baselines)} checkpoints; workers={args.workers}", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(infer_clip, args.dataset / baseline["video_path"], args.model,
                               args.sample_fps, baseline["camera_view"]): baseline for baseline in pending}
        for future in as_completed(futures):
            baseline = futures[future]
            video_id = baseline["video_id"]
            try:
                item = future.result()
            except Exception as exc:
                item = {"result": angle_result([], baseline["camera_view"]), "frames": [], "error": f"{type(exc).__name__}: {exc}"}
            atomic_json(args.output / "clips" / f"{video_id}.json", item)
            completed += 1
            if completed % 10 == 0 or completed == len(baselines):
                print(f"{completed}/{len(baselines)} {video_id}", flush=True)
    publish(args.output, baselines)
    print(json.dumps({"output": str(args.output.resolve()), "videos": len(baselines)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
