"""Screen for apparent heel rise relative to the same foot's forefoot."""
import statistics

from squat_guidance import contiguous_geometry_runs, point_ok

HEEL_RULE = 'heel-rise-image-v1'
FEET = {'left': (23, 27, 29, 31), 'right': (24, 28, 30, 32)}


def analyze_heel_raise(pose, view, *, high_recall=False):
    return analyze_foot_raise(pose, view, high_recall=high_recall)


def analyze_foot_raise(pose, view, *, high_recall=False, forefoot=False):
    cycles = pose['analysis'].get('cycles', [])
    version = 'forefoot-rise-image-v1' if forefoot else HEEL_RULE
    version += '-high-recall' if high_recall else ''
    version += '-short-clip-v2'
    label = 'forefoot_raise' if forefoot else 'heel_raise'
    rising, reference = ('前脚掌', '脚跟') if forefoot else ('脚跟', '前脚掌')
    threshold, min_frames, min_span = (.018, 2, .09) if high_recall else (.025, 3, .17)
    result = {'type': label, 'rule_version': version, 'status': 'unknown',
              'reason_code': 'insufficient_foot_evidence', 'reason': '脚跟、前脚掌或站姿参照不足，无法判定。',
              'events': [], 'review_events': [], 'evaluated_cycles': 0, 'total_cycles': len(cycles), 'advice': None,
              'validation': 'development_review_only'}
    if view not in ('front', 'side', 'oblique', 'front_or_oblique'):
        return {**result, 'reason_code': 'unsupported_view', 'reason': '请注明拍摄视角并让脚部完整入画。'}
    if pose['analysis']['quality']['status'] != 'usable_candidate':
        return result

    windows = list(enumerate(cycles, 1))
    if not windows and len(pose['frames']) >= 3:
        start, end = pose['frames'][0]['t'], pose['frames'][-1]['t']
        windows = [(None, {'start_sec': start, 'end_sec': end, 'bottom_sec': None})]

    flexions = {f['t']: f.get('flexion') for f in pose['analysis'].get('frames', [])}
    frame_order = {f['t']: i for i, f in enumerate(pose['frames'])}
    height = pose['height']
    evaluated = set()
    for side, (hip, ankle, heel, toe) in FEET.items():
        if forefoot:
            heel, toe = toe, heel
        samples = {}
        visibility = {}
        for frame in pose['frames']:
            t, points = frame['t'], frame.get('image', [])
            if not point_ok(points, (hip, ankle, heel, toe), min_visibility=.55 if high_recall else .7):
                continue
            scale = (points[ankle]['y'] - points[hip]['y']) * height
            if scale >= 20:
                samples[t] = (points[heel]['y']*height, points[toe]['y']*height, scale)
                visibility[t] = min(points[i]['visibility'] for i in (hip, ankle, heel, toe))
        for number, cycle in windows:
            start, bottom, end = (cycle[k] for k in ('start_sec', 'bottom_sec', 'end_sec'))
            peak = cycle.get('bottom_flexion_deg')
            near_edge = [(t, value) for t, value in samples.items()
                         if (start <= t <= start+.3 if number is None else min(abs(t-start), abs(t-end)) <= .3)
                         and flexions.get(t) is not None
                         and flexions[t] <= (40 if number is None else min(40, peak-12))]
            pairs = []
            for (a, first), (b, second) in zip(near_edge, near_edge[1:]):
                if frame_order[b] != frame_order[a]+1 or not 0 < b-a <= .16 or (bottom is not None and a <= bottom <= b):
                    continue
                if abs(flexions[b]-flexions[a]) <= 8 and abs(first[1]-second[1]) <= max(3, .03*first[2]):
                    pairs.append((a, b))
            if not pairs:
                continue
            ref = min(pairs, key=lambda pair: max(flexions[t] for t in pair))
            heel_y, toe_y, scale = map(statistics.median, zip(*(samples[t] for t in ref)))
            trigger_px = max(2, threshold * scale)
            usable = []
            for t, (current_heel, current_toe, _) in samples.items():
                if start <= t <= end and abs(current_toe-toe_y) <= max(4, .05*scale):
                    heel_up = heel_y-current_heel
                    relative = (current_toe-current_heel)-(toe_y-heel_y)
                    usable.append((t, min(relative, 2*heel_up)))
            if not any(len(run) >= min_frames and run[-1][0]-run[0][0] >= min_span
                       for run in contiguous_geometry_runs(usable, frame_order)):
                continue
            evaluated.add(number)
            above = [(t, score) for t, score in usable if score >= trigger_px]
            for run in contiguous_geometry_runs(above, frame_order):
                first, last = run[0][0], run[-1][0]
                if len(run) < min_frames or last-first < min_span:
                    continue
                phase = 'unknown' if bottom is None else 'down' if last < bottom else 'up' if first > bottom else 'down_and_up'
                min_visibility = min(visibility[t] for t in (*ref, *(t for t,_ in run)))
                queue = 'events' if min_visibility >= .7 else 'review_events'
                if queue == 'review_events' and (len(run) < 3 or last-first < .17):
                    continue
                result[queue].append({'cycle_index': number, 'side': side, 'phase': phase,
                                         'start_sec': first, 'end_sec': last,
                                         'evidence': {'method': 'relative vertical motion of the two foot landmarks',
                                                      'reference_times_sec': list(ref),
                                                      'scope': 'short_clip_without_squat_cycle' if number is None else 'squat_cycle',
                                                      'min_visibility': min_visibility,
                                                      'peak_support_px': round(max(v for _, v in run), 2),
                                                      'trigger_px': round(trigger_px, 2)}})
    result['evaluated_cycles'] = len(evaluated - {None})
    result['evaluated_short_clip'] = None in evaluated
    result['events'].sort(key=lambda e: (e['start_sec'], e['side']))
    if result['events']:
        result.update(status='candidate', reason_code='relative_forefoot_rise' if forefoot else 'relative_heel_rise',
                      reason=f'{rising}相对同侧{reference}上移，请按时段复核。')
    elif result['review_events']:
        result.update(status='review_required', reason_code='low_visibility_foot_motion',
                      reason=f'观察到{rising}相对上移，但脚部关键点可见度偏低，请结合原视频检查。')
    elif evaluated:
        result.update(reason_code='no_candidate', reason=f'可分析的片段暂未触发{rising}抬起候选。')
    return result
