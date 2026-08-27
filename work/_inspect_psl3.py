import json
import sys
from pathlib import Path
ROOT = Path('.').resolve()
sys.path.insert(0, str(ROOT))
from src.services.video_editor_workflow import _is_real_stock_video_broll

with open('work/auto-fine-cut-generalization-20260827-v5-provider-long-r8/production-local-run.json', encoding='utf-8') as f:
    r = json.load(f)
q = r['items'][0]['provider_payload']['local_export']['quality_report']
psl = q['provider_search_log']
real_brolls = []
for entry in psl:
    if entry.get('provider_status') == 'skipped_local_semantic_match':
        real_brolls.append({
            'asset_id': entry.get('final_asset_id'),
            'mode': entry.get('final_mode'),
            'source_provider': entry.get('provider'),
            'authorization_status': 'confirmed',
            'publish_licensed': True,
            'start': float(entry.get('start', 0) or 0),
            'end': float(entry.get('end', 0) or 0),
        })
print('real_brolls collected:', len(real_brolls))
print()
for b in real_brolls:
    print('  asset_id:', b['asset_id'], 'provider:', b['source_provider'])
    print('    asset_origin inferred: stock_video_asset (pe provider + confirmed)')
    is_real = _is_real_stock_video_broll(b)
    print('    _is_real_stock_video_broll:', is_real)
    print('    duration:', b['end'] - b['start'])
