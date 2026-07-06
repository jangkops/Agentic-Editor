from fastapi.testclient import TestClient
from ai_engine.server import app, _IMAGE_GEN_CIRCUIT
import time as _t
c = TestClient(app)
# case 1: healthy circuit
_IMAGE_GEN_CIRCUIT['disabled_at'] = 0
r = c.get('/api/debug/image-gen-status')
assert r.status_code == 200, r.status_code
b = r.json()
for k in ('circuit', 'models', 'selectPreview', 'env', 'recentAttempts'):
    assert k in b, f'missing {k}: {list(b.keys())}'
assert isinstance(b['circuit']['disabled_at'], (int, float))
assert isinstance(b['circuit']['ttl'], int)
assert isinstance(b['circuit']['isBroken'], bool)
assert b['circuit']['isBroken'] is False
for ev in ('AE_IMAGE_PARALLEL_N', 'AE_IMAGE_QUALITY_THRESHOLD', 'AE_FORCE_NATIVE_DIAGRAM', 'AE_DISABLE_HTML_SLIDES'):
    assert ev in b['env'], f'missing env {ev}'
# case 2: broken circuit — must still return 200
_IMAGE_GEN_CIRCUIT['disabled_at'] = _t.time()
r2 = c.get('/api/debug/image-gen-status')
assert r2.status_code == 200, r2.status_code
assert r2.json()['circuit']['isBroken'] is True
_IMAGE_GEN_CIRCUIT['disabled_at'] = 0
print('OK')
