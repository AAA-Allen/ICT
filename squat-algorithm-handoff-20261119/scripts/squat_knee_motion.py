"""Experimental frontal knee movement screening relative to observed standing."""
import math
import statistics

from squat_guidance import contiguous_geometry_runs, hip_order, inward_score, knee_reference_offsets, point_ok

SIDES = ((23, 25, 27), (24, 26, 28))
STANDING_MAX_FLEXION = 25
MOVEMENT_FLEXION_MARGIN = 10
SCORE_THRESHOLD = .15


def tracked_frames(pose, diagnostics, side=None):
    flexions = {row['t']: row.get('flexion') for row in pose['analysis'].get('frames', [])}
    width, height = pose['width'], pose['height']
    legs = SIDES if side is None else (SIDES[side],)
    required = tuple(range(23, 29)) if side is None else (23, 24, *SIDES[side][1:])
    blocks = []
    previous = None
    for index, frame in enumerate(pose['frames']):
        time, points = frame['t'], frame.get('image', [])
        flexion = flexions.get(time)
        if flexion is None or not math.isfinite(flexion) or not point_ok(points, required):
            previous = None
            diagnostics.append('motion_landmarks_unusable')
            continue
        order = hip_order(points)
        if any(order*(points[right]['x']-points[left]['x']) <= 0
               for left,right in ((25,26),(27,28)) if point_ok(points, (left,right))):
            previous = None
            diagnostics.append('motion_landmark_order_ambiguous')
            continue
        projections = [inward_score(points, *leg, width, height, scale_geometry=True) for leg in legs]
        if any(value is None for value in projections):
            previous = None
            diagnostics.append('motion_projection_unusable')
            continue
        hip_width = abs(points[24]['x']-points[23]['x'])
        residual = [value*hip_width*width for value in projections]
        offsets = [knee_reference_offsets(points, *leg) for leg in legs]
        # With one visible leg, compare that ankle to its own standing
        # position rather than trusting the other ankle's predicted location.
        stance = abs(points[28]['x']-points[27]['x']) if side is None else points[SIDES[side][2]]['x']
        current = (index, time, flexion, offsets, hip_width, stance)
        if previous:
            prev_order, prev_time, prev_residual, prev_hip_width = previous
            step = time-prev_time
            jump_limit = max(4, .35*min(hip_width, prev_hip_width)*width*max(1, step/.1))
            if order != prev_order or any(abs(a-b) > jump_limit for a,b in zip(residual,prev_residual)):
                previous = None
                diagnostics.append('motion_knee_track_discontinuity')
            elif not 0 < step <= .16:
                previous = None
        if previous is None:
            blocks.append([])
        blocks[-1].append(current)
        previous = (order, time, residual, hip_width)
    return blocks


def standing_pair(block, edge, bottom, peak, phase):
    pairs = []
    for a, b in zip(block, block[1:]):
        if any(abs(frame[1]-edge) > .4 or frame[2] > STANDING_MAX_FLEXION
               or peak-frame[2] < 15 for frame in (a,b)):
            continue
        if abs(a[2]-b[2]) > 8 or (phase == 'down' and b[1] >= bottom) or (phase == 'up' and a[1] <= bottom):
            continue
        pairs.append((a,b))
    return min(pairs, key=lambda pair: (max(f[2] for f in pair), sum(abs(f[1]-edge) for f in pair)),
               default=None)


def deepest_window(samples, edge, bottom, min_excursion, fraction):
    """Walk from the bottom to the requested fraction of travel toward the phase endpoint."""
    lo, hi = sorted((edge, bottom))
    phase = [(time, value) for time, value in samples if lo <= time <= hi]
    if edge < bottom:
        phase.reverse()
    if len(phase) < 2 or abs(phase[0][0]-bottom) > .16 or abs(phase[-1][0]-edge) > .16:
        return None
    depth = phase[0][1] - phase[-1][1]
    if depth < min_excursion:
        return None
    boundary = phase[0][1] - fraction * depth
    for deep, shallow in zip(phase, phase[1:]):
        if abs(deep[0]-shallow[0]) > .16:
            return None
        if shallow[1] < boundary:
            cutoff = deep[0] + (shallow[0]-deep[0]) * (deep[1]-boundary) / (deep[1]-shallow[1])
            return tuple(sorted((cutoff, bottom)))
    return None


