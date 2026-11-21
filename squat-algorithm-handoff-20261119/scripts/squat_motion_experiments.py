"""Exploratory skeleton grouping, retrieval, alignment, and development audit."""
import argparse
import csv
import gzip
import json
import math
from collections import Counter
from pathlib import Path

import numpy as np
from sklearn.cluster import KMeans, AgglomerativeClustering
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.metrics import silhouette_score, confusion_matrix, recall_score, f1_score
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

FEATURES=('peak_flexion_deg','median_stance_ratio_2d','median_torso_image_tilt_deg','median_bilateral_flexion_diff_deg','median_cycle_duration_sec')
BINS=('<30 degrees','30-90 degrees','>=90 degrees')


def read_csv(path):
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def load_clip(run,vid):
    with gzip.open(run/'clips'/(vid+'.json.gz'),'rt',encoding='utf8') as f:return json.load(f)


def load_features(path):
    rows=[r for r in read_csv(path) if r['quality']=='usable_candidate']
    matrix=np.array([[float(r[k]) if r.get(k) else np.nan for k in FEATURES] for r in rows])
    if not len(rows):return rows,None,None
    if np.any(np.all(np.isnan(matrix),axis=0)):raise ValueError('Some feature columns have no values')
    transform=SimpleImputer(strategy='median',add_indicator=True);raw=transform.fit_transform(matrix)
    scaler=StandardScaler();return rows,scaler.fit_transform(raw),(transform,scaler)


def dtw(x,y):
    """Align scalar traces; observed frame data required, no interpolation across gaps."""
    x=np.asarray(x,dtype=float);y=np.asarray(y,dtype=float)
    if len(x)<2 or len(y)<2 or not np.isfinite(x).all() or not np.isfinite(y).all():return None
    costs=np.full((len(x)+1,len(y)+1),np.inf);costs[0,0]=0
    for i in range(1,len(x)+1):
        for j in range(1,len(y)+1):costs[i,j]=abs(x[i-1]-y[j-1])+min(costs[i-1,j],costs[i,j-1],costs[i-1,j-1])
    i,j=len(x),len(y);path=[]
    while i>0 and j>0:
        path.append((i-1,j-1)); move=np.argmin([costs[i-1,j-1],costs[i-1,j],costs[i,j-1]])
        if move==0:i-=1;j-=1
        elif move==1:i-=1
        else:j-=1
    if i or j:return None
    return {'mean_abs_flexion_difference_deg':float(costs[len(x),len(y)]/len(path)),'alignment_pairs':len(path)}


def comparison_candidates(obj):
    """Trim standing plateaus using local baseline; candidate, not ground truth."""
    frames=obj['analysis'].get('frames',[]); result=[]
    for cycle in obj['analysis'].get('cycles',[]):
        series=[f for f in frames if cycle['start_sec']<=f['t']<=cycle['end_sec']]
        if len(series)<7 or any(f.get('flexion') is None for f in series):continue
        if any(b['t']-a['t']>.301 for a,b in zip(series,series[1:])):continue
        values=np.asarray([f['flexion'] for f in series],dtype=float)
        smooth=np.asarray([np.median(values[max(0,i-1):min(len(values),i+2)]) for i in range(len(values))])
        peak=min(range(len(series)),key=lambda i:abs(series[i]['t']-cycle['bottom_sec']))
        if peak<2 or peak>=len(series)-2:continue
        lv=float(np.min(smooth[:peak]));rv=float(np.min(smooth[peak+1:]));v=float(smooth[peak])
        if min(v-lv,v-rv)<15:continue
        lt=lv+max(5,.1*(v-lv));rt=rv+max(5,.1*(v-rv))
        left=next((i for i in range(peak-1,-1,-1) if smooth[i]<=lt),None)
        right=next((i for i in range(peak+1,len(series)) if smooth[i]<=rt),None)
        if left is None or right is None:continue
        start,bottom,end=series[left]['t'],series[peak]['t'],series[right]['t']
        if bottom-start<.199 or end-bottom<.199:continue
        result.append({'start_sec':start,'bottom_sec':bottom,'end_sec':end,'down':[start,bottom],'up':[bottom,end],
                       'boundary_method':'peak_to_local_baseline','status':'candidate_not_human_verified'})
    return result


def compare_cycles(a,b):
    ac=comparison_candidates(a);bc=comparison_candidates(b)
    if not ac or not bc:return {'status':'unknown','reason':'missing_phase_candidate'}
    def values(obj,cycle,start,end):
        data=[(f['t'],f['flexion']) for f in obj['analysis']['frames'] if start<=f['t']<=end]
        if len(data)<2 or any(v is None for _,v in data) or any(y[0]-x[0]>.301 for x,y in zip(data,data[1:])):return []
        return [v for _,v in data]
    results={}
    for name in ('down','up'):
        x=values(a,ac[0],*ac[0][name]);y=values(b,bc[0],*bc[0][name]);results[name]=dtw(x,y)
    return {'status':'candidate_comparison' if all(results.values()) else 'unknown','phase_dtw':results,
            'query_cycle':ac[0],'reference_cycle':bc[0],
            'note':'not a correctness score; candidate repetition boundaries require review; different viewpoints may distort angles'}


