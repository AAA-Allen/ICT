"""Fuse rule candidates and descriptive kinematics; both share estimated pose."""
import math
import statistics


def finite(value):
    return isinstance(value, (float, int)) and math.isfinite(value)


def analyze_dual(biomechanics, guidance):
    metrics = biomechanics.get('metrics', {})
    findings = []
    def add(kind, title, reason, advice, paths):
        findings.append(dict(id=kind, problem=title, reason=reason, advice=advice,
                             label='运动学观察', evidence_paths=paths, status='descriptive'))
    def available(name):
        m = metrics.get(name, {})
        return m if m.get('status') == 'estimated' and m.get('coverage', 0) >= .5 else {}
    depths = []
    for side in ('left', 'right'):
        name = side+'_knee_flexion_deg'
        m = available(name)
        if finite(m.get('p95')):
            depths.append(('左' if side == 'left' else '右')+f'膝较深位置参考约 {m["p95"]:.1f}°')
    if depths:
        add('motion-depth', '本次下蹲幅度参考' if biomechanics.get('cycles') else '膝部活动范围参考（未确认深蹲周期）', '；'.join(depths)+'。采用有效帧的95%分位数减少单帧尖峰影响，站直约为0°；这是视频整体参考，不是最低点的精确测量或安全界线。',
            '在脚掌稳定、躯干可控且舒适的范围练习；若本次目标是增加幅度，可逐步尝试更深，出现失控时回到能控制的范围。',
            ['biomechanics.metrics.left_knee_flexion_deg', 'biomechanics.metrics.right_knee_flexion_deg'])
    complete = [c for c in biomechanics.get('cycles', [])
                if c.get('boundary_status') == 'candidate_complete'
                and finite(c.get('descent_to_bottom_sec')) and finite(c.get('active_ascent_sec'))
                and c['descent_to_bottom_sec'] > 0 and c['active_ascent_sec'] > 0
                and any(c.get('metrics', {}).get(s+'_knee_flexion_deg', {}).get('status') == 'estimated'
                        for s in ('left', 'right'))]
    if complete:
        down=statistics.median(c['descent_to_bottom_sec'] for c in complete)
        up=statistics.median(c['active_ascent_sec'] for c in complete)
        add('motion-tempo', '本次下蹲与起身节奏参考',
            f'{len(complete)}次完整候选动作：下蹲至最低点用时中位数约{down:.2f}秒，扣除已识别底部停留后的起身用时约{up:.2f}秒。阶段边界为模型估计；时间长短本身不代表错误。',
            '下一组先保持平稳、可重复的节奏；最低点继续保持控制，起身时让髋部与胸口协调上升，不借突然弹震完成。', ['biomechanics.cycles'])
    diff=available('bilateral_knee_difference_deg')
    if finite(diff.get('median')) and finite(diff.get('p95')):
        add('motion-symmetry', '左右腿协同参考',
            f'同帧双膝屈曲角差中位数约{diff["median"]:.1f}°，95%分位数约{diff["p95"]:.1f}°。该差值受视角、遮挡与骨架估计影响，不能解释为力量差、膝内移或肌肉代偿。',
            '观察双腿是否协调下蹲、平稳起身，双脚保持稳定支撑；若反复明显偏向一侧，先减小幅度，并用同一正面机位复查。', ['biomechanics.metrics.bilateral_knee_difference_deg'])
    tilt=available('trunk_image_tilt_deg')
    if biomechanics.get('declared_view') == 'side' and finite(tilt.get('p05')) and finite(tilt.get('p95')):
        add('motion-trunk', '侧面躯干倾斜参考',
            f'躯干相对图像竖直方向的倾斜参考范围为{tilt["p05"]:.1f}–{tilt["p95"]:.1f}°（5%–95%分位区间）。自然前倾可以存在；该量不能代表脊柱曲度或关节受力。',
            '保持腰腹适度张力，在能控制的深度内协调起身，不强求上半身始终竖直；机位应固定且避免倾斜。', ['biomechanics.metrics.trunk_image_tilt_deg'])
    observations=guidance.get('observations', [])
    candidates=[o for o in observations if o.get('status') in ('candidate','review_required')
                and (o.get('events') or o.get('review_events'))]
    return {'version':'squat-dual-analysis-v1', 'shared_input':'same_model_estimated_pose_not_independent_measurements',
            'skeleton':{'candidate_count':len(candidates), 'unknown_count':sum(o.get('status')=='unknown' and o.get('reason_code')!='no_candidate' for o in observations)},
            'kinematics':{'finding_count':len(findings), 'findings':findings},
            'fusion':{'status':'partial_evidence' if findings or candidates else 'insufficient_evidence',
                      'policy':'Retain rule candidates and kinematic descriptions; no voting across different quantities. Unknown stays unknown.',
                      'validation':'development_only_not_accuracy_evaluated'}}
