"""Experimental, evidence-linked squat observations; not a medical assessment."""
import math
import statistics

ADVICE = '建议先减少下蹲深度、放慢速度，让膝盖大致跟随脚尖方向；不必用力向外顶膝。'
PHASES = ('start_sec', 'bottom_sec', 'end_sec')


def point_ok(points, ids, *, min_visibility=.7):
    return len(points) == 33 and all(
        all(math.isfinite(float(points[i].get(k, float('nan')))) for k in ('x', 'y', 'visibility'))
        and points[i]['visibility'] >= min_visibility and 0 <= points[i]['x'] <= 1 and 0 <= points[i]['y'] <= 1
        for i in ids)


def inward_score(points, hip, knee, ankle, width, height, *, scale_geometry=False):
    """Signed horizontal deviation from hip-ankle line in projected image.

    Positive = towards body's center. Normalized by projected hip separation.
    This is NOT a 3-D joint angle or a toe-direction comparison.
    """
    h, k, a = (points[i] for i in (hip, knee, ankle))
    dy = a['y'] - h['y']
    hip_width = abs(points[23]['x'] - points[24]['x'])
    if scale_geometry:
        # Keep a pixel floor for tracking noise and a body-relative floor for frontal visibility.
        if dy * height < 25 or hip_width * width < max(8, .14 * dy * height):
            return None
    elif dy < .12 or hip_width < .06:
        return None
    fraction = (k['y'] - h['y']) / dy
    if not .1 <= fraction <= .9:
        return None
    line_x = h['x'] + fraction * (a['x'] - h['x'])
    toward_center = 1 if points[hip]['x'] < (points[23]['x'] + points[24]['x']) / 2 else -1
    return toward_center * (k['x'] - line_x) / hip_width


def partial_cycle_reference(samples, flexion_by_time, cycle, cutoff):
    """Find two adjacent, stable, less-flexed frames before the observed bottom."""
    early = [(t, scores, flexion_by_time.get(t)) for t, scores in samples
             if t <= cutoff and t < cycle['bottom_sec']]
    peak = cycle['bottom_flexion_deg']
    for first, second in zip(early, early[1:]):
        t1, scores1, angle1 = first
        t2, scores2, angle2 = second
        if (angle1 is not None and angle2 is not None and 0 < t2-t1 <= .16
                and abs(angle2-angle1) <= 8 and peak-max(angle1, angle2) >= 15):
            return [(t1, scores1), (t2, scores2)]
    return []


def knee_reference_offsets(points, hip, knee, ankle):
    # Both measurements must move toward the body center. Keep the scale fixed
    # across the cycle so a projected hip-width change cannot mimic movement.
    opposite_hip = 24 if hip == 23 else 23
    direction = 1 if points[hip]['x'] < points[opposite_hip]['x'] else -1
    return tuple(direction*(points[knee]['x']-points[i]['x']) for i in (hip, ankle))


def contiguous_geometry_runs(samples, frame_order):
    runs = []
    for sample in samples:
        t = sample[0]
        if not runs or frame_order[t] != frame_order[runs[-1][-1][0]] + 1 or t-runs[-1][-1][0] > .16:
            runs.append([])
        runs[-1].append(sample)
    return runs


def hip_order(points):
    return 1 if points[24]['x'] > points[23]['x'] else -1


def wide_bilateral_ascent(events, action):
    """Flag overlapping bilateral ascent candidates for manual inspection."""
    if action != 'wide_squat':
        return list(events), []
    deferred = set()
    for i, left in enumerate(events):
        if left['side'] != 'left' or left['phase'] != 'up':
            continue
        for j, right in enumerate(events):
            if right['side'] != 'right' or right['phase'] != 'up' or left['cycle_index'] != right['cycle_index']:
                continue
            overlap = min(left['end_sec'], right['end_sec']) - max(left['start_sec'], right['start_sec'])
            if overlap >= .19:
                deferred.update((i, j))
    return ([event for i, event in enumerate(events) if i not in deferred],
            [event for i, event in enumerate(events) if i in deferred])


