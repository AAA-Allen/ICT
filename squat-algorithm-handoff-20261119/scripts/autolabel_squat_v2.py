"""Pre-label the 621 QEVD Part-1 squat clips with MediaPipe Pose.

Every output is a *proposal* for human review. Unobservable measurements remain
unknown; source labels are kept separately and never treated as ground truth.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from statistics import median

import cv2
import mediapipe as mp


LM = {"ls": 11, "rs": 12, "lh": 23, "rh": 24, "lk": 25, "rk": 26,
      "la": 27, "ra": 28, "lheel": 29, "rheel": 30, "ltoe": 31, "rtoe": 32}
SIDES = (("lh", "lk", "la", "lheel", "ltoe"),
         ("rh", "rk", "ra", "rheel", "rtoe"))
ANGLE_CONVENTION = "knee_flexion_deg_straight_0_ge90"
FIELDS = ["video_id", "video_path", "width_label", "width_ratio", "width_confidence",
          "angle_label", "max_knee_flexion_deg", "min_knee_internal_angle_deg", "angle_convention", "angle_confidence",
          "knees_over_toes", "knee_toe_margin", "knee_toe_confidence",
          "plie", "plie_confidence", "hold", "hold_seconds", "hold_confidence",
          "camera_view", "valid_frames", "sampled_frames", "review_priority", "raw_labels"]


def distance(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)


def angle(a, b, c):
    ax, ay = a.x - b.x, a.y - b.y
    cx, cy = c.x - b.x, c.y - b.y
    den = math.hypot(ax, ay) * math.hypot(cx, cy)
    if den < 1e-6:
        return None
    return math.degrees(math.acos(max(-1, min(1, (ax * cx + ay * cy) / den))))


def angle_3d(a, b, c):
    u = (a.x - b.x, a.y - b.y, a.z - b.z)
    v = (c.x - b.x, c.y - b.y, c.z - b.z)
    den = math.sqrt(sum(x * x for x in u) * sum(x * x for x in v))
    if den < 1e-6:
        return None
    return math.degrees(math.acos(max(-1, min(1, sum(x * y for x, y in zip(u, v)) / den))))


def visible(points, *names, minimum=0.55):
    return all(points[LM[n]].visibility >= minimum for n in names)


def percentile(values, q):
    if not values:
        return None
    values = sorted(values)
    return values[round((len(values) - 1) * q)]


def width_bin(ratio):
    # Operational bins around the requested nominal shoulder-width and 1.5x widths.
    if ratio < 0.90:
        return "<shoulder-width"
    if ratio < 1.25:
        return "shoulder-width"
    if ratio <= 1.75:
        return "1.5shoulder-width"
    return ">1.5shoulder-width"


def angle_bin(degrees):
    if degrees < 45:
        return "<45 degrees"
    if degrees < 90:
        return "45-90 degrees"
    return ">=90 degrees"


def confidence_fraction(n, total, margin, required=3):
    if n < required or total <= 0:
        return "unknown"
    if n >= 6 and margin >= 0.12:
        return "medium"
    return "low"


def get_frames(path, sample_fps):
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps <= 0 or fps > 240:
        fps = 30
    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    step = max(1, round(fps / sample_fps))
    sampled = 0
    for idx in range(0, frame_count, step):
        cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
        ok, frame = cap.read()
        if not ok:
            continue
        sampled += 1
        yield idx / fps, frame
    cap.release()


def measure_frame(points, world, seconds):
    result = {"t": round(seconds, 3)}
    if visible(points, "ls", "rs", "lh", "rh"):
        shoulder_width = distance(points[11], points[12])
        torso = median((distance(points[11], points[23]), distance(points[12], points[24])))
        ratio = shoulder_width / max(torso, 0.001)
        result["view_ratio"] = round(ratio, 3)
        result["view"] = "side" if ratio < 0.23 else "front_or_oblique" if ratio > 0.32 else "oblique"
        if visible(points, "la", "ra", "lheel", "rheel") and shoulder_width > 0.04:
            foot_width = median((distance(points[27], points[28]), distance(points[29], points[30])))
            result["width_ratio"] = round(foot_width / shoulder_width, 3)

    for side, (hip, knee, ankle, heel, toe) in enumerate(SIDES):
        if visible(points, hip, knee, ankle):
            value = angle_3d(world[LM[hip]], world[LM[knee]], world[LM[ankle]])
            if value is not None:
                result[f"angle_{side}"] = round(value, 2)
        if visible(points, knee, heel, toe):
            h, t, k = (points[LM[x]] for x in (heel, toe, knee))
            forward = t.x - h.x
            foot_length = distance(h, t)
            if abs(forward) > 0.025 and foot_length > 0.035:
                result[f"knee_toe_{side}"] = round((k.x - t.x) * (1 if forward > 0 else -1) / foot_length, 3)
            result[f"toe_dx_{side}"] = round((t.x - h.x) / max(foot_length, 0.001), 3)
    return result


def aggregate(frames, raw_labels, path):
    row = {k: "" for k in FIELDS}
    row["video_id"] = path.name
    row["video_path"] = f"videos/{path.name}"
    row["raw_labels"] = json.dumps(raw_labels, ensure_ascii=False)
    row["angle_convention"] = ANGLE_CONVENTION
    row["sampled_frames"] = len(frames)
    row["valid_frames"] = sum("view" in f for f in frames)
    views = [f["view"] for f in frames if "view" in f]
    view = max(set(views), key=views.count) if views else "unknown"
    row["camera_view"] = view

    # Foot span projected into image X is informative mainly from front/oblique views.
    widths = [f["width_ratio"] for f in frames if "width_ratio" in f and f.get("view") == "front_or_oblique"]
    if len(widths) >= 3:
        ratio = median(widths)
        row["width_ratio"] = round(ratio, 3)
        row["width_label"] = width_bin(ratio)
        row["width_confidence"] = confidence_fraction(len(widths), len(frames), min(abs(ratio - b) for b in (.90, 1.25, 1.75)))
    else:
        row["width_label"] = "unknown"
        row["width_confidence"] = "unknown"

    # Saved frame angles are internal angles (straight=180). Keep bottom/hold
    # detection in that coordinate system and expose flexion (straight=0).
    knee_series = []
    for f in frames:
        candidates = [f[k] for k in ("angle_0", "angle_1") if k in f]
        if candidates:
            knee_series.append((f["t"], median(candidates)))
    low = percentile([x[1] for x in knee_series], .15)
    if low is not None and len(knee_series) >= 3:
        bottom = [(t, a) for t, a in knee_series if a <= low + 10]
        deepest = median([a for _, a in bottom]) if bottom else low
        flexion = 180.0 - deepest
        row["min_knee_internal_angle_deg"] = round(deepest, 1)
        row["max_knee_flexion_deg"] = round(flexion, 1)
        row["angle_label"] = angle_bin(row["max_knee_flexion_deg"])
        boundary_margin = min(abs(flexion - 45), abs(flexion - 90))
        row["angle_confidence"] = "medium" if view == "side" and len(knee_series) >= 6 and boundary_margin >= 8 else "low"
        # Bottom hold: longest near-bottom continuous interval, based on timestamps.
        streak_start = None
        previous = None
        best = 0.0
        for t, a in knee_series:
            if a <= low + 12:
                if streak_start is None or (previous is not None and t - previous > .55):
                    streak_start = t
                best = max(best, t - streak_start)
                previous = t
            else:
                streak_start = None
                previous = None
        row["hold_seconds"] = round(best, 2)
        if len(knee_series) >= 6:
            row["hold"] = "yes" if best >= 1.5 else "no" if best <= .67 else "unknown"
            row["hold_confidence"] = "medium" if row["hold"] != "unknown" else "low"
        else:
            row["hold"] = "unknown"
            row["hold_confidence"] = "unknown"
    else:
        row["angle_label"] = row["hold"] = "unknown"
        row["angle_confidence"] = row["hold_confidence"] = "unknown"

    bottom_times = {t for t, a in knee_series if low is not None and a <= low + 15}
    # Side projection is necessary for a meaningful knee-versus-toe comparison.
    knee_toe = [f[k] for f in frames if f["t"] in bottom_times and f.get("view") == "side"
                for k in ("knee_toe_0", "knee_toe_1") if k in f]
    if len(knee_toe) >= 3:
        margin = median(knee_toe)
        row["knee_toe_margin"] = round(margin, 3)
        row["knees_over_toes"] = "yes" if margin > .08 else "no" if margin < -.08 else "unknown"
        row["knee_toe_confidence"] = "medium" if abs(margin) > .18 and len(knee_toe) >= 6 else "low"
    else:
        row["knees_over_toes"] = "unknown"
        row["knee_toe_confidence"] = "unknown"

    # A 2D front-facing toe-out heuristic. It is deliberately conservative.
    outward = []
    for f in frames:
        if f.get("view") != "front_or_oblique" or "toe_dx_0" not in f or "toe_dx_1" not in f:
            continue
        outward.append((f["toe_dx_0"], f["toe_dx_1"]))
    if len(outward) >= 4:
        left, right = median(x[0] for x in outward), median(x[1] for x in outward)
        spread = abs(left - right)
        # The dataset's plie tag is only a weak hint; 2D toe spread alone gives
        # false positives in ordinary frontal squats, so require both signals.
        if "squats - plie" in raw_labels and spread > .75 and row["width_ratio"] != "" and float(row["width_ratio"]) > 1.25:
            row["plie"] = "yes"
        elif "squats - plie" not in raw_labels and spread < .35:
            row["plie"] = "no"
        else:
            row["plie"] = "unknown"
        row["plie_confidence"] = "low" if row["plie"] != "unknown" else "unknown"
    else:
        row["plie"] = "unknown"
        row["plie_confidence"] = "unknown"

    unknowns = sum(row[k] == "unknown" for k in ("width_label", "angle_label", "knees_over_toes", "plie", "hold"))
    rare_deep = row["max_knee_flexion_deg"] != "" and row["max_knee_flexion_deg"] > 135
    rare_positive = row["hold"] == "yes" or row["plie"] == "yes" or rare_deep
    row["review_priority"] = "high" if unknowns >= 3 or row["valid_frames"] < 3 or rare_positive else "medium" if unknowns >= 1 else "low"
    return row


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-fps", type=float, default=3.0)
    parser.add_argument("--limit", type=int, default=0, help="Development smoke test only")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    manifest = list(csv.DictReader((args.dataset / "squat_part1_manifest.csv").open(encoding="utf-8-sig", newline="")))
    if args.limit:
        manifest = manifest[:args.limit]
    options = mp.tasks.vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(args.model)),
        running_mode=mp.tasks.vision.RunningMode.VIDEO,
        num_poses=1,
        min_pose_detection_confidence=.5,
        min_pose_presence_confidence=.5,
        min_tracking_confidence=.5)
    csv_path = args.output / "autolabels.csv"
    metrics_path = args.output / "frame_metrics.jsonl"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as csv_file, \
         metrics_path.open("w", encoding="utf-8") as metrics_file:
        writer = csv.DictWriter(csv_file, fieldnames=FIELDS)
        writer.writeheader()
        for idx, item in enumerate(manifest, 1):
            path = args.dataset / item["video_path"]
            frames = []
            with mp.tasks.vision.PoseLandmarker.create_from_options(options) as landmarker:
                for t, bgr in get_frames(path, args.sample_fps):
                    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                    result = landmarker.detect_for_video(
                        mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb), int(t * 1000) + 1)
                    if result.pose_landmarks and result.pose_world_landmarks:
                        frames.append(measure_frame(result.pose_landmarks[0], result.pose_world_landmarks[0], t))
            raw_labels = json.loads(item["all_labels"])
            row = aggregate(frames, raw_labels, path)
            writer.writerow(row)
            metrics_file.write(json.dumps({"video_id": path.name, "frames": frames}, ensure_ascii=False) + "\n")
            if idx % 25 == 0 or idx == len(manifest):
                print(f"{idx}/{len(manifest)}", flush=True)


if __name__ == "__main__":
    main()