def experiment(run,manual,baseline,output):
    rows,x,_=load_features(run/'features.csv');output.mkdir(parents=True,exist_ok=False)
    result={'candidate_quality_videos':len(rows),'features':list(FEATURES),'clustering':{},'warnings':['no verified person separation','quality and phases are model estimates; test independently']}
    if x is None or len(rows)<6:
        result['error']='insufficient usable videos'
    else:
        for k in (2,3,4,5):
            if len(rows)<=k:continue
            for name,model in [('kmeans',KMeans(n_clusters=k,random_state=42,n_init=10)),('hierarchical',AgglomerativeClustering(n_clusters=k))]:
                labels=model.fit_predict(x);groups=[]
                for g in range(k):
                    indices=np.flatnonzero(labels==g);center=x[indices].mean(axis=0);d=np.linalg.norm(x[indices]-center,axis=1);order=np.argsort(d)
                    groups.append({'cluster':g,'count':len(indices),'center_examples':[rows[indices[i]]['video_id'] for i in order[:3]],
                                   'boundary_examples':[rows[indices[i]]['video_id'] for i in order[-3:]],
                                   'feature_medians':{f:float(np.median([float(rows[i][f]) for i in indices if rows[i].get(f)])) if any(rows[i].get(f) for i in indices) else None for f in FEATURES}})
                score=float(silhouette_score(x,labels)) if len(set(labels))>1 else None
                result['clustering'][f'{name}_{k}']={'silhouette_descriptive_only':score,'groups':groups}
                with (output/f'{name}_{k}.csv').open('w',encoding='utf-8-sig',newline='') as f:
                    w=csv.DictWriter(f,fieldnames=['video_id','cluster']);w.writeheader();w.writerows({'video_id':r['video_id'],'cluster':int(v)} for r,v in zip(rows,labels))
        anomaly=IsolationForest(n_estimators=100,random_state=42,contamination='auto').fit(x)
        scores=-anomaly.score_samples(x)
        with (output/'anomaly_candidates.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['video_id','relative_outlier_score','interpretation']);w.writeheader()
            for i in np.argsort(scores)[::-1]:w.writerow({'video_id':rows[i]['video_id'],'relative_outlier_score':round(float(scores[i]),6),'interpretation':'candidate_only_not_error'})
    manual_data=json.loads(manual.read_text(encoding='utf8'))
    labels={k:v.get('labels',{}).get('angle_label','') for k,v in manual_data.items()}
    labeled=[r for r in rows if labels.get(r['video_id']) in BINS]
    label_counts=Counter(labels[r['video_id']] for r in labeled)
    supervised={'development_label_counts':dict(label_counts),'status':'not_trained','candidate_for_use':False,
                'warning':'exploratory video-level folds only; no person isolation, previously reviewed labels, and tiny classes; not independent accuracy'}
    min_class=min((label_counts.get(k,0) for k in BINS),default=0)
    if min_class>=2:
        folds=min(3,min_class);z=np.array([[float(r[k]) if r.get(k) else np.nan for k in FEATURES] for r in labeled]); y=np.array([labels[r['video_id']] for r in labeled]);pred=np.empty(len(y),dtype=object)
        for train,test in StratifiedKFold(n_splits=folds,shuffle=True,random_state=42).split(z,y):
            model=make_pipeline(SimpleImputer(strategy='median',add_indicator=True,keep_empty_features=True),StandardScaler(),LogisticRegression(class_weight='balanced',max_iter=2000,random_state=42))
            model.fit(z[train],y[train]);pred[test]=model.predict(z[test])
        scores=lambda truth,predict:{'n':len(truth),'agree':int(np.sum(truth==predict)), 'confusion':confusion_matrix(truth,predict,labels=list(BINS)).tolist(),
                                    'recall_by_class':dict(zip(BINS,recall_score(truth,predict,labels=list(BINS),average=None,zero_division=0).tolist())),
                                    'macro_f1':float(f1_score(truth,predict,labels=list(BINS),average='macro',zero_division=0))}
        base={r['video_id']:r for r in read_csv(baseline)};paired=[]
        for i,r in enumerate(labeled):
            raw=base[r['video_id']]['max_knee_flexion_deg']
            if raw:
                a=float(raw);paired.append((i,'<30 degrees' if a<30 else '30-90 degrees' if a<90 else '>=90 degrees'))
        supervised.update(status='exploratory_cross_validation',folds=folds,model=scores(y,pred),
                          paired_model=scores(np.array([y[i] for i,_ in paired]),np.array([pred[i] for i,_ in paired])),
                          paired_v2=scores(np.array([y[i] for i,_ in paired]),np.array([v for _,v in paired])))
        with (output/'supervised_development_predictions.csv').open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['video_id','human_label','out_of_fold_prediction']);w.writeheader()
            w.writerows({'video_id':r['video_id'],'human_label':y[i],'out_of_fold_prediction':pred[i]} for i,r in enumerate(labeled))
    else:supervised['reason']='at least one angle bin has fewer than two human-labeled videos'
    result['supervised']=supervised
    (output/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    lines=['# 骨架运动探索报告','','输出为开发阶段探索；不能据此判断动作正确或安全，也不能作为独立准确率。',f"姿态质量候选可用：{len(rows)} 条。聚类与异常值会受视角、检测噪声和缺失值影响。",'', '聚类为 2～5 类的 K-Means 和层次聚类，详见 summary.json 及对应 CSV；每类提供中心和边缘样例供人工查看。异常分数只表示相对少见。','',f"已有人工角度标签分布（探索样本）：{dict(label_counts)}；监督实验状态：{supervised['status']}。人物身份未可靠隔离、类别极少，任何交叉验证仅为开发诊断，不能作为独立准确率或候选模型。",'', '示范视频需要人工确认身份、动作目标、视角和使用权限；在没有确认示范时，接口返回 unknown。','']
    (output/'运动探索报告.md').write_text('\n'.join(lines),encoding='utf8')
    print(json.dumps({'rows':len(rows),'supervised':result['supervised']},ensure_ascii=False))


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--manual',type=Path,required=True);p.add_argument('--baseline',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args();experiment(a.run,a.manual,a.baseline,a.output)


if __name__=='__main__':main()