def knee_phase_windows(pose):
    """Estimate the latter half of descent and the first quarter of ascent by excursion."""
    analysis = pose['analysis']
    flexion = [(row['t'], row['flexion']) for row in analysis.get('frames', [])
               if row.get('flexion') is not None and math.isfinite(row['flexion'])]
    hip = []
    for frame in pose['frames']:
        points = frame.get('image', [])
        if point_ok(points, (23, 24, 27, 28)):
            hip.append((frame['t'], (points[23]['y'] + points[24]['y'] - points[27]['y'] - points[28]['y']) / 2))

    def smooth(samples):
        return [(time, statistics.median(value for t, value in samples[max(0, i-1):i+2]
                                         if abs(t-time) <= .16))
                for i, (time, _) in enumerate(samples)]

    flexion, hip = smooth(flexion), smooth(hip)
    windows = {}
    for index, cycle in enumerate(analysis['cycles'], 1):
        sources = ((hip, .025),) if cycle.get('boundary_method') == 'hip_descent_ascent_gap' else (
            (flexion, 15), (hip, .025))
        hold = cycle.get('bottom_window_sec') if cycle.get('bottom_hold_candidate_sec', 0) >= .3 else None
        for phase, edge, fraction in (('down', cycle['start_sec'], .5), ('up', cycle['end_sec'], .25)):
            for samples, min_excursion in sources:
                window = deepest_window(samples, edge, cycle['bottom_sec'], min_excursion, fraction)
                if window is not None:
                    start, end = window
                    if phase == 'up' and hold:
                        start = max(start, hold[1])
                    if start <= end:
                        windows.setdefault(index, {})[phase] = (start, end)
                        break
    return windows


def local_horizontal_events(run, side, name, points_by_time, hip_width, image_width, cycle, phase,
                            *, threshold=SCORE_THRESHOLD, check_ankle=False):
    """Find horizontal movement within one phase and one continuous track."""
    hip, knee, ankle = SIDES[('left', 'right').index(name)]
    first_points = points_by_time[run[0][0]]
    direction = hip_order(first_points) * (1 if hip == 23 else -1)
    samples = [(t, (direction*points_by_time[t][knee]['x'], *row[3][side])) for t,row in run]
    trigger = max(threshold * hip_width, 4 / image_width)
    ankle_tolerance = max(4 / image_width, .15 * hip_width)
    confirmed_through = -math.inf
    for index, (time, values) in enumerate(samples):
        if time <= confirmed_through:
            continue
        for start, reference in reversed(samples[:index]):
            elapsed = time - start
            if elapsed > .251:
                break
            if start < confirmed_through or elapsed < .069:
                continue
            if check_ankle:
                ankle_positions = [points_by_time[t][ankle]['x'] for t, _ in samples[:index+1] if t >= start]
                if max(ankle_positions) - min(ankle_positions) > ankle_tolerance:
                    continue
            change = min(current - initial for current, initial in zip(values, reference))
            if change < trigger:
                continue
            for end, later in samples[index+1:]:
                if end - time > .161:
                    break
                if check_ankle:
                    ankle_positions.append(points_by_time[end][ankle]['x'])
                    if max(ankle_positions) - min(ankle_positions) > ankle_tolerance:
                        break
                support = min(current - initial for current, initial in zip(later, reference))
                if support < .75 * trigger:
                    break
                if end - time < .059:
                    continue
                yield {'cycle_index': cycle, 'side': name, 'phase': phase,
                       'start_sec': start, 'end_sec': end,
                       'evidence': {'method': 'local horizontal knee travel with hip and ankle agreement',
                                    'motion_reference_sec': start, 'trigger_sec': time,
                                    'confirmation_sec': end, 'min_horizontal_change_px': round(change * image_width, 3),
                                    'normalized_inward_change': round(change / hip_width, 3),
                                    'threshold': round(trigger / hip_width, 6),
                                    'threshold_validation': 'heuristic_unvalidated'}}
                confirmed_through = end
                break
            if time <= confirmed_through:
                break