def local_ascent_review(events, pose):
    """Defer low-evidence ascent events found by the local geometry fallback."""
    local = [e for e in events if e['phase'] == 'up' and e['evidence'].get('local_geometry_fallback')]
    if not local:
        return list(events), []
    _, bilateral = wide_bilateral_ascent(local, 'wide_squat')
    frame_order = {f['t']: i for i, f in enumerate(pose['frames'])}
    accepted, deferred = [], []
    for event in events:
        if event not in local:
            accepted.append(event)
            continue
        if event in bilateral:
            deferred.append({**event, 'review_reason': 'bilateral_local_ascent'})
            continue
        ids = (23, 25, 27) if event['side'] == 'left' else (24, 26, 28)
        supported = []
        for frame in pose['frames']:
            if not event['start_sec'] <= frame['t'] <= event['end_sec']:
                continue
            points = frame.get('image', [])
            if not point_ok(points, (23, 24, 25, 26, 27, 28)):
                continue
            score = inward_score(points, *ids, pose['width'], pose['height'], scale_geometry=True)
            hip_span_px = abs(points[23]['x']-points[24]['x'])*pose['width']
            if score is not None and score*hip_span_px >= 3:
                supported.append((frame['t'], score))
        runs = contiguous_geometry_runs(supported, frame_order)
        if any(len(run) >= 3 and run[-1][0]-run[0][0] >= .19 for run in runs):
            accepted.append(event)
        else:
            deferred.append({**event, 'review_reason': 'weak_absolute_medial_projection'})
    return accepted, deferred


