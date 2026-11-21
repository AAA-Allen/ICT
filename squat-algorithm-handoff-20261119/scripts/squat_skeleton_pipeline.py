"""Offline experimental squat skeleton extraction and kinematic analysis."""
import argparse
import csv
import gzip
import hashlib
import json
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import mediapipe as mp
import numpy as np
from scipy.signal import find_peaks

from squat_angle_v3 import sha256

VERSION = "squat-skeleton-offline-v1"
SIDES = ((23, 25, 27), (24, 26, 28))


def sample_video(path,target_fps):
    cap=cv2.VideoCapture(str(path))
    if not cap.isOpened():raise OSError(f'Cannot open video: {path}')
    fps=cap.get(cv2.CAP_PROP_FPS)
    if not math.isfinite(fps) or fps<=0 or fps>240:
        cap.release();raise ValueError('Video frame rate unavailable')
    index=0;next_time=0.
    try:
        while True:
            ok,bgr=cap.read()
            if not ok:break
            seconds=index/fps;index+=1
            if seconds+1e-8<next_time:continue
            yield round(seconds,3),bgr
            next_time+=1/target_fps
            if next_time<seconds:next_time=seconds+1/target_fps
    finally:cap.release()


def angle(a, b, c):
    u, v = np.asarray(a)-b, np.asarray(c)-b
    den = np.linalg.norm(u)*np.linalg.norm(v)
    if den < 1e-8: return None
    return float(180-np.degrees(np.arccos(np.clip(np.dot(u,v)/den, -1, 1))))


def valid(points, indices):
    return all(i < len(points) and points[i]["visibility"] >= .55 and 0 <= points[i]["x"] <= 1 and 0 <= points[i]["y"] <= 1 for i in indices)


def frame_features(frame, width, height):
    p, w = frame.get("image", []), frame.get("world", [])
    if len(p)!=33 or len(w)!=33: return {"t":frame["t"],"flexion":None,"hip_y":None}
    flex=[]
    for side in SIDES:
        if valid(p,side):
            v=angle(*[np.array([w[i][c] for c in ("x","y","z")]) for i in side])
            if v is not None: flex.append(v)
    hip_y=(p[23]["y"]+p[24]["y"])/2 if valid(p,(23,24)) else None
    result={"t":frame["t"],"flexion":float(np.median(flex)) if flex else None,"left_flexion":None,"right_flexion":None,"hip_y":hip_y}
    for name, side in zip(("left_flexion","right_flexion"),SIDES):
        if valid(p,side): result[name]=angle(*[np.array([w[i][c] for c in ("x","y","z")]) for i in side])
    if valid(p,(11,12,27,28)):
        shoulder=abs(p[11]["x"]-p[12]["x"]);feet=abs(p[27]["x"]-p[28]["x"])
        result["stance_ratio"] = feet/shoulder if shoulder>.04 else None
    if valid(p,(11,12,23,24)):
        dx=((p[11]["x"]+p[12]["x"])-(p[23]["x"]+p[24]["x"]))*width/2
        dy=((p[23]["y"]+p[24]["y"])-(p[11]["y"]+p[12]["y"]))*height/2
        result["torso_image_tilt_deg"]=float(np.degrees(np.arctan2(abs(dx),dy))) if dy>0 else None
    return result


