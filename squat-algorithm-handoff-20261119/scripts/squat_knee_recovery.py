"""Experimental cycle recovery for frontal knee-inward screening."""
import statistics

import numpy as np
from scipy.signal import find_peaks
from squat_guidance import point_ok


def recover_hip_cycles(pose, *, conservative_boundaries=False):
    """Add observed hip descent/ascent cycles whose bottom falls in a gap."""
    analysis = pose['analysis']
    cycles = analysis['cycles']
    if analysis['quality']['status'] != 'usable_candidate':
        return pose
    blocks = []
    points_by_time = {f['t']:f.get('image',[]) for f in pose['frames']}
    for frame in analysis.get('frames', []):
        t, hip = frame['t'], frame.get('hip_y')
        points = points_by_time.get(t, [])
        if hip is None or not np.isfinite(hip) or not point_ok(points,(23,24,27,28)):
            continue
        if not blocks or t-blocks[-1][-1][0] > .17:
            blocks.append([])
        ankle_height = (points[27]['y']+points[28]['y'])/2
        blocks[-1].append((t, hip-ankle_height, frame.get('flexion')))
    recovered = []
    for block in blocks:
        if len(block) < 7:
            continue
        times = np.array([row[0] for row in block])
        raw = [row[1] for row in block]
        smooth = np.array([statistics.median(raw[max(0,i-1):i+2]) for i in range(len(raw))])
        step = statistics.median(np.diff(times))
        peaks,_ = find_peaks(smooth, prominence=.025, distance=max(1, round(.55/step)))
        for j, i in enumerate(peaks):
            t = times[i]
            if any(c['start_sec'] <= t <= c['end_sec'] for c in cycles):
                continue
            left_limit = peaks[j-1]+1 if j else 0
            right_limit = peaks[j+1] if j+1 < len(peaks) else len(times)
            before = [k for k in range(left_limit,i) if .19 <= t-times[k] <= 1.15]
            after = [k for k in range(i+1,right_limit) if .19 <= times[k]-t <= 1.15]
            if not before or not after:
                continue
            left = min(before,key=lambda k:(smooth[k],-k))
            right = min(after,key=lambda k:(smooth[k],k))
            if smooth[i]-max(smooth[left],smooth[right]) < .025:
                continue
            observed_flexion = [row[2] for row in block[left:right+1] if row[2] is not None]
            if not observed_flexion:
                continue
            # A visible reversal does not establish both endpoints when the
            # motion reaches a clip edge or a gap in the reliable pose track.
            clipped = left == 0 or right == len(block)-1
            boundary = 'incomplete' if conservative_boundaries and clipped else 'candidate_complete'
            recovered.append({'start_sec':float(times[left]),'bottom_sec':float(t),
                              'end_sec':float(times[right]),'bottom_flexion_deg':max(observed_flexion),
                              'boundary_status':boundary,'boundary_method':'hip_descent_ascent_gap'})
    return {**pose,'analysis':{**analysis,'cycles':cycles+recovered}}