def analyze_inward(obj, view, *, diagnostics=None, allow_partial_cycles=False, scale_geometry=False,
                   dual_reference=False, stance_checks=None, trace=None, local_reference=False,
                   local_geometry=False, verify_local_reference=False,
                   score_threshold=.15, minimum_event_frames=3, minimum_event_span=.19, phase_windows=None):
    if stance_checks and not dual_reference:
        raise ValueError('stance_checks requires dual_reference')
    if stance_checks not in (None, 'absolute_ankles', 'relative_stance'):
        raise ValueError('Unknown stance check mode')
    base = {'type': 'knee_inward', 'validation': 'exploratory_unvalidated', 'status': 'unknown',
            'events': [], 'reason': None, 'advice': None,
            'limitations': ['二维投影可能受视角、站距和遮挡影响；未判断脚尖方向，不代表受伤或确定动作错误。']}
    def record(reason):
        if diagnostics is not None:
            diagnostics.append(reason)

    if view != 'front':
        record('unsupported_view')
        base['reason'] = '需要从正面拍摄；仅凭视角名称无法确保摄像机正对人物。'
        return base
    if obj['analysis']['quality']['status'] != 'usable_candidate':
        record('pose_quality')
        base['reason'] = '身体关键点质量不足。'; return base
    cycles = obj['analysis'].get('cycles', [])
    if not cycles:
        record('no_cycle')
        base['reason'] = '未识别到可分析的动作周期。'; return base
    evaluated = 0
    flexion_by_time = {row['t']:row.get('flexion') for row in obj['analysis'].get('frames', [])} if allow_partial_cycles or local_geometry else {}
    for number, cycle in enumerate(cycles, 1):
        partial = cycle.get('boundary_status') == 'incomplete'
        if cycle.get('boundary_status') != 'candidate_complete' and not (partial and allow_partial_cycles):
            record('incomplete_cycle')
            continue
        fs = [f for f in obj['frames'] if cycle['start_sec'] <= f['t'] <= cycle['end_sec']]
        samples = []
        hip_widths = []
        points_by_time = {}
        for f in fs:
            points = f.get('image', [])
            if not point_ok(points, (23, 24, 25, 26, 27, 28)):
                continue
            scores = [inward_score(points, *side, obj['width'], obj['height'], scale_geometry=scale_geometry)
                      for side in ((23,25,27),(24,26,28))]
            if all(x is not None for x in scores):
                if dual_reference:
                    scores = [knee_reference_offsets(points, *side) for side in ((23,25,27),(24,26,28))]
                    hip_widths.append(abs(points[23]['x']-points[24]['x']))
                samples.append((f['t'], scores))
                if stance_checks or verify_local_reference:
                    points_by_time[f['t']] = points
        if len(fs) < 6 or len(samples) < 6:
            record('insufficient_geometry_frames')
            continue
        local_fallback = len(samples)/len(fs) < .8
        if local_fallback and not local_geometry:
            record('insufficient_geometry_frames')
            continue
        # The first 25% of the cycle is an observed baseline, not assumed to be standing.
        cutoff = cycle['start_sec'] + .25*(cycle['end_sec']-cycle['start_sec'])
        if local_fallback:
            frame_order = {f['t']: i for i, f in enumerate(fs)}
            early_runs = contiguous_geometry_runs([(t,s) for t,s in samples if t <= cutoff], frame_order)
            reference = next((ref for run in early_runs
                              if (ref := partial_cycle_reference(run, flexion_by_time, cycle, cutoff))), [])
            if not reference:
                record('local_geometry_no_stable_reference')
                continue
        elif partial:
            reference = partial_cycle_reference(samples, flexion_by_time, cycle, cutoff)
            if not reference:
                record('partial_cycle_no_stable_reference')
                continue
            record('partial_cycle_evaluated')
        else:
            reference = [(t,s) for t,s in samples if t <= cutoff]
        if len(reference) < 2:
            record('insufficient_early_baseline')
            continue
        post_reference = [(t,s) for t,s in samples if t > cutoff]
        if local_fallback and verify_local_reference:
            reference_order = hip_order(points_by_time[reference[0][0]])
            if any(hip_order(points_by_time[t]) != reference_order for t, _ in reference):
                record('local_reference_orientation_changed')
                continue
            # Reuse the stability tolerance from the reference-pair check.
            # Comparing a more extended frame to a flexed baseline can make
            # ordinary standing up look like inward displacement.
            minimum_flexion = min(flexion_by_time[t] for t, _ in reference) - 8
            verified = []
            for t, scores in post_reference:
                if hip_order(points_by_time[t]) != reference_order:
                    record('local_comparison_orientation_changed')
                    continue
                flexion = flexion_by_time.get(t)
                if flexion is None or not math.isfinite(flexion) or flexion < minimum_flexion:
                    record('local_comparison_flexion_mismatch')
                    continue
                verified.append((t, scores))
            post_reference = verified
        if stance_checks:
            width = obj['width']
            first_points = points_by_time[reference[0][0]]
            left_to_right = 1 if first_points[24]['x'] > first_points[23]['x'] else -1
            def knees_separable(t):
                points = points_by_time[t]
                return left_to_right*(points[26]['x']-points[25]['x'])*width >= 3
            reference = [(t,s) for t,s in reference if knees_separable(t)]
            if len(reference) < 2:
                record('ambiguous_reference_knees')
                continue
            anchors = [points_by_time[t] for t,_ in reference]
            ankle_centers = [statistics.median(p[i]['x'] for p in anchors) for i in (27,28)]
            stance_width = statistics.median(left_to_right*(p[28]['x']-p[27]['x']) for p in anchors)
            hip_width = statistics.median(abs(p[24]['x']-p[23]['x']) for p in anchors)
            ankle_tolerance = max(3/width, .10*hip_width)
            stance_tolerance = max(3/width, .15*stance_width)
            stable = []
            for t, scores in post_reference:
                p = points_by_time[t]
                if not knees_separable(t):
                    record('ambiguous_knee_frame')
                    continue
                if stance_checks == 'relative_stance':
                    current_width = left_to_right*(p[28]['x']-p[27]['x'])
                    feet_moved = current_width <= 0 or abs(current_width-stance_width) > stance_tolerance
                else:
                    feet_moved = any(abs(p[i]['x']-center) > ankle_tolerance for i, center in zip((27,28),ankle_centers))
                if feet_moved:
                    record('moving_ankle_frame')
                    continue
                stable.append((t,scores))
            if len(stable) < 3 or not any(
                    0 < b[0]-a[0] <= .16 and 0 < c[0]-b[0] <= .16
                    for a,b,c in zip(stable,stable[1:],stable[2:])):
                record('stance_reference_insufficient')
                continue
            post_reference = stable
        if phase_windows is not None:
            windows = sorted(phase_windows.get(number, {}).values())
            if len(windows) == 2 and windows[0][1] >= windows[1][0]:
                windows = [(windows[0][0], windows[1][1])]
            batches = [[(t,s) for t,s in post_reference if left <= t <= right] for left,right in windows]
            order = {f['t']: i for i, f in enumerate(fs)}
            if not any(len(run) >= minimum_event_frames and run[-1][0]-run[0][0] >= minimum_event_span
                       for batch in batches for run in contiguous_geometry_runs(batch, order)):
                record('insufficient_half_depth_geometry')
                continue
            window_by_time = {t: i for i,batch in enumerate(batches) for t,_ in batch}
            post_reference = [point for batch in batches for point in batch]
        if local_fallback:
            runs = contiguous_geometry_runs(post_reference, frame_order)
            if not any(len(run) >= 3 and run[-1][0]-run[0][0] >= .19 for run in runs):
                record('local_geometry_no_continuous_window')
                continue
            record('local_geometry_evaluated')
        baseline = [s for _,s in reference]
        evaluated += 1
        record('evaluated_cycle')
        if trace is not None:
            trace[number] = {'reference_times': [t for t,_ in reference], 'scores': {}}
        if dual_reference:
            baseline_offsets = [tuple(statistics.median(s[i][j] for s in baseline) for j in (0,1)) for i in (0,1)]
            reference_width = statistics.median(hip_widths)
            baseline_score = [0.0, 0.0]
        else:
            baseline_score = [statistics.median(s[i] for s in baseline) for i in (0,1)]
        for side in (0,1):
            if dual_reference:
                candidates = [(t, min(v-b for v,b in zip(s[side],baseline_offsets[side]))/reference_width)
                              for t,s in post_reference]
                if local_reference:
                    short_window = []
                    for (t,scores), (_,standing_score) in zip(post_reference,candidates):
                        previous = [s[side] for u,s in samples if .14 <= t-u <= .40]
                        score = standing_score
                        if len(previous) >= 2:
                            recent = [statistics.median(values) for values in zip(*previous)]
                            local_score = min(v-b for v,b in zip(scores[side],recent))/reference_width
                            score = max(score,local_score)
                        short_window.append((t,score))
                    candidates = short_window
            else:
                candidates = [(t, s[side]-baseline_score[side]) for t,s in post_reference]
            if trace is not None:
                trace[number]['scores'][('left','right')[side]] = candidates
            run=[]
            def emit(sequence):
                if len(sequence) >= minimum_event_frames and sequence[-1][0]-sequence[0][0] >= minimum_event_span:
                    base['events'].append({'cycle_index': number, 'side': ('left','right')[side],
                        'start_sec':round(sequence[0][0],3), 'end_sec':round(sequence[-1][0],3),
                        'phase': 'down' if sequence[-1][0] <= cycle['bottom_sec'] else 'up' if sequence[0][0] >= cycle['bottom_sec'] else 'down_and_up',
                        'evidence': {'max_normalized_inward_change':round(max(v for _,v in sequence),3),
                                     'baseline':round(baseline_score[side],3),'frames':len(sequence),
                                     'method':('minimum inward displacement relative to ipsilateral hip and ankle, divided by fixed cycle hip width'
                                               if dual_reference else 'projected knee deviation from hip-ankle line; normalized by projected hip width'),
                                     'threshold':score_threshold, 'threshold_validation':'heuristic_unvalidated'}})
                    if stance_checks:
                        base['events'][-1]['evidence']['stance_checks'] = stance_checks
                    if local_reference:
                        base['events'][-1]['evidence']['local_reference_sec'] = [.14,.40]
                    if local_fallback:
                        base['events'][-1]['evidence']['local_geometry_fallback'] = True
                        if verify_local_reference:
                            base['events'][-1]['evidence']['local_reference_verified'] = True
            max_gap = .16 if stance_checks or local_fallback else .301
            for t, value in candidates:
                if value >= score_threshold:
                    if run and (t-run[-1][0] > max_gap or
                                (phase_windows is not None and window_by_time[t] != window_by_time[run[-1][0]]) or
                                (local_fallback and frame_order[t] != frame_order[run[-1][0]] + 1)):
                        emit(run);run=[]
                    run.append((t,value))
                else:
                    emit(run);run=[]
            emit(run)
    base['evaluated_cycles'] = evaluated
    base['total_cycles'] = len(cycles)
    if base['events']:
        base.update(status='candidate', reason='未经独立验证，仅供人工复查。', advice=ADVICE)
    else:
        base['reason'] = '未出现候选不代表动作正确；检测规则尚未完成标注验证。' if evaluated else '有效动作周期或关键点不足。'
    return base


