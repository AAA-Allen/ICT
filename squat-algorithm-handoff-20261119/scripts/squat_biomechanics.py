"""Descriptive squat kinematics from visible, model-estimated landmarks."""
import math
import statistics

import numpy as np

from squat_guidance import point_ok
from squat_skeleton_pipeline import angle

METRICS = {
    'left_knee_flexion_deg': ('deg', 'model_3d', '左膝屈曲角，伸直约为零'),
    'right_knee_flexion_deg': ('deg', 'model_3d', '右膝屈曲角，伸直约为零'),
    'left_trunk_thigh_angle_deg': ('deg', 'model_3d', '左侧肩髋膝三点构成的躯干与大腿夹角变化'),
    'right_trunk_thigh_angle_deg': ('deg', 'model_3d', '右侧肩髋膝三点构成的躯干与大腿夹角变化'),
    'bilateral_knee_difference_deg': ('deg', 'model_3d', '同一时刻左右膝屈曲角的绝对差'),
    'trunk_image_tilt_deg': ('deg', 'image_2d', '髋中心至肩中心连线相对图像竖直方向的倾斜'),
    'left_shank_image_tilt_deg': ('deg', 'image_2d', '左侧踝膝连线相对图像竖直方向的倾斜'),
    'right_shank_image_tilt_deg': ('deg', 'image_2d', '右侧踝膝连线相对图像竖直方向的倾斜'),
    'hip_descent_leg_ratio': ('reference_leg_lengths', 'image_2d', '髋相对踝的向下位移除以参考投影腿长'),
}


def world_angle(frame, ids):
    world = frame.get('world', [])
    if len(world) != 33 or not point_ok(frame.get('image', []), ids):
        return None
    coordinates = [[world[i].get(axis, math.nan) for axis in ('x','y','z')] for i in ids]
    if not all(math.isfinite(value) for point in coordinates for value in point):
        return None
    return angle(*[np.array(point) for point in coordinates])


def image_tilt(lower, upper, width, height):
    dx = (upper[0]-lower[0])*width
    dy = (lower[1]-upper[1])*height
    return math.degrees(math.atan2(abs(dx),dy)) if dy > 1 else None


def frame_kinematics(frame, width, height, view):
    row = {'t': frame['t'], **dict.fromkeys(METRICS)}
    points = frame.get('image', [])
    for side, shoulder, hip, knee, ankle in (('left',11,23,25,27), ('right',12,24,26,28)):
        row[side+'_knee_flexion_deg'] = world_angle(frame,(hip,knee,ankle))
        row[side+'_trunk_thigh_angle_deg'] = world_angle(frame,(shoulder,hip,knee))
        if view == 'side' and point_ok(points,(knee,ankle)):
            row[side+'_shank_image_tilt_deg'] = image_tilt(
                (points[ankle]['x'],points[ankle]['y']), (points[knee]['x'],points[knee]['y']),width,height)
    left, right = row['left_knee_flexion_deg'],row['right_knee_flexion_deg']
    if left is not None and right is not None:
        row['bilateral_knee_difference_deg'] = abs(left-right)
    if view == 'side' and point_ok(points,(11,12,23,24)):
        centers = [tuple((points[a][axis]+points[b][axis])/2 for axis in ('x','y'))
                   for a,b in ((23,24),(11,12))]
        row['trunk_image_tilt_deg'] = image_tilt(*centers,width,height)
    return row


def hip_projection(frame, width, height):
    points = frame.get('image', [])
    if not point_ok(points,(23,24,25,26,27,28)):
        return None
    def distance(a,b):
        return math.hypot((points[a]['x']-points[b]['x'])*width,(points[a]['y']-points[b]['y'])*height)
    scale = statistics.mean(distance(h,k)+distance(k,a) for h,k,a in ((23,25,27),(24,26,28)))
    if scale < 30:
        return None
    offset = (points[23]['y']+points[24]['y']-points[27]['y']-points[28]['y'])*height/2
    return offset,scale


