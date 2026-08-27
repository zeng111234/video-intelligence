import json
r = json.load(open('work/auto-fine-cut-generalization-20260827-v5-provider-long-r8/production-local-run.json', encoding='utf-8'))
q = r['items'][0]['provider_payload']['local_export']['quality_report']
psl = q['provider_search_log']
print('total psl:', len(psl))
for i, e in enumerate(psl):
    status = e.get('provider_status')
    faid = e.get('final_asset_id')
    s = e.get('start')
    ed = e.get('end')
    print(f'[{i}] status={status} final_asset_id={faid} start={s} end={ed}')
