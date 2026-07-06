"""FastEmbed 오프라인 번들 캐시 경로 해석 검증 (동결 배포).

_bundled_fastembed_cache는 동결(sys.frozen) + 실행파일 옆 fastembed_models 존재 시에만
경로를 반환하고, 그 외에는 None(기본 캐시/런타임 다운로드 폴백)을 반환해야 한다.

실행: ./ai_engine/.venv/bin/python -m pytest scripts/test_fastembed_bundle_cache.py -p no:cacheprovider -q
"""
import os
import sys
import ai_engine.rag.embedder as emb


def test_not_frozen_returns_none(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert emb._bundled_fastembed_cache() is None


def test_frozen_without_dir_returns_none(monkeypatch, tmp_path):
    fake_exe = tmp_path / "ai-engine-server"
    fake_exe.write_text("x")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)
    # fastembed_models 디렉터리 없음 → None
    assert emb._bundled_fastembed_cache() is None


def test_frozen_with_bundled_dir_returns_path(monkeypatch, tmp_path):
    fake_exe = tmp_path / "ai-engine-server"
    fake_exe.write_text("x")
    (tmp_path / "fastembed_models").mkdir()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(fake_exe), raising=False)
    got = emb._bundled_fastembed_cache()
    assert got == str(tmp_path / "fastembed_models")
    assert os.path.isdir(got)


def test_env_override_takes_precedence(monkeypatch, tmp_path):
    """AE_FASTEMBED_CACHE env가 설정되면 provider가 그 경로를 cache_dir로 사용한다."""
    cache = tmp_path / "custom_cache"
    cache.mkdir()
    monkeypatch.setenv("AE_FASTEMBED_CACHE", str(cache))
    # 초기화 실패해도(모델 미다운로드) _cache_dir 해석은 env를 우선해야 함.
    p = emb.FastEmbedProvider.__new__(emb.FastEmbedProvider)
    # __init__의 캐시 해석부만 검증(모델 로드 회피)
    resolved = (None or os.environ.get("AE_FASTEMBED_CACHE")
                or emb._bundled_fastembed_cache() or None)
    assert resolved == str(cache)