def metric_summary(rows, name, view):
    unit, method, description = METRICS[name]
    valid = [(row['t'],row[name]) for row in rows if row[name] is not None]
    count = len(valid)
    ready = count >= 3 and count/len(rows) >= .5
    reason = 'available' if ready else 'insufficient_visible_samples'
    if 'image_tilt' in name and view != 'side':
        reason = 'requires_declared_side_view'
    if name == 'hip_descent_leg_ratio' and not valid:
        reason = 'no_continuous_standing_reference_or_visible_leg'
    values = [value for _,value in valid]
    return {'status': 'estimated' if ready else 'unknown', 'reason_code':reason,
            'unit':unit,'method':method,'description':description,
            'valid_frames':count,'total_frames':len(rows),'coverage':round(count/len(rows),4) if rows else 0,
            'median':round(statistics.median(values),3) if ready else None,
            'p05':round(float(np.percentile(values,5)),3) if ready else None,
            'p95':round(float(np.percentile(values,95)),3) if ready else None}


def analyze_biomechanics(pose, view='unknown', cycles=None):
    frames = pose['frames']
    width,height = pose['width'],pose['height']
    rows = [frame_kinematics(frame,width,height,view) for frame in frames]
    projections = [hip_projection(frame,width,height) for frame in frames]
    reference = None
    for index in range(1,len(rows)):
        pair = rows[index-1:index+1]
        angles = [row[key] for row in pair for key in ('left_knee_flexion_deg','right_knee_flexion_deg')]
        if (all(value is not None and value <= 25 for value in angles)
                and 0 < pair[1]['t']-pair[0]['t'] <= .16
                and all(projections[i] is not None for i in (index-1,index))
                and max(angles)-min(angles) <= 8):
            reference = {'times_sec':[row['t'] for row in pair],
                         'offset_px':statistics.mean(projections[i][0] for i in (index-1,index)),
                         'scale_px':statistics.mean(projections[i][1] for i in (index-1,index))}
            break
    if reference:
        for row, projection in zip(rows,projections):
            if projection is not None:
                row['hip_descent_leg_ratio'] = (projection[0]-reference['offset_px'])/reference['scale_px']
    for row in rows:
        for name in METRICS:
            if row[name] is not None:
                row[name] = round(row[name],4)
    metrics = {name:metric_summary(rows,name,view) for name in METRICS}
    repetitions = []
    for index,cycle in enumerate(pose['analysis']['cycles'] if cycles is None else cycles,1):
        start,bottom,end = (cycle[key] for key in ('start_sec','bottom_sec','end_sec'))
        if not start <= bottom <= end:
            continue
        segment = [row for row in rows if start <= row['t'] <= end]
        hold = cycle.get('bottom_window_sec') if cycle.get('bottom_hold_candidate_sec',0) >= .3 else None
        ascent_start = max(bottom,hold[1]) if hold else bottom
        repetitions.append({'cycle_index':index,'start_sec':start,'bottom_sec':bottom,'end_sec':end,
                            'boundary_status':cycle.get('boundary_status','unknown'),
                            'descent_to_bottom_sec':round(bottom-start,3),
                            'bottom_to_end_sec':round(end-bottom,3),
                            'active_ascent_sec':round(end-ascent_start,3) if ascent_start <= end else None,
                            'metrics':{name:metric_summary(segment,name,view) for name in METRICS}})
    return {'version':'squat-biomechanics-kinematics-v1',
            'status':'estimated' if any(x['status']=='estimated' for x in metrics.values()) else 'unknown',
            'validation':'descriptive_development_only','declared_view':view,
            'metrics':metrics,'cycles':repetitions,'frames':rows,'standing_reference':reference,
            'interpretation': ['角度来自模型估计骨架，躯干与大腿夹角受肩部姿势影响。',
                               '二维倾斜需固定侧面机位；相机倾斜与透视会影响数值。',
                               '髋部位移为相对踝部和投影腿长的比值。',
                               '左右差异、幅度和节奏的变化需结合动作目标、视角及关键点质量解释。'],
            'unavailable': ['joint_forces','joint_moments','muscle_activation','injury_probability']}