def subjective_answers(answers=None):
    answers = answers or {}
    allowed = {'pain': {'none','mild','sharp_or_worsening','unknown'},
               'instability': {'no','yes','unknown'}, 'swelling_locking': {'no','yes','unknown'}}
    for k, v in answers.items():
        if k not in allowed or v not in allowed[k]:
            raise ValueError(f'无效的主观反馈: {k}={v}')
    urgent = answers.get('pain') == 'sharp_or_worsening'
    assess = answers.get('instability') == 'yes' or answers.get('swelling_locking') == 'yes'
    guidance = []
    if urgent: guidance.append('停止当前动作；疼痛持续、加重或影响日常活动时寻求专业评估。')
    if assess: guidance.append('停止当前动作；出现明显不稳、肿胀或关节锁住时建议接受专业评估。')
    if answers.get('pain') == 'mild': guidance.append('不要勉强继续引起不适的动作；持续不适时寻求专业评估。')
    return {'questions': [
        {'id':'pain','text':'练习时有疼痛或不适吗？','options':list(('none','mild','sharp_or_worsening','unknown'))},
        {'id':'instability','text':'是否有明显不稳或打软腿？','options':list(('no','yes','unknown'))},
        {'id':'swelling_locking','text':'是否有明显肿胀或关节锁住？','options':list(('no','yes','unknown'))}],
        'answers':answers,'guidance':guidance}