def analyze(frames,width,height):
    f=[frame_features(x,width,height) for x in frames]; n=len(f)
    valid_idx=[i for i,x in enumerate(f) if x['flexion'] is not None]
    quality={"sampled_frames":n,"valid_knee_frames":len(valid_idx),"valid_knee_fraction":len(valid_idx)/n if n else 0,
             "status":"usable_candidate" if n and len(valid_idx)>=6 and len(valid_idx)/n>=.6 else "insufficient_pose"}
    answer={"quality":quality,"features":{},"cycles":[],"frames":f}
    if quality['status']!='usable_candidate':return answer
    # Interpolate gaps only up to 0.3 seconds; larger gaps remain unknown.
    t=np.array([x['t'] for x in f]); a=np.array([x['flexion'] if x['flexion'] is not None else np.nan for x in f],dtype=float)
    for start in range(n):
        if np.isfinite(a[start]): continue
        left=start-1;right=start+1
        while right<n and not np.isfinite(a[right]):right+=1
        if left>=0 and right<n and np.isfinite(a[left]) and t[right]-t[left]<=.301:
            a[start]=a[left]+(a[right]-a[left])*(t[start]-t[left])/(t[right]-t[left])
    smooth=np.array([np.nanmedian(a[max(0,i-1):min(n,i+2)]) if np.isfinite(a[i]) else np.nan for i in range(n)])
    segment=np.where(np.isfinite(smooth),smooth,-1000)
    peaks,_=find_peaks(segment,prominence=15,distance=5)
    for peak in peaks:
        left=peak-1
        while left>=0 and np.isfinite(smooth[left]) and smooth[left]<=smooth[left+1]+3:left-=1
        left+=1
        right=peak+1
        while right<n and np.isfinite(smooth[right]) and smooth[right]<=smooth[right-1]+3:right+=1
        right-=1
        if left>=peak or right<=peak or t[peak]-t[left]<.2 or t[right]-t[peak]<.2:continue
        low=peak;high=peak
        while low>left and np.isfinite(smooth[low-1]) and smooth[low-1]>=smooth[peak]-5:low-=1
        while high<right and np.isfinite(smooth[high+1]) and smooth[high+1]>=smooth[peak]-5:high+=1
        answer['cycles'].append({"start_sec":float(t[left]),"bottom_sec":float(t[peak]),"end_sec":float(t[right]),
            "down": [float(t[left]),float(t[peak])],"up":[float(t[peak]),float(t[right])],
            "bottom_window_sec":[float(t[low]),float(t[high])],"bottom_hold_candidate_sec":round(float(t[high]-t[low]),2),
            "standing_before_candidate":bool(smooth[left]<=20),"standing_after_candidate":bool(smooth[right]<=20),
            "bottom_flexion_deg":round(float(smooth[peak]),1),"duration_sec":round(float(t[right]-t[left]),2),
            "boundary_status":"incomplete" if left==0 or right==n-1 or (left>0 and not np.isfinite(smooth[left-1])) or (right<n-1 and not np.isfinite(smooth[right+1])) else "candidate_complete"})
    # Adjacent repetitions may initially share a shallow valley; split them at
    # the least-flexed observed frame so a frame cannot belong to both cycles.
    for previous,current in zip(answer['cycles'],answer['cycles'][1:]):
        if previous['end_sec'] <= current['start_sec']:continue
        eligible=[i for i in range(n) if previous['bottom_sec'] < t[i] < current['bottom_sec'] and np.isfinite(smooth[i])]
        if not eligible:
            previous['boundary_status']=current['boundary_status']='incomplete'
            continue
        split=min(eligible,key=lambda i:smooth[i]);at=float(t[split])
        previous['end_sec']=at;previous['up'][1]=at
        previous['duration_sec']=round(at-previous['start_sec'],2)
        previous['standing_after_candidate']=bool(smooth[split]<=20)
        current['start_sec']=at;current['down'][0]=at
        current['duration_sec']=round(current['end_sec']-at,2)
        current['standing_before_candidate']=bool(smooth[split]<=20)
    def med(key):
        vals=[x[key] for x in f if x.get(key) is not None and math.isfinite(x[key])]
        return round(float(np.median(vals)),3) if len(vals)>=3 else None
    bilateral=[abs(x['left_flexion']-x['right_flexion']) for x in f if x.get('left_flexion') is not None and x.get('right_flexion') is not None]
    answer['features']={"peak_flexion_deg":round(float(np.nanmax(smooth)),2),"median_stance_ratio_2d":med('stance_ratio'),
        "median_torso_image_tilt_deg":med('torso_image_tilt_deg'),"median_bilateral_flexion_diff_deg":round(float(np.median(bilateral)),2) if len(bilateral)>=3 else None,
        "cycle_count":len(answer['cycles']),"median_cycle_duration_sec":round(float(np.median([c['duration_sec'] for c in answer['cycles']])),2) if answer['cycles'] else None,
        "median_bottom_hold_candidate_sec":round(float(np.median([c['bottom_hold_candidate_sec'] for c in answer['cycles']])),2) if answer['cycles'] else None}
    return answer


def infer(row,dataset,model,config,*,sample_fps=10):
    path=dataset / row['video_path']; cap=cv2.VideoCapture(str(path));width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH));height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT));cap.release()
    options=mp.tasks.vision.PoseLandmarkerOptions(base_options=mp.tasks.BaseOptions(model_asset_path=str(model)),running_mode=mp.tasks.vision.RunningMode.VIDEO,num_poses=1,min_pose_detection_confidence=.5,min_pose_presence_confidence=.5,min_tracking_confidence=.5)
    frames=[]
    def pack(pts):return [{"x":round(p.x,6),"y":round(p.y,6),"z":round(p.z,6),"visibility":round(p.visibility,4)} for p in pts]
    with mp.tasks.vision.PoseLandmarker.create_from_options(options) as detector:
        for t,bgr in sample_video(path,sample_fps):
            result=detector.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,data=cv2.cvtColor(bgr,cv2.COLOR_BGR2RGB)),int(t*1000)+1)
            frame={"t":t,"pose_detected":bool(result.pose_landmarks and result.pose_world_landmarks)}
            if frame['pose_detected']:
                frame['image']=pack(result.pose_landmarks[0]);frame['world']=pack(result.pose_world_landmarks[0])
                hip=np.mean([[frame['world'][i][k] for k in ('x','y','z')] for i in (23,24)],axis=0)
                shoulder=np.array([[frame['world'][i][k] for k in ('x','y','z')] for i in (11,12)])
                scale=float(np.linalg.norm(shoulder[0]-shoulder[1]))
                frame['normalized_world']=[{'x':round((p['x']-hip[0])/scale,5),'y':round((p['y']-hip[1])/scale,5),'z':round((p['z']-hip[2])/scale,5)} for p in frame['world']] if scale>.05 and valid(frame['image'],(11,12,23,24)) else None
            frames.append(frame)
    if not frames or width<=0 or height<=0:raise ValueError('empty video or invalid dimensions')
    result=analyze(frames,width,height)
    return {"video_id":row['video_id'],"video_path":row['video_path'],"source_sha256":sha256(path),"model_sha256":config['model_sha256'],"version":VERSION,"analysis_revision":3,"sample_rate_requested_fps":sample_fps,"width":width,"height":height,"frames":frames,"analysis":result}


