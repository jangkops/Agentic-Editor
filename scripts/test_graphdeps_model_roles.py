"""GraphDeps 모델 역할 필드 단위 테스트 (Task 1.4 / 요구사항 9).

검증 대상:
- `ai_engine/agent_system/deps.py` 의 `GraphDeps`:
    · model_planner / model_generator / model_evaluator 필드 존재.
    · 미주입 시 기본값 — Planner/Generator/Evaluator 모두 Sonnet 4.5 (동기 신뢰 경로).
      설계 의도(steering)는 Planner/Evaluator=Opus 이나, Opus 는 게이트웨이 `/converse`
      에서 비동기 S3 잡 폴링 경로를 타 evaluator/planner 의 wait_for 타임아웃에 걸려
      실전 무력화되므로, 프로덕션 기본값은 동기 응답하는 Sonnet 을 채택한다.
      Opus 는 deps 주입으로 계속 사용 가능(요구사항 9.5).
    · 주입 시 주입값 사용 (요구사항 9.5).
    · 기존 model_coding 필드 하위 호환 유지.

gateway·네트워크 불필요, 유한 시간.
실행: ai_engine/.venv/bin/python -m pytest scripts/test_graphdeps_model_roles.py -q
"""
import dataclasses
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_engine.agent_system import deps as D
from ai_engine.agent_system.deps import GraphDeps


def test_fields_exist():
    field_names = {f.name for f in dataclasses.fields(GraphDeps)}
    assert {"model_planner", "model_generator", "model_evaluator"} <= field_names
    # 기존 필드 하위 호환 유지.
    assert "model_coding" in field_names


def test_default_model_roles_reliable_sync_path():
    """미주입 시 Planner/Generator/Evaluator 모두 Sonnet 4.5 (동기 신뢰 경로).

    Opus 는 게이트웨이 `/converse` 에서 ACCEPTED→S3 잡 폴링(max_wait=300s) 비동기 경로를
    타므로, wait_for(AE_*_TIMEOUT=300s) 로 감싼 evaluator/planner 단발 호출이 실전에서
    거의 항상 타임아웃 폴백된다. → 동기 응답하는 Sonnet 을 프로덕션 기본값으로 채택.
    Opus 는 deps 주입으로 계속 사용 가능(test_injected_model_roles_are_used 참고).
    """
    deps = GraphDeps()
    # Planner/Generator/Evaluator = Sonnet 계열(동기 신뢰 경로).
    assert "sonnet" in deps.model_planner.lower()
    assert "sonnet" in deps.model_generator.lower()
    assert "sonnet" in deps.model_evaluator.lower()
    # 기본 상수와 정확히 일치.
    assert deps.model_planner == D._DEFAULT_PLANNER_MODEL
    assert deps.model_generator == D._DEFAULT_GENERATOR_MODEL
    assert deps.model_evaluator == D._DEFAULT_EVALUATOR_MODEL


def test_opus_remains_injectable_for_planner_and_evaluator():
    """설계 의도(Opus Planner/Evaluator)는 deps 주입으로 그대로 실현 가능(요구사항 9.5)."""
    opus = "us.anthropic.claude-opus-4-1-20250805-v1:0"
    deps = GraphDeps(model_planner=opus, model_evaluator=opus)
    assert deps.model_planner == opus
    assert deps.model_evaluator == opus


def test_injected_model_roles_are_used():
    """deps 로 특정 역할 model_id 주입 시 주입값 사용 (요구사항 9.5)."""
    deps = GraphDeps(
        model_planner="custom.planner-model",
        model_generator="custom.generator-model",
        model_evaluator="custom.evaluator-model",
    )
    assert deps.model_planner == "custom.planner-model"
    assert deps.model_generator == "custom.generator-model"
    assert deps.model_evaluator == "custom.evaluator-model"


def test_partial_injection_keeps_other_defaults():
    """일부만 주입하면 나머지는 기본값 유지."""
    deps = GraphDeps(model_evaluator="custom.evaluator")
    assert deps.model_evaluator == "custom.evaluator"
    assert deps.model_planner == D._DEFAULT_PLANNER_MODEL
    assert deps.model_generator == D._DEFAULT_GENERATOR_MODEL


def test_no_credentials_in_defaults():
    """모델 역할 기본 상수에 자격증명 흔적이 없다(요구사항 10)."""
    for val in (D._DEFAULT_PLANNER_MODEL, D._DEFAULT_GENERATOR_MODEL, D._DEFAULT_EVALUATOR_MODEL):
        low = val.lower()
        assert "accesskey" not in low
        assert "secret" not in low
