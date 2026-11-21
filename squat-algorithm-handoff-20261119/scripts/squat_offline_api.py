"""CLI producing a developer-facing JSON analysis; no web interface."""
import argparse
import json
from pathlib import Path

import numpy as np
from squat_angle_v3 import sha256
from squat_skeleton_pipeline import infer
from squat_motion_experiments import FEATURES, compare_cycles, load_clip, load_features
from squat_guidance import build_guidance, compare_followup
from squat_biomechanics import analyze_biomechanics


def search(obj,run,limit=3):
    rows,x,transforms=load_features(run/'features.csv')
    if x is None:return []
    f=obj['analysis']['features'];a=np.array([[f.get(k) if f.get(k) is not None else np.nan for k in FEATURES]],dtype=float)
    if sum(np.isfinite(a[0]))<3:return []
    imputer,scaler=transforms;q=scaler.transform(imputer.transform(a))[0]
    distances=np.linalg.norm(x-q,axis=1);result=[]
    for i in np.argsort(distances):
        r=rows[i]
        if r['video_id']==obj['video_id']:continue
        common=[{'feature':k,'query':f[k],'candidate':float(r[k])} for k in FEATURES if f.get(k) is not None and r.get(k)]
        if len(common)<3:continue
        result.append({'video_id':r['video_id'],'distance':round(float(distances[i]),4),'shared_features':common,
                       'note':'exploratory similarity, not a correctness or person-identity match'})
        if len(result)>=limit:break
    return result


def compare_registry(obj,registry,run,model,camera_view='unknown',action=None,camera_elevation='unknown',exclude_person_id=None):
    if registry is None:return {'status':'unknown','reason':'no_confirmed_reference_registry'}
    if not action:return {'status':'unknown','reason':'query_action_unspecified'}
    if camera_view=='unknown':return {'status':'unknown','reason':'query_camera_view_unspecified'}
    items=json.loads(registry.read_text(encoding='utf8'));comparisons=[]
    for item in items:
        if item.get('confirmed') is not True or item.get('usage_permitted') is not True or not all(item.get(k) for k in ('person_id','target','camera_view','video_path')):continue
        if item['target']!=action or item['camera_view']!=camera_view or item['person_id']==exclude_person_id:continue
        if camera_elevation!='unknown' and item.get('camera_elevation')!=camera_elevation:continue
        path=Path(item['video_path'])
        if not path.is_file() or not item.get('sha256') or sha256(path)!=item['sha256']:continue
        if obj.get('source_sha256')==item['sha256']:continue
        cache=registry.parent/'reference_cache'/(path.name+'.json.gz')
        if not cache.exists() and run is not None:cache=run/'clips'/(path.name+'.json.gz')
        if cache.exists():
            import gzip
            with gzip.open(cache,'rt',encoding='utf8') as f:ref=json.load(f)
            if ref.get('source_sha256')!=item['sha256'] or ref.get('model_sha256')!=sha256(model):continue
        else:ref=infer({'video_id':path.name,'video_path':path.name},path.parent,model,{'model_sha256':sha256(model)})
        comparisons.append({'video_id':path.name,'person_id':item['person_id'],'camera_view':item['camera_view'],'camera_elevation':item.get('camera_elevation','unknown'),'target':item['target'],**compare_cycles(obj,ref)})
    any_compared=any(x['status']=='candidate_comparison' for x in comparisons)
    return {'status':'candidate_comparisons' if any_compared else 'unknown','reason':'' if any_compared else 'no_comparable_phase_in_confirmed_references' if comparisons else 'no_usable_confirmed_reference',
            'comparisons':comparisons,'note':'model-estimated comparison only, difference is not correctness; unknown elevation may mix camera heights'}


def main():
    p=argparse.ArgumentParser();p.add_argument('--video',type=Path,required=True);p.add_argument('--model',type=Path,required=True);p.add_argument('--run',type=Path,help='Optional similarity feature index');p.add_argument('--reference-registry',type=Path);p.add_argument('--camera-view',choices=('front','side','front_or_oblique','oblique','unknown'),default='unknown');p.add_argument('--camera-elevation',choices=('top_down','low_angle','unknown'),default='unknown');p.add_argument('--action',choices=('normal_squat','wide_squat','narrow_squat'));p.add_argument('--exclude-person-id');p.add_argument('--answers-json',type=Path,help='JSON object containing user-reported answers');p.add_argument('--previous-result',type=Path,help='Prior API output for descriptive comparison');p.add_argument('--output',type=Path)
    p.add_argument('--knee-mode', choices=('conservative', 'high-recall'), default='conservative')
    p.add_argument('--heel-review', action='store_true', help='Include experimental heel-raise candidates')
    p.add_argument('--all-labels', action='store_true', help='Return all 12 Part1 label observations')
    a=p.parse_args()
    obj=infer({'video_id':a.video.name,'video_path':a.video.name},a.video.parent,a.model,{'model_sha256':sha256(a.model)})
    quality=obj['analysis']['quality'];ready=quality['status']=='usable_candidate'
    result={'version':'squat-offline-api-v1','video_id':a.video.name,'status':quality['status'],'quality':quality,
            'cycles':obj['analysis']['cycles'],'features':obj['analysis']['features'],
            'reference_comparison':compare_registry(obj,a.reference_registry,a.run,a.model,a.camera_view,a.action,a.camera_elevation,a.exclude_person_id) if ready else {'status':'unknown','reason':'insufficient_pose'},
            'similar_videos':search(obj,a.run) if ready and a.run is not None else [],'limitations':['model estimated pose; no medical or safety judgment','heel and forefoot contact not inferred','phase and distances require validation']}
    answers=json.loads(a.answers_json.read_text(encoding='utf8')) if a.answers_json else None
    result['guidance']=build_guidance(obj,a.camera_view,a.action,answers,knee_mode=a.knee_mode,
                                     heel_review=a.heel_review,all_labels=a.all_labels)
    result['biomechanics']=analyze_biomechanics(obj,a.camera_view,result['guidance']['cycles'])
    prior=json.loads(a.previous_result.read_text(encoding='utf8')).get('guidance') if a.previous_result else None
    result['guidance']['followup_comparison']=compare_followup(result['guidance'],prior)
    data=json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
    if a.output:
        with a.output.open('x',encoding='utf8') as f:f.write(data)
    else:print(data)


if __name__=='__main__':main()