def build_guidance(obj, view='unknown', action=None, answers=None, *, knee_mode='conservative',
                   heel_review=False, all_labels=False):
    from squat_knee_final import knee_inward_observation, screen_knee_inward
    from squat_heel_raise import analyze_heel_raise

    knee = screen_knee_inward(obj, view, action, recall_fallback=knee_mode == 'high-recall')
    issues=[knee_inward_observation(knee,view)]
    if all_labels:
        from squat_label_screening import screen_other_labels

        issues.extend(screen_other_labels(obj, view, knee['cycles']))
    for typ, reason in ([] if all_labels else [('heel_raise','脚跟与地面的接触尚未验证，不能仅凭关键点确认。'),
                        ('forefoot_raise','前脚掌与地面的接触尚未验证，不能仅凭关键点确认。'),
                        ('torso_coordination','尚无经过人工标注验证的躯干与起身协调判定。'),
                        ('balance','尚无经过人工标注验证的平衡判定。')]):
        if typ == 'heel_raise' and heel_review:
            pose = {**obj, 'analysis': {**obj['analysis'], 'cycles': knee['cycles']}}
            issues.append(analyze_heel_raise(pose, view, high_recall=True))
            continue
        issues.append({'type':typ,'status':'unknown','validation':'not_validated','reason':reason,'events':[],'advice':None})
    descriptions=[]
    for i,c in enumerate(knee['cycles'],1):
        descriptions.append({'cycle_index':i,'start_sec':c['start_sec'],'bottom_sec':c['bottom_sec'],
            'end_sec':c['end_sec'],'duration_sec':c.get('duration_sec',round(c['end_sec']-c['start_sec'],3)),
            'estimated_peak_knee_flexion_deg':c.get('bottom_flexion_deg'),
            'boundary_status':c.get('boundary_status'),'interpretation':'模型估计的时间与膝屈曲角，不作为安全或正确性判断。'})
    return {'version':'squat-guidance-v2','selected_action':action,'declared_view':view,
            'view_verification':'user_declared_not_verified','cycles':descriptions,'observations':issues,
            'subjective_feedback':subjective_answers(answers),
            'warning':'研究与人工复核用途；检测规则未通过独立验证，不应作为正式纠错或医疗结论。'}


def compare_followup(current, previous):
    if not previous:
        return {'status':'unknown','reason':'未提供上一轮结果'}
    if current.get('selected_action') is None or current.get('declared_view') in (None,'unknown'):
        return {'status':'unknown','reason':'需要明确两次动作与拍摄角度'}
    if (previous.get('selected_action'),previous.get('declared_view')) != (current.get('selected_action'),current.get('declared_view')):
        return {'status':'unknown','reason':'两次动作或拍摄角度不一致，暂不比较'}
    a=previous.get('cycles',[]);b=current.get('cycles',[])
    if not a or not b:
        return {'status':'unknown','reason':'缺少可比较的动作周期'}
    return {'status':'descriptive_only','note':'动作周期和人物姿态为模型估计，数值变化不等于动作改善。',
            'previous_cycles':len(a),'current_cycles':len(b),
            'previous_median_duration_sec':round(statistics.median(x['duration_sec'] for x in a if x.get('duration_sec') is not None),2) if any(x.get('duration_sec') is not None for x in a) else None,
            'current_median_duration_sec':round(statistics.median(x['duration_sec'] for x in b if x.get('duration_sec') is not None),2) if any(x.get('duration_sec') is not None for x in b) else None}
