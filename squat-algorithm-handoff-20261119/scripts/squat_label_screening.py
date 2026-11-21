"""Development screening for the complete Part1 squat label set."""
import math
import statistics

from squat_guidance import contiguous_geometry_runs, point_ok
from squat_heel_raise import analyze_foot_raise

LABELS = ('knee_inward', 'heel_raise', 'forefoot_raise', 'medial_foot_raise', 'lateral_foot_raise',
          'back_rounding', 'lumbar_overextension', 'torso_coordination', 'lateral_shift', 'balance', 'plie', 'hold')
REASON = {
    'medial_foot_raise': '需要看清鞋底内缘与地面接触变化，现有骨架缺少足内缘点。',
    'lateral_foot_raise': '需要看清鞋底外缘与地面接触变化，现有骨架缺少足外缘点。',
    'back_rounding': '需要看清脊柱轮廓或取得脊柱分段关键点。',
    'lumbar_overextension': '需要看清腰椎与骨盆的相对角度，现有骨架缺少腰椎分段点。',
}
SIGNALS = {
    'torso_coordination': '起身早段髋部上升快于肩部',
    'lateral_shift': '髋中心相对双脚持续向同侧移动',
    'balance': '动作中单脚横向移动且抬高',
    'plie': '双脚跟至前脚掌的方向均明显外开',
    'hold': '屈曲角持续处于最低点附近',
}


def observation(label, events=(), evaluated=0, *, reason=None):
    status = 'candidate' if events else 'unknown'
    code = 'motion_candidate' if events else 'no_candidate' if evaluated else 'insufficient_evidence'
    version = 'squat-label-screening-v2' if label in ('hold', 'lateral_shift') else 'squat-label-screening-v1'
    return {'type': label, 'rule_version': version, 'status': status,
            'reason_code': code, 'reason': reason or (SIGNALS[label] + '，请按时段复核。' if events else
                 '有效片段未触发候选。' if evaluated else '画面或动作参照不足，无法判定。'),
            'events': list(events), 'evaluated_cycles': evaluated,
            'validation': 'development_review_only', 'advice': None}


def event(cycle, phase, start, end, side, evidence):
    return {'cycle_index': cycle, 'phase': phase, 'start_sec': start, 'end_sec': end,
            'side': side, 'evidence': evidence}


def projected_frames(pose):
    width, height = pose['width'], pose['height']
    for frame in pose['frames']:
        points = frame.get('image', [])
        if not point_ok(points, (11, 12, 23, 24, 27, 28)):
            continue
        shoulder, hip, ankle = (tuple((points[a][axis]+points[b][axis])*size/2
                                for axis, size in (('x', width), ('y', height)))
                                for a, b in ((11,12),(23,24),(27,28)))
        scale = math.hypot(hip[0]-shoulder[0], hip[1]-shoulder[1])
        if scale >= 20:
            feet = tuple((points[i]['x']*width, points[i]['y']*height) for i in (27,28))
            yield frame['t'], (shoulder, hip, ankle, scale, feet)


def toe_opening(frame):
    if not point_ok(frame.get('image', []), (23,24,29,30,31,32)):
        return None
    points = frame.get('world', [])
    if len(points) != 33:
        return None
    values = [points[i][axis] for i in (23,24,29,30,31,32) for axis in ('x','z')]
    if not all(math.isfinite(value) for value in values):
        return None
    across = tuple(points[23][axis]-points[24][axis] for axis in ('x','z'))
    span = math.hypot(*across)
    if span < .08:
        return None
    angles = []
    for heel, toe, sign in ((29,31,1),(30,32,-1)):
        foot = tuple(points[toe][axis]-points[heel][axis] for axis in ('x','z'))
        length = math.hypot(*foot)
        if length < .06:
            return None
        outward = sign * sum(a*b for a,b in zip(foot, across)) / (span * length)
        angles.append(math.degrees(math.asin(max(-1, min(1, outward)))))
    return min(angles)


