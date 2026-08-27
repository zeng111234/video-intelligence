import json
r = json.load(open('work/auto-fine-cut-generalization-20260827-v5-provider-long-r8/production-local-run.json', encoding='utf-8'))
q = r['items'][0]['provider_payload']['local_export']['quality_report']
# psl 是不是嵌套的？
psl = q['provider_search_log']
print('type:', type(psl).__name__)
print('len:', len(psl))
print('first item keys:', list(psl[0].keys()) if psl else 'empty')
print('first item provider_status:', repr(psl[0].get('provider_status')))
print('first item provider_status type:', type(psl[0].get('provider_status')).__name__)