def write_gzip(path,obj):
    temp=path.with_suffix('.tmp')
    with gzip.open(temp,'wt',encoding='utf8') as f:json.dump(obj,f,ensure_ascii=False,separators=(',',':'))
    os.replace(temp,path)


def main():
    p=argparse.ArgumentParser();p.add_argument('--dataset',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--screen',type=Path,required=True);p.add_argument('--model',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--workers',type=int,default=6);p.add_argument('--resume',action='store_true');a=p.parse_args()
    def read(path):
        with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))
    rows=read(a.baseline);screen=read(a.screen);lookup={r['video_id']:r for r in screen}
    if len(rows)!=621 or len(lookup)!=621 or {r['video_id'] for r in rows}!=set(lookup):raise ValueError('Expected 621 matching unique IDs')
    config={'version':VERSION,'model_sha256':sha256(a.model),'baseline_sha256':sha256(a.baseline),'screen_sha256':sha256(a.screen),'dataset':str(a.dataset.resolve())}
    if a.output.exists():
        if not a.resume or json.loads((a.output/'config.json').read_text(encoding='utf8'))!=config:raise ValueError('Output exists or configuration changed')
    else:a.output.mkdir(parents=True);(a.output/'config.json').write_text(json.dumps(config,indent=2),encoding='utf8')
    out=a.output/'clips';out.mkdir(exist_ok=True)
    selected=[r for r in rows if lookup[r['video_id']]['training_screen']!='exclude']
    with (a.output/'input_manifest.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['video_id','video_path','training_screen','selected']);w.writeheader()
        for r in rows:w.writerow({'video_id':r['video_id'],'video_path':r['video_path'],'training_screen':lookup[r['video_id']]['training_screen'],'selected':lookup[r['video_id']]['training_screen']!='exclude'})
    todo=[r for r in selected if not (out/(r['video_id']+'.json.gz')).exists()]
    def run(r):
        try:result=infer(r,a.dataset,a.model,config)
        except Exception as e:result={'video_id':r['video_id'],'error':f'{type(e).__name__}: {e}'}
        write_gzip(out/(r['video_id']+'.json.gz'),result);return result
    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        for n,f in enumerate(as_completed([pool.submit(run,r) for r in todo]),1):
            f.result()
            if n%25==0:print(f'{n}/{len(todo)} processed',flush=True)
    results=[]
    for row in selected:
        clip_path=out/(row['video_id']+'.json.gz')
        with gzip.open(clip_path,'rt',encoding='utf8') as f:obj=json.load(f)
        if obj['video_id']!=row['video_id']:raise ValueError('Checkpoint mismatch')
        if not obj.get('error') and obj.get('analysis_revision')!=3:
            obj['analysis']=analyze(obj['frames'],obj['width'],obj['height']);obj['analysis_revision']=3
            write_gzip(clip_path,obj)
        analysis=obj.get('analysis',{});features=analysis.get('features',{})
        results.append({'video_id':row['video_id'],'quality':analysis.get('quality',{}).get('status','error'),'error':obj.get('error',''),**features})
    keys=['video_id','quality','error','peak_flexion_deg','median_stance_ratio_2d','median_torso_image_tilt_deg','median_bilateral_flexion_diff_deg','cycle_count','median_cycle_duration_sec','median_bottom_hold_candidate_sec']
    with (a.output/'features.csv').open('w',encoding='utf-8-sig',newline='') as f:w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore');w.writeheader();w.writerows(results)
    print(json.dumps({'selected':len(selected),'excluded':621-len(selected),'errors':sum(bool(r['error']) for r in results),'candidate_usable':sum(r['quality']=='usable_candidate' for r in results)}),flush=True)


if __name__=='__main__':main()
