"""Competition release of the knee-inward review policy."""
import statistics

from squat_guidance import analyze_inward, hip_order, wide_bilateral_ascent
from squat_knee_motion import analyze_knee_motion, knee_phase_windows, local_horizontal_events, tracked_frames
from squat_knee_recovery import recover_hip_cycles

KNEE_RULE_VERSION = 'knee-inward-review-v3'
KNEE_RECALL_VERSION = 'knee-inward-recall-v4'
SHORT_REVIEW_THRESHOLD = .28
RECALL_THRESHOLD = .08


def short_motion_for_review(pose, phase_windows, *, high_recall=False):
    """Find strong horizontal shifts on a visible leg within the current phase."""
    points = {frame['t']: frame.get('image', []) for frame in pose['frames']}
    tracks = [tracked_frames(pose, [], side) for side in range(2)]
    other_side_frames = [{row[1]: (number, row[3][0]) for number, block in enumerate(blocks) for row in block}
                         for blocks in tracks]
    for side, name in enumerate(('left', 'right')):
        opposite = other_side_frames[1 - side]
        for block in tracks[side]:
            for number, phases in phase_windows.items():
                for phase, (start, end) in phases.items():
                    run = [(row[1], row) for row in block if start <= row[1] <= end]
                    if len(run) < 3:
                        continue
                    width = statistics.median(row[4] for _, row in run)
                    for event in local_horizontal_events(
                        run, 0, name, points, width, pose['width'], number, phase,
                        threshold=RECALL_THRESHOLD if high_recall else SHORT_REVIEW_THRESHOLD,
                        check_ankle=not high_recall,
                    ):
                        if high_recall:
                            yield event
                            continue
                        reference = event['evidence']['motion_reference_sec']
                        trigger = event['evidence']['trigger_sec']
                        confirmation = event['evidence']['confirmation_sec']
                        first, peak, last = (opposite.get(t) for t in (reference, trigger, confirmation))
                        if not (first and peak and last) or first[0] != peak[0] or first[0] != last[0]:
                            continue
                        other_change = min(b - a for a, b in zip(first[1], peak[1])) * pose['width']
                        change = event['evidence']['min_horizontal_change_px']
                        if other_change > .5 * change:
                            continue
                        evidence = {**event['evidence'], 'opposite_leg_horizontal_change_px': round(other_change, 3)}
                        yield {**event, 'evidence': evidence, 'review_reason': 'short_asymmetric_horizontal_shift'}


def observed_reference_fallback(pose, view, phase_windows):
    """Corroborate early-cycle relative evidence with tracked horizontal knee travel."""
    trace = {}
    original = analyze_inward(pose, view, allow_partial_cycles=True, scale_geometry=True,
                              dual_reference=True, trace=trace, local_reference=True,
                              score_threshold=RECALL_THRESHOLD, minimum_event_frames=2,
                              minimum_event_span=.079, phase_windows=phase_windows)
    original['evaluated_cycle_ids'] = set(trace)
    if not original['events']:
        return original
    frames = {frame['t']: frame.get('image', []) for frame in pose['frames']}
    tracks = [tracked_frames(pose, [], side) for side in range(2)]
    accepted = []
    for event in original['events']:
        side = ('left', 'right').index(event['side'])
        knee = (25, 26)[side]
        cycle = pose['analysis']['cycles'][event['cycle_index'] - 1]
        reference_times = trace[event['cycle_index']]['reference_times']
        reference = [frames[t] for t in reference_times]
        order = hip_order(reference[0])
        if any(hip_order(points) != order for points in reference):
            continue
        direction = order * (1 if side == 0 else -1)
        knee_x = statistics.median(points[knee]['x'] for points in reference)
        hip_width = statistics.median(abs(p[24]['x']-p[23]['x']) for p in reference)
        minimum = max(2 / pose['width'], .05 * hip_width)
        scores = dict(trace[event['cycle_index']]['scores'][event['side']])
        for block in tracks[side]:
            supported = []
            for position, (index, t, *_) in enumerate(block):
                if not event['start_sec'] <= t <= event['end_sec']:
                    continue
                points = frames[t]
                dx = direction * (points[knee]['x'] - knee_x)
                recent = []
                for previous in reversed(block[:position]):
                    age = t - previous[1]
                    if age > .40:
                        break
                    if age >= .14:
                        recent.append(frames[previous[1]][knee]['x'])
                if len(recent) >= 2:
                    dx = max(dx, direction * (points[knee]['x'] - statistics.median(recent)))
                if hip_order(points) == order and dx >= minimum and scores.get(t, 0) >= RECALL_THRESHOLD:
                    if supported and index != supported[-1][0] + 1:
                        supported = []
                    supported.append((index, t, dx))
                    if len(supported) >= 2 and t - supported[0][1] >= .079:
                        start = supported[0][1]
                        phase = 'down' if t <= cycle['bottom_sec'] else 'up' if start >= cycle['bottom_sec'] else 'down_and_up'
                        accepted.append({**event, 'start_sec': start, 'end_sec': t, 'phase': phase,
                                         'evidence': {**event['evidence'], 'method': 'early-cycle reference and horizontal knee travel',
                                                      'reference_times_sec': reference_times,
                                                      'min_horizontal_change_px': round(min(row[2] for row in supported)*pose['width'], 3),
                                                      'frames': len(supported)}})
                        break
    return {**original, 'events': accepted}


