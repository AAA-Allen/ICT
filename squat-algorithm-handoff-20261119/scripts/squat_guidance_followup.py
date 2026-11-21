"""Apply questionnaire answers to an existing result without rerunning video inference."""
import argparse
import json
from pathlib import Path
from squat_guidance import subjective_answers, compare_followup

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--result',type=Path,required=True)
    p.add_argument('--answers-json',type=Path,required=True)
    p.add_argument('--previous-result',type=Path)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    obj=json.loads(args.result.read_text(encoding='utf8'))
    obj['guidance']['subjective_feedback']=subjective_answers(json.loads(args.answers_json.read_text(encoding='utf8')))
    prior=json.loads(args.previous_result.read_text(encoding='utf8'))['guidance'] if args.previous_result else None
    obj['guidance']['followup_comparison']=compare_followup(obj['guidance'],prior)
    with args.output.open('x',encoding='utf8') as f:json.dump(obj,f,ensure_ascii=False,indent=2,allow_nan=False)

if __name__=='__main__':main()