def analyze_knee_motion(pose, view, diagnostics=None, *, trace=None, per_leg=False, local_horizontal=False,
                        phase_windows=None):
    diagnostics = [] if diagnostics is None else diagnostics
    cycles = pose['analysis']['cycles']
    result = {'events': [], 'evaluated_cycles': 0, 'total_cycles': len(cycles)}
    if view != 'front' or pose['analysis']['quality']['status'] != 'usable_candidate' or not cycles:
        diagnostics.append('motion_unsupported_view' if view != 'front' else
                           'motion_pose_quality' if pose['analysis']['quality']['status'] != 'usable_candidate'
                           else 'motion_no_cycle')
        return result

    points_by_time = {frame['t']: frame.get('image', []) for frame in pose['frames']} if local_horizontal else {}
    leg_names = ('left', 'right')
    tracks = ([(block, (name,)) for side, name in enumerate(leg_names)
               for block in tracked_frames(pose, diagnostics, side)] if per_leg
              else [(block, leg_names) for block in tracked_frames(pose, diagnostics)])
    for number, cycle in enumerate(cycles, 1):
        start, bottom, end = (cycle[k] for k in ('start_sec','bottom_sec','end_sec'))
        peak = cycle['bottom_flexion_deg']
        hold = cycle.get('bottom_window_sec') if cycle.get('bottom_hold_candidate_sec', 0) >= .3 else None
        evaluated = False
        has_anchor = False
        for block, names in tracks:
            if block[0][1] > end or block[-1][1] < start:
                continue
            before = standing_pair(block, start, bottom, peak, 'down')
            after = standing_pair(block, end, bottom, peak, 'up')
            if not (before or after):
                if trace is not None:
                    trace.append({'cycle_index': number, 'phase': None, 'sides': names,
                                  'reference_times_sec': [], 'phase_frames': [],
                                  'block_times': [row[1] for row in block if start <= row[1] <= end],
                                  'rejected_frames': {}, 'scores': {'left': [], 'right': []}})
                continue
            has_anchor = True
            for phase in ('down', 'up'):
                anchor = (before or after) if phase == 'down' else (after or before)
                left, right = (start,bottom) if phase == 'down' else (bottom,end)
                if phase_windows is not None:
                    window = phase_windows.get(number, {}).get(phase)
                    if window is None:
                        continue
                    left, right = window
                standing_angle = statistics.median(row[2] for row in anchor)
                hip_width = statistics.median(row[4] for row in anchor)
                stance = statistics.median(row[5] for row in anchor)
                stance_scale = hip_width if per_leg else stance
                phase_frames, rejected_frames = [], {}
                for row in block:
                    time = row[1]
                    if not left <= time <= right:
                        continue
                    reasons = []
                    if row[2] < standing_angle + MOVEMENT_FLEXION_MARGIN:
                        reasons.append('near_standing')
                    if abs(row[5]-stance) > max(4/pose['width'], .15*stance_scale):
                        reasons.append('ankle_position_changed')
                    if hold and hold[0] <= time <= hold[1]:
                        reasons.append('bottom_hold_window')
                    if not reasons:
                        phase_frames.append(row)
                    elif trace is not None:
                        rejected_frames[time] = reasons
                frame_order = {row[1]: row[0] for row in phase_frames}
                usable = [run for run in contiguous_geometry_runs([(row[1], row) for row in phase_frames], frame_order)
                          if len(run) >= 3 and run[-1][0]-run[0][0] >= .19]
                phase_trace = None
                if trace is not None:
                    phase_trace = {'cycle_index': number, 'phase': phase, 'sides': names,
                                   'reference_times_sec': [row[1] for row in anchor],
                                   'phase_frames': [row[1] for row in phase_frames],
                                   'rejected_frames': rejected_frames,
                                   'scores': {'left': [], 'right': []}}
                    trace.append(phase_trace)
                if not usable:
                    continue
                evaluated = True
                for side, name in enumerate(names):
                    baseline = [statistics.median(row[3][side][i] for row in anchor) for i in range(2)]
                    for run in usable:
                        scores = [(t, min(a-b for a,b in zip(row[3][side], baseline))/hip_width) for t,row in run]
                        if phase_trace is not None:
                            phase_trace['scores'][name].append(scores)
                        if local_horizontal:
                            result['events'].extend(local_horizontal_events(
                                run, side, name, points_by_time, hip_width, pose['width'], number, phase))
                            continue
                        above = [point for point in scores if point[1] >= SCORE_THRESHOLD]
                        for event in contiguous_geometry_runs(above, frame_order):
                            if len(event) < 3 or event[-1][0]-event[0][0] < .19:
                                continue
                            result['events'].append({
                                'cycle_index': number, 'side': name, 'phase': phase,
                                'start_sec': event[0][0], 'end_sec': event[-1][0],
                                'evidence': {'method': 'change from same-track personal standing reference',
                                             'reference_times_sec': [row[1] for row in anchor],
                                             'reference_flexion_deg': round(standing_angle, 1),
                                             'max_normalized_inward_change': round(max(s for _,s in event), 3),
                                             'frames': len(event), 'threshold': SCORE_THRESHOLD,
                                             'threshold_validation': 'heuristic_unvalidated'}})
                            if per_leg:
                                result['events'][-1]['evidence']['tracking'] = 'independent_leg'
        result['evaluated_cycles'] += int(evaluated)
        diagnostics.append('motion_evaluated' if evaluated else 'motion_no_stable_standing' if not has_anchor
                           else 'motion_no_continuous_phase')
    return result
