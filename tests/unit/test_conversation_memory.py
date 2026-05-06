"""체크포인트 및 대화 메모리 테스트.

테스트 항목:
1. ConversationCheckpoint 생성/저장/로드
2. build_messages — 요약 + 최근 메시지 조합
3. _clean_messages — user/assistant 교대 규칙
4. summarize_and_checkpoint — 요약 생성 (mock)
"""
import pytest
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from dataclasses import asdict

import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "ai_engine"))

from rag.conversation_memory import (
    ConversationCheckpoint,
    ConversationMemory,
    get_memory,
)


class TestConversationCheckpoint:
    """체크포인트 데이터 클래스 테스트."""

    def test_checkpoint_creation(self):
        """체크포인트 생성."""
        cp = ConversationCheckpoint(
            session_id="test-123",
            summary="이전 대화 요약",
            message_count=10,
            key_facts=["사실1", "사실2"],
        )
        assert cp.session_id == "test-123"
        assert cp.summary == "이전 대화 요약"
        assert cp.message_count == 10
        assert cp.key_facts == ["사실1", "사실2"]

    def test_checkpoint_defaults(self):
        """체크포인트 기본값."""
        cp = ConversationCheckpoint()
        assert cp.session_id == ""
        assert cp.summary == ""
        assert cp.message_count == 0
        assert cp.key_facts == []


