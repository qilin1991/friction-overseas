"""GA runner: 带 secret 转发一次 POST 到目标 API, 结果写 JSON 文件。"""
import argparse, json, os, requests

p = argparse.ArgumentParser()
p.add_argument('--url', required=True)
p.add_argument('--payload', required=True, help='JSON 字符串')
p.add_argument('--model', default='')
p.add_argument('--secret-name', default='', help='环境变量名, 从 secrets 注入')
p.add_argument('--key', required=True)
p.add_argument('--out', default='result.json')
a = p.parse_args()

payload = json.loads(a.payload)
if a.model:
    payload['model'] = a.model
headers = {'Content-Type': 'application/json'}
secret = os.environ.get(a.secret_name, '') if a.secret_name else ''
if secret:
    headers['Authorization'] = 'Bearer ' + secret
    headers['X-Relay-Token'] = secret   # 自建中继若需要

result = {'key': a.key, 'url': a.url, 'model': payload.get('model', ''), 'ok': False,
          'status': 0, 'error': '', 'body': '', 'elapsed': 0}
try:
    import time
    t0 = time.time()
    r = requests.post(a.url, headers=headers, json=payload, timeout=120)
    result['status'] = r.status_code
    result['body'] = r.text[:4000]
    result['ok'] = r.status_code == 200
    result['elapsed'] = round(time.time() - t0, 2)
except Exception as e:
    result['error'] = repr(e)[:500]

with open(a.out, 'w', encoding='utf-8') as f:
    json.dump(result, f, ensure_ascii=False)
print('写结果', a.out, 'status', result['status'])
