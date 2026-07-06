import asyncio, json, time
from ai_engine import server

server._IMAGE_GEN_CIRCUIT['disabled_at'] = time.time()
server._IMAGE_GEN_ATTEMPTS.clear()
server._IMAGE_GEN_ATTEMPTS.append({
    'ts': time.time(),
    'model': 'stability.sd3-5-large-v1:0',
    'status': 'error',
    'reason': 'AccessDenied: execute-api:Invoke not permitted',
    'durationMs': 12,
})
raw = asyncio.run(server._tool_generate_image({'prompt': 'test'}, '/tmp'))
p = json.loads(raw)
assert 'recentAttempts' in p, f'missing recentAttempts: {p}'
assert isinstance(p['recentAttempts'], list) and p['recentAttempts'], f'empty: {p}'
assert 'actionable' in p, f'missing actionable: {p}'
assert '권한 필요 모델' in p['actionable'], p['actionable']
assert 'admin must grant invoke permission' in p['actionable'], p['actionable']
server._IMAGE_GEN_CIRCUIT['disabled_at'] = 0
server._IMAGE_GEN_ATTEMPTS.clear()
print('OK')
