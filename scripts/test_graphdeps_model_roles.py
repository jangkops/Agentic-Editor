"""GraphDeps 모델 역할 필드 단위 테스트 (Task 1.4 / 요구사항 9).

검증 대상:
- `ai_engine/agent_system/deps.py` 의 `GraphDeps`:
    · model_planner / model_generator / model_evaluator 필드 존재.
    · 미주입 시 기본값 — Planner=Opus, Generator=Sonnet, Evaluator=Opus (요구사항 9.2/9.3/9.4).
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


def test_default_model_roles_opus_sonnet_opus():
    """미주입 시 Planner=Opus, Generator=Sonnet, Evaluator=Opus (요구사항 9.2/9.3/9.4)."""
    deps = GraphDeps()
    # Planner/Evaluator = Opus 계열.
    assert "opus" in deps.model_planner.lower()
    assert "opus" in deps.model_evaluator.lower()
    # Generator = Sonnet 계열.
    assert "sonnet" in deps.model_generator.lower()
    # 기본 상수와 정확히 일치.
    assert deps.model_planner == D._DEFAULT_PLANNER_MODEL
    assert deps.model_generator == D._DEFAULT_GENERATOR_MODEL
    assert deps.model_evaluator == D._DEFAULT_EVALUATOR_MODEL


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