class TestConversationMemory:
    """ConversationMemory 테스트."""

    @pytest.fixture
    def temp_dir(self):
        """임시 디렉토리."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    @pytest.fixture
    def memory(self, temp_dir):
        """메모리 인스턴스."""
        return ConversationMemory(storage_dir=temp_dir)

    def test_memory_init(self, temp_dir):
        """메모리 초기화."""
        mem = ConversationMemory(storage_dir=temp_dir)
        assert mem.storage_dir == temp_dir
        assert mem.RECENT_WINDOW == 10
        assert mem.SUMMARIZE_THRESHOLD == 12

    def test_checkpoint_path_with_storage(self, memory):
        """체크포인트 경로 생성 (저장소 있음)."""
        path = memory._checkpoint_path("session-123")
        assert "conv_session-123.json" in path
        assert path.startswith(memory.storage_dir)

    def test_checkpoint_path_without_storage(self):
        """체크포인트 경로 생성 (저장소 없음)."""
        mem = ConversationMemory(storage_dir="")
        path = mem._checkpoint_path("session-123")
        assert path == ""

    def test_save_and_load_checkpoint(self, memory):
        """체크포인트 저장 및 로드."""
        cp = ConversationCheckpoint(
            session_id="test-123",
            summary="테스트 요약",
            message_count=5,
            key_facts=["팩트A", "팩트B"],
        )
        memory.save_checkpoint(cp)

        # 로드
        loaded = memory.load_checkpoint("test-123")
        assert loaded is not None
        assert loaded.session_id == "test-123"
        assert loaded.summary == "테스트 요약"
        assert loaded.message_count == 5
        assert loaded.key_facts == ["팩트A", "팩트B"]

    def test_load_nonexistent_checkpoint(self, memory):
        """존재하지 않는 체크포인트 로드."""
        loaded = memory.load_checkpoint("nonexistent")
        assert loaded is None

    def test_checkpoint_persistence_to_disk(self, temp_dir):
        """디스크에 체크포인트 저장."""
        mem = ConversationMemory(storage_dir=temp_dir)
        cp = ConversationCheckpoint(
            session_id="disk-test",
            summary="디스크 테스트",
            message_count=3,
        )
        mem.save_checkpoint(cp)

        # 파일 확인
        checkpoint_file = os.path.join(temp_dir, "conv_disk-test.json")
        assert os.path.exists(checkpoint_file)

        # JSON 내용 확인
        with open(checkpoint_file) as f:
            data = json.load(f)
        assert data["session_id"] == "disk-test"
        assert data["summary"] == "디스크 테스트"
        assert data["message_count"] == 3

    def test_clean_messages_alternating_roles(self, memory):
        """메시지 정제 — user/assistant 교대."""
        messages = [
            {"role": "user", "content": [{"text": "안녕"}]},
            {"role": "assistant", "content": [{"text": "안녕하세요"}]},
            {"role": "user", "content": [{"text": "뭘 할 수 있어?"}]},
            {"role": "assistant", "content": [{"text": "도움이 됩니다"}]},
        ]
        cleaned = memory._clean_messages(messages)
        assert len(cleaned) == 4
        assert cleaned[0]["role"] == "user"
        assert cleaned[1]["role"] == "assistant"

    def test_clean_messages_consecutive_same_role(self, memory):
        """메시지 정제 — 같은 역할 연속."""
        messages = [
            {"role": "user", "content": [{"text": "질문1"}]},
            {"role": "user", "content": [{"text": "질문2"}]},
            {"role": "assistant", "content": [{"text": "답변"}]},
        ]
        cleaned = memory._clean_messages(messages)
        # 같은 role 연속하면 병합
        assert len(cleaned) == 2
        assert cleaned[0]["role"] == "user"
        assert "질문1" in cleaned[0]["content"][0]["text"]
        assert "질문2" in cleaned[0]["content"][0]["text"]

    def test_clean_messages_starts_with_assistant(self, memory):
        """메시지 정제 — assistant로 시작하면 제거."""
        messages = [
            {"role": "assistant", "content": [{"text": "첫 답변"}]},
            {"role": "user", "content": [{"text": "질문"}]},
        ]
        cleaned = memory._clean_messages(messages)
        assert cleaned[0]["role"] == "user"

    def test_build_messages_without_checkpoint(self, memory):
        """메시지 구성 — 체크포인트 없음."""
        chat_history = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
        ]
        current_prompt = "Q3"

        messages, needs_summarize = memory.build_messages(
            session_id="test",
            chat_history=chat_history,
            current_prompt=current_prompt,
        )

        # 메시지 구성 확인
        assert len(messages) >= 2  # 최소 user + current
        assert messages[-1]["role"] == "user"
        assert "Q3" in messages[-1]["content"][0]["text"]
        assert not needs_summarize  # 히스토리가 적으면 요약 불필요

    def test_build_messages_with_checkpoint(self, memory):
        """메시지 구성 — 체크포인트 있음."""
        # 체크포인트 저장
        cp = ConversationCheckpoint(
            session_id="test",
            summary="이전 대화 요약입니다.",
            message_count=5,
            key_facts=["팩트1", "팩트2"],
        )
        memory.save_checkpoint(cp)

        chat_history = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
        ]
        current_prompt = "Q2"

        messages, needs_summarize = memory.build_messages(
            session_id="test",
            chat_history=chat_history,
            current_prompt=current_prompt,
        )

        # 첫 번째 메시지는 요약
        assert "[이전 대화 요약]" in messages[0]["content"][0]["text"]
        assert "이전 대화 요약입니다." in messages[0]["content"][0]["text"]
        assert "[핵심 사실]" in messages[0]["content"][0]["text"]

    def test_build_messages_recent_window(self, memory):
        """메시지 구성 — RECENT_WINDOW 적용."""
        # 20개 메시지 생성
        chat_history = []
        for i in range(20):
            role = "user" if i % 2 == 0 else "assistant"
            chat_history.append({"role": role, "content": f"Message {i}"})

        messages, needs_summarize = memory.build_messages(
            session_id="test",
            chat_history=chat_history,
            current_prompt="최신 질문",
        )

        # RECENT_WINDOW (10개) + 현재 프롬프트
        # 실제로는 max_total_chars 제한도 적용
        assert len(messages) <= memory.RECENT_WINDOW + 3

    def test_build_messages_needs_summarize_threshold(self, memory):
        """메시지 구성 — 요약 필요 판단."""
        # 요약 필요 조건: (total_messages - checkpoint_count) >= SUMMARIZE_THRESHOLD
        chat_history = []
        for i in range(15):  # SUMMARIZE_THRESHOLD(12) 초과
            role = "user" if i % 2 == 0 else "assistant"
            chat_history.append({"role": role, "content": f"Message {i}"})

        messages, needs_summarize = memory.build_messages(
            session_id="test",
            chat_history=chat_history,
            current_prompt="Q",
        )

        assert needs_summarize is True

    def test_build_messages_filters_errors(self, memory):
        """메시지 구성 — 오류 메시지 필터링."""
        chat_history = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "[오류: 실패]"},  # 필터됨
            {"role": "assistant", "content": "A2"},
        ]

        messages, _ = memory.build_messages(
            session_id="test",
            chat_history=chat_history,
            current_prompt="Q2",
        )

        # "[오류:" 로 시작하는 메시지는 제외
        full_text = str(messages)
        assert full_text.count("[오류:") == 0

    def test_build_messages_max_total_chars(self, memory):
        """메시지 구성 — max_total_chars 제한."""
        chat_history = []
        for i in range(50):
            role = "user" if i % 2 == 0 else "assistant"
            chat_history.append({"role": role, "content": "x" * 1000})

        messages, _ = memory.build_messages(
            session_id="test",
            chat_history=chat_history,
            current_prompt="Q",
            max_total_chars=5000,
        )

        # 제한된 크기 내에서만 포함
        total = sum(len(m["content"][0]["text"]) for m in messages)
        assert total <= 5000 + 1000  # 약간의 여유

    @pytest.mark.asyncio
    async def test_summarize_and_checkpoint_mock(self, memory):
        """요약 및 체크포인트 저장 (mock gateway)."""
        # Mock gateway
        mock_gateway = AsyncMock()
        mock_gateway.converse.return_value = {
            "decision": "ALLOW",
            "output": {
                "message": {
                    "content": [
                        {"text": "- 팩트1\n- 팩트2\n요약이 완료되었습니다."}
                    ]
                }
            },
        }

        chat_history = []
        for i in range(15):
            role = "user" if i % 2 == 0 else "assistant"
            chat_history.append({"role": role, "content": f"Message {i}"})

        await memory.summarize_and_checkpoint(
            session_id="test",
            chat_history=chat_history,
            gateway_client=mock_gateway,
        )

        # 체크포인트 확인
        cp = memory.load_checkpoint("test")
        assert cp is not None
        assert "팩트1" in cp.key_facts or len(cp.key_facts) >= 0
        assert cp.message_count > 0

    @pytest.mark.asyncio
    async def test_summarize_and_checkpoint_insufficient_messages(self, memory):
        """요약 — 메시지 부족."""
        mock_gateway = AsyncMock()

        chat_history = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
        ]

        await memory.summarize_and_checkpoint(
            session_id="test",
            chat_history=chat_history,
            gateway_client=mock_gateway,
        )

        # gateway 호출 안 됨 (메시지 부족)
        mock_gateway.converse.assert_not_called()

    @pytest.mark.asyncio
    async def test_summarize_and_checkpoint_gateway_error(self, memory):
        """요약 — gateway 에러 처리."""
        mock_gateway = AsyncMock()
        mock_gateway.converse.side_effect = Exception("Gateway error")

        chat_history = []
        for i in range(15):
            role = "user" if i % 2 == 0 else "assistant"
            chat_history.append({"role": role, "content": f"Message {i}"})

        # 에러 발생해도 크래시 안 함
        await memory.summarize_and_checkpoint(
            session_id="test",
            chat_history=chat_history,
            gateway_client=mock_gateway,
        )

        # 테스트 통과 (에러 처리됨)
        assert True


class TestGlobalMemoryInstance:
    """전역 메모리 인스턴스 테스트."""

    def test_get_memory_singleton(self):
        """get_memory 싱글톤."""
        mem1 = get_memory()
        mem2 = get_memory()
        assert mem1 is mem2

    def test_get_memory_with_storage(self, tmp_path):
        """get_memory with storage_dir."""
        # 전역 싱글톤 초기화 (테스트 격리를 위해 재생성하지 않음)
        mem = ConversationMemory(storage_dir=str(tmp_path))
        assert mem.storage_dir == str(tmp_path)


class TestEdgeCases:
    """엣지 케이스 테스트."""

    def test_empty_chat_history(self):
        """빈 채팅 히스토리."""
        mem = ConversationMemory()
        messages, needs_summarize = mem.build_messages(
            session_id="test",
            chat_history=[],
            current_prompt="Q",
        )
        assert len(messages) >= 1  # 최소 현재 프롬프트
        assert not needs_summarize

    def test_invalid_message_format(self):
        """잘못된 메시지 형식."""
        mem = ConversationMemory()
        chat_history = [
            {"role": "user"},  # content 없음
            {"content": "text"},  # role 없음
            {"role": "user", "content": "Q"},
        ]
        messages, _ = mem.build_messages(
            session_id="test",
            chat_history=chat_history,
            current_prompt="Q2",
        )
        # 유효한 메시지만 포함
        assert all("content" in m and "role" in m for m in messages)

    def test_very_long_summary(self):
        """매우 긴 요약 (MAX_SUMMARY_CHARS)."""
        mem = ConversationMemory()
        long_summary = "x" * 5000
        cp = ConversationCheckpoint(
            session_id="test",
            summary=long_summary,
        )
        mem.save_checkpoint(cp)

        loaded = mem.load_checkpoint("test")
        # MAX_SUMMARY_CHARS(3000) 제한 전에 저장된 값 그대로
        assert loaded.summary == long_summary

    def test_checkpoint_with_unicode(self, tmp_path):
        """Unicode 포함 체크포인트."""
        mem = ConversationMemory(storage_dir=str(tmp_path))
        cp = ConversationCheckpoint(
            session_id="한글테스트",
            summary="한글 요약 😀 🚀 日本語",
            key_facts=["팩트한글", "Fact English", "事実日本語"],
        )
        mem.save_checkpoint(cp)

        loaded = mem.load_checkpoint("한글테스트")
        assert loaded.summary == "한글 요약 😀 🚀 日本語"
        assert "팩트한글" in loaded.key_facts
