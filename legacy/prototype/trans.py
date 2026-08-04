import json

with open('Nanna.txt', 'w', encoding='utf-8') as f:
    data = json.load(open('QA.json', 'r', encoding='utf-8'))
    for d in data:
        f.write('Q: '+d['conversations'][0]['value'] + '\n')
        f.write('A: '+d['conversations'][1]['value'] + '\n')
        f.write('\n')