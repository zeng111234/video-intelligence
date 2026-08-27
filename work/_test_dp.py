import sys
sys.path.insert(0, 'C:/Users/zeng/Desktop/video')
from src.services.director_plan import build_director_plan, DIRECTOR_PLAN_VERSION
print('DIRECTOR_PLAN_VERSION:', DIRECTOR_PLAN_VERSION)

segments = [
    {'start': 0.0, 'end': 5.0, 'text': '增长了 30%'},
    {'start': 5.0, 'end': 10.0, 'text': '第一步打开'},
    {'start': 10.0, 'end': 15.0, 'text': '评论留言'},
    {'start': 15.0, 'end': 20.0, 'text': '普通句子'},
    {'start': 20.0, 'end': 25.0, 'text': '普通句子'},
    {'start': 25.0, 'end': 30.0, 'text': '普通句子'},
    {'start': 30.0, 'end': 35.0, 'text': '普通句子'},
]
plan = build_director_plan(segments, duration_seconds=35.0, title='test')
print('plan_version:', plan.get('plan_version'))
print('has visual_window_plan:', 'visual_window_plan' in plan)
if 'visual_window_plan' in plan:
    vwp = plan['visual_window_plan']
    print('  target_min:', vwp.get('target_real_coverage_seconds_min'))
    print('  required_window_count:', vwp.get('required_window_count'))