def screen_other_labels(pose, view, cycles):
    """Return all non-knee labels using the same cycle indices as guidance."""
    pose = {**pose, 'analysis': {**pose['analysis'], 'cycles': cycles}}
    results = [analyze_foot_raise(pose, view, high_recall=True, forefoot=forefoot)
               for forefoot in (False, True)]
    results.extend(observation(label, reason=reason) for label, reason in REASON.items())
    found = {label: [] for label in SIGNALS}
    counts = dict.fromkeys(SIGNALS, 0)
    if pose['analysis']['quality']['status'] == 'usable_candidate':
        evaluate_motion_cues(pose, view, cycles, found, counts)
    for label in SIGNALS:
        result = observation(label, found[label], counts[label])
        if label == 'balance' and view == 'front' and pose['analysis']['quality']['status'] == 'usable_candidate':
            result['rule_version'] = 'balance-local-step-review-v2'
            result['review_events'] = local_step_reviews(pose, cycles)
            if result['review_events'] and not result['events']:
                result.update(status='review_required', reason_code='local_step_needs_review',
                              reason='观察到足部短时抬高并横移，请查看是否为失衡后的调整步。')
        results.append(result)
    return results


def local_step_reviews(pose, cycles):
    """Flag local lift-and-step evidence even when the initial standing frame is hidden."""
    samples = list(projected_frames(pose))
    order = {frame['t']: i for i, frame in enumerate(pose['frames'])}
    found = []
    for side, name in enumerate(('left', 'right')):
        scores = []
        for index, (time, (_, _, _, scale, feet)) in enumerate(samples):
            earlier = [(t, value) for t,value in samples[:index] if .2 <= time-t <= .5]
            if len(earlier) < 2 or any(order[b]-order[a] != 1 for (a,_),(b,_) in zip(earlier,earlier[1:])):
                continue
            if time-earlier[-1][0] > .301:
                continue
            path = [(t,value) for t,value in samples if earlier[0][0] <= t <= time]
            if any(order[b]-order[a] != 1 or b-a > .16 for (a,_),(b,_) in zip(path,path[1:])):
                continue
            reference = tuple(statistics.median(value[4][side][axis] for _,value in earlier) for axis in (0,1))
            shift = abs(feet[side][0]-reference[0])/scale
            rise = (reference[1]-feet[side][1])/scale
            scores.append((time,min(shift/.15, rise/.07)))
        for run in contiguous_geometry_runs([(t,s) for t,s in scores if s >= 1],order):
            if len(run) < 3 or run[-1][0]-run[0][0] < .17:
                continue
            start, end = run[0][0], run[-1][0]
            cycle = next((i for i,c in enumerate(cycles,1) if c['start_sec'] <= start <= end <= c['end_sec']), None)
            found.append(event(cycle,'unknown',start,end,name,
                               {'method':'local ankle lift and horizontal travel',
                                'reference_lookback_sec':[.2,.5], 'horizontal_threshold_body_ratio':.15,
                                'vertical_threshold_body_ratio':.07, 'validation':'heuristic_unvalidated'}))
    return found