def screen_knee_inward(pose, view, action=None, diagnostics=None, *, recall_fallback=False):
    original_pose = pose
    pose = recover_hip_cycles(pose, conservative_boundaries=True) if view == 'front' else pose
    windows = knee_phase_windows(pose) if view == 'front' else {}
    trace = [] if recall_fallback else None
    baseline = analyze_knee_motion(pose, view, diagnostics, per_leg=True, trace=trace, phase_windows=windows)
    events, deferred = wide_bilateral_ascent(baseline['events'], action)
    review = [{**event, 'review_reason': 'bilateral_wide_stance_ascent'} for event in deferred]
    evaluated_cycles = {item['cycle_index'] for item in trace or ()
                        if any(item['scores'].values())}
    if recall_fallback and view == 'front':
        fallback = observed_reference_fallback(original_pose, view, windows)
        events.extend(event for event in fallback['events'] if not any(
            event['side'] == current['side']
            and max(event['start_sec'], current['start_sec']) < min(event['end_sec'], current['end_sec'])
            for current in events))
        evaluated_cycles.update(fallback['evaluated_cycle_ids'])
    if view == 'front' and pose['analysis']['quality']['status'] == 'usable_candidate':
        for event in short_motion_for_review(pose, windows, high_recall=recall_fallback):
            if not any(other['side'] == event['side']
                       and other['cycle_index'] == event['cycle_index']
                       and other['phase'] == event['phase']
                       and max(other['start_sec'], event['start_sec']) < min(other['end_sec'], event['end_sec'])
                       for other in (*events, *review)):
                (events if recall_fallback else review).append(event)
    if recall_fallback:
        evaluated_cycles.update(event['cycle_index'] for event in (*events, *review))
        baseline['evaluated_cycles'] = len(evaluated_cycles)
    event_order = lambda e: (e['start_sec'], e['end_sec'], e['side'])
    return {**baseline, 'rule_version': KNEE_RECALL_VERSION if recall_fallback else KNEE_RULE_VERSION,
            'events': sorted(events, key=event_order),
            'review_events': sorted(review, key=event_order), 'cycles': pose['analysis']['cycles'],
            'phase_windows': windows}


def knee_inward_observation(result, view):
    events, review = result['events'], result['review_events']
    high_recall = result['rule_version'] == KNEE_RECALL_VERSION
    if events:
        status, reason_code, reason = 'candidate', 'standing_relative_motion', '观察到膝中心相对个人站姿向内移动，请结合原视频复核。'
        if high_recall:
            reason_code, reason = 'horizontal_motion_high_recall', '找到可能的水平膝内移片段，请按时间和侧别复核。'
    elif review:
        status, reason_code, reason = 'review_required', 'motion_needs_review', '检测到一段需要复核的水平位移，请查看标出的时段。'
    elif view != 'front':
        status, reason_code, reason = 'unknown', 'unsupported_view', '请提供正面拍摄的深蹲视频。'
    elif result['evaluated_cycles']:
        status, reason_code, reason = 'unknown', 'no_candidate', '在下蹲后半段或起身前四分之一的可分析片段中暂未检出候选。'
    else:
        status, reason_code, reason = 'unknown', 'insufficient_motion_evidence', '动作检测窗口、可见的腿部轨迹或站姿参照证据不足。'
    windows = [{'cycle_index': index, **{phase: [round(t, 3) for t in span] for phase, span in phases.items()}}
               for index, phases in result['phase_windows'].items()]
    return {'type': 'knee_inward', 'rule_version': result['rule_version'], 'validation': 'development_review_only',
            'status': status, 'reason_code': reason_code, 'reason': reason,
            'events': events, 'review_events': review, 'analysis_windows_sec': windows, 'advice': None,
            'evaluated_cycles': result['evaluated_cycles'], 'total_cycles': result['total_cycles'],
            'limitations': ['二维视频中的膝中心水平位移，仅供动作复核。']}
