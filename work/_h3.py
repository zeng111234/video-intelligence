import json
with open('work/auto-fine-cut-adaptive-20260824-short-real/asr-full.json', encoding='utf-8') as f:
    asr = json.load(f)
ws = asr['segments'][0]['words'][:3]
for w in ws:
    t = w.get('word', '')
    print('  raw_bytes=' + t.encode('utf-8').hex() + '  repr=' + repr(t))
print()
print('text[0:5] =', repr(asr['segments'][0]['text'][:30]))
