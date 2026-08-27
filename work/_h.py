import json
with open('work/auto-fine-cut-adaptive-20260824-short-real/asr-full.json', encoding='utf-8') as f:
    asr = json.load(f)
ws = asr['segments'][0]['words'][:8]
for w in ws:
    t = w.get('word', '')
    print('  ' + repr(t) + '  codepoints=' + ' '.join(f'{ord(c):04x}' for c in t))