def evaluate_motion_cues(pose, view, cycles, found, counts):
    frame_order = {f['t']: i for i, f in enumerate(pose['frames'])}
    flexion = {f['t']: f.get('flexion') for f in pose['analysis'].get('frames', [])}
    frames = list(projected_frames(pose)) if view in ('front', 'side', 'oblique', 'front_or_oblique') else []
    toes = [(f['t'], angle) for f in pose['frames'] if (angle := toe_opening(f)) is not None]
    holds, evaluated = bottom_holds(flexion, frame_order, cycles)
    found['hold'].extend(holds)
    counts['hold'] = evaluated
    for number, cycle in enumerate(cycles, 1):
        start, bottom, end = (cycle[k] for k in ('start_sec', 'bottom_sec', 'end_sec'))
        emit_windows('plie', [(t,v) for t,v in toes if start <= t <= end],
                     30, number, bottom, frame_order, found, counts)
        visible = [(t,v) for t,v in frames if start <= t <= end]
        if len(visible) < 3:
            continue
        at_bottom = [(t,v) for t,v in visible if abs(t-bottom) <= .12]
        if len(at_bottom) >= 2:
            _, (shoulder, hip, _, scale, _) = min(at_bottom, key=lambda x: abs(x[0]-bottom))
            ascent = []
            for t, (current_shoulder, current_hip, _, _, _) in visible:
                hip_rise = hip[1]-current_hip[1]
                if bottom <= t <= min(end, bottom+.55):
                    ascent.append((t, min(hip_rise, hip_rise-(shoulder[1]-current_shoulder[1]))/scale))
            emit_windows('torso_coordination', ascent, .13, number, bottom, frame_order, found, counts)
        anchors = [(t,v) for t,v in visible if t <= min(bottom, start+.35)
                   and flexion.get(t) is not None and flexion[t] <= min(40, cycle['bottom_flexion_deg']-12)]
        if len(anchors) < 2 or view != 'front':
            continue
        _, (_, hip, ankle, scale, feet) = min(anchors, key=lambda x: flexion[x[0]])
        left_sign = 1 if feet[0][0] > feet[1][0] else -1
        stance = left_sign * (feet[0][0]-feet[1][0])
        shifts, steps = [], []
        for t, (_, cur_hip, cur_ankle, _, cur_feet) in visible:
            hip_dx = (cur_hip[0]-cur_ankle[0])-(hip[0]-ankle[0])
            current_stance = left_sign * (cur_feet[0][0]-cur_feet[1][0])
            if stance >= 8 and current_stance > 0 and abs(current_stance-stance) <= max(4, .15*stance):
                shifts.append((t, left_sign*hip_dx/scale))
            step = 0
            for i in range(2):
                j = 1-i
                offset = abs((cur_feet[i][0]-cur_feet[j][0])-(feet[i][0]-feet[j][0]))
                rise = (cur_feet[j][1]-cur_feet[i][1])-(feet[j][1]-feet[i][1])
                if abs(cur_feet[j][0]-feet[j][0]) <= .10*scale:
                    step = max(step, min(offset, 2*rise)/scale)
            steps.append((t, step))
        threshold = max(4/scale, .10)
        before = counts['lateral_shift']
        emit_windows('lateral_shift', shifts, threshold, number, bottom, frame_order, found, counts, side='left')
        emit_windows('lateral_shift', [(t, -score) for t, score in shifts], threshold, number,
                     bottom, frame_order, found, counts, side='right')
        counts['lateral_shift'] = before + int(counts['lateral_shift'] > before)
        emit_windows('balance', steps, .23, number, bottom, frame_order, found, counts)


def emit_windows(label, samples, threshold, number, bottom, frame_order, found, counts, *, side='unspecified'):
    valid = contiguous_geometry_runs(samples, frame_order)
    if not any(len(run) >= 3 and run[-1][0]-run[0][0] >= .17 for run in valid):
        return
    counts[label] += 1
    for run in contiguous_geometry_runs([(t,v) for t,v in samples if v >= threshold], frame_order):
        first, last = run[0][0], run[-1][0]
        if len(run) < 3 or last-first < .17:
            continue
        phase = 'down' if last < bottom else 'up' if first >= bottom else 'down_and_up'
        found[label].append(event(number, phase, first, last, side,
                                  {'peak': round(max(v for _,v in run), 3), 'threshold': threshold}))


def bottom_holds(flexion, frame_order, cycles):
    """Find a visible bottom plateau after descent or before ascent, including at clip edges."""
    valid = [(t, value) for t, value in flexion.items() if value is not None and math.isfinite(value)]
    blocks = contiguous_geometry_runs(valid, frame_order)
    events = []
    evaluated = sum(1 for cycle in cycles if cycle.get('bottom_window_sec'))
    for block in blocks:
        if block[-1][0]-block[0][0] < 1.49:
            continue
        smooth = [(t, statistics.median(value for _, value in block[max(0,i-1):i+2]))
                  for i, (t, _) in enumerate(block)]
        start = 0
        for end in range(len(smooth)):
            while start < end:
                values = [v for _, v in smooth[start:end+1]]
                if max(values)-min(values) <= 10:
                    break
                start += 1
            a, b = smooth[start][0], smooth[end][0]
            if b-a < 1.49:
                continue
            depth = statistics.median(v for _, v in smooth[start:end+1])
            near = [v for t,v in smooth if a-1.2 <= t < a or b < t <= b+1.2]
            if depth < 30 or not near or depth-min(near) < 15 or max(near) > depth+10:
                continue
            match = next((i for i, c in enumerate(cycles, 1) if a <= c['bottom_sec'] <= b), None)
            candidate = event(match, 'bottom', a, b, 'unspecified',
                              {'duration_sec': round(b-a, 3), 'median_flexion_deg': round(depth, 1),
                               'touches_video_edge': a == next(iter(frame_order)) or b == next(reversed(frame_order))})
            if events and a <= events[-1]['end_sec']:
                if b-a > events[-1]['end_sec']-events[-1]['start_sec']:
                    events[-1] = candidate
            else:
                events.append(candidate)
    return events, evaluated
