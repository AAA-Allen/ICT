import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from squat_dual_analysis import analyze_dual
class DualTests(unittest.TestCase):
 def test_missing_does_not_create_results(self):
  self.assertEqual(analyze_dual({}, {})['kinematics']['findings'], [])
 def test_valid_kinematics_work_with_unknown_view(self):
  b={'metrics':{'left_knee_flexion_deg':{'status':'estimated','coverage':.9,'p95':75}}}
  r=analyze_dual(b,{'observations':[{'status':'unknown'}]})
  self.assertEqual(r['kinematics']['findings'][0]['id'],'motion-depth')
  self.assertEqual(r['skeleton']['unknown_count'],1)
 def test_low_coverage_and_nan_are_not_used(self):
  for m in ({'status':'estimated','coverage':.2,'p95':90},{'status':'estimated','coverage':1,'p95':float('nan')}):
   self.assertEqual(analyze_dual({'metrics':{'left_knee_flexion_deg':m}}, {})['kinematics']['findings'],[])
 def test_partial_cycles_not_used_for_tempo(self):
  b={'cycles':[{'boundary_status':'incomplete','descent_to_bottom_sec':1,'active_ascent_sec':1}]}
  self.assertEqual(analyze_dual(b,{})['kinematics']['findings'],[])
 def test_complete_cycle_has_timing(self):
  b={'cycles':[{'boundary_status':'candidate_complete','descent_to_bottom_sec':2,'active_ascent_sec':1,'metrics':{'left_knee_flexion_deg':{'status':'estimated'}}}]}
  self.assertEqual(analyze_dual(b,{})['kinematics']['findings'][0]['id'],'motion-tempo')
 def test_difference_never_becomes_knee_inward(self):
  b={'metrics':{'bilateral_knee_difference_deg':{'status':'estimated','coverage':1,'median':30,'p95':40}}}
  r=analyze_dual(b,{})
  self.assertEqual(r['skeleton']['candidate_count'],0)
  self.assertEqual(r['kinematics']['findings'][0]['id'],'motion-symmetry')
if __name__=='__main__':unittest.main()
