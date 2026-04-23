"""대화 메모리 — 요약 체크포인트 기반 장기 기억.

전체 히스토리는 로컬에 보관하면서,
LLM에는 [요약 체크포인트] + [최근 원본 메시지]를 전달.
토큰 한도 내에서 최대한 많은 대화 맥락을 유지.
"""
import json
import os
import time
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field, asdict


@dataclass
class ConversationCheckpoint:
    """대화 요약 체크포인트."""
    session_id: str = ""
    summary: str = ""  # 이전 대화 요약
    message_count: int = 0  # 요약에 포함된 메시지 수
    last_updated: float = 0
    key_facts: List[str] = field(default_factory=list)  # 핵심 사실들


class ConversationMemory:
    """세션별 대화 메모리 관리."""

    RECENT_WINDOW = 6  # 원본으로 유지할 최근 메시지 수
    SUMMARIZE_THRESHOLD = 8  # 이 수 이상이면 요약 트리거
    MAX_SUMMARY_CHARS = 1500  # 요약 최대 길이
    MAX_MSG_CHARS = 800  # 개별 메시지 최대 길이

    def __init__(self, storage_dir: str = ""):
        self.storage_dir = storage_dir
        self._checkpoints: Dict[str, ConversationCheckpoint] = {}

    def _checkpoint_path(self, session_id: str) -> str:
        if self.storage_dir:
            os.makedirs(self.storage_dir, exist_ok=True)
            return os.path.join(self.storage_dir, f"conv_{session_id}.json")
        return ""

    def load_checkpoint(self, session_id: str) -> Optional[ConversationCheckpoint]:
        """저장된 체크포인트 로드."""
        if session_id in self._checkpoints:
            return self._checkpoints[session_id]
        path = self._checkpoint_path(session_id)
        if path and os.path.exists(path):
            try:
                with open(path) as f:
                    data = json.load(f)
                cp = ConversationCheckpoint(**data)
                self._checkpoints[session_id] = cp
                return cp
            except Exception:
                pass
        return None

    def save_checkpoint(self, cp: ConversationCheckpoint):
        """체크포인트 저장."""
        self._checkpoints[cp.session_id] = cp
        path = self._checkpoint_path(cp.session_id)
        if path:
            try:
                with open(path, 'w') as f:
                    json.dump(asdict(cp), f, ensure_ascii=False, indent=2)
            except Exception:
                pass

    def build_messages(
        self,
        session_id: str,
        chat_history: List[Dict],
        current_prompt: str,
        max_total_chars: int = 10000,
    ) -> Tuple[List[Dict], bool]:
        """
        LLM에 전달할 messages 배열 구성.
        반환: (messages, needs_summarize)
        """
        cp = self.load_checkpoint(session_id)
        messages = []
        total_chars = len(current_prompt)

        # 1. 체크포인트 요약이 있으면 첫 번째 user 메시지로 주입
        if cp and cp.summary:
            summary_msg = f"[이전 대화 요약]\n{cp.summary}"
            if cp.key_facts:
                summary_msg += "\n\n[핵심 사실]\n" + "\n".join(f"- {f}" for f in cp.key_facts[:10])
            messages.append({"role": "user", "content": [{"text": summary_msg}]})
            messages.append({"role": "assistant", "content": [{"text": "네, 이전 대화 내용을 이해했습니다. 계속 도와드리겠습니다."}]})
            total_chars += len(summary_msg) + 50

        # 2. 최근 메시지를 역순으로 추가 (최신 우선, 토큰 한도 내)
        valid_history = [
            m for m in (chat_history or [])
            if m.get("content") and m.get("role") in ("user", "assistant")
            and not m.get("content", "").startswith("[오류:")
        ]

        selected = []
        for msg in reversed(valid_history[-self.RECENT_WINDOW:]):
            content = msg["content"][:self.MAX_MSG_CHARS]
            if total_chars + len(content) > max_total_chars:
                break
            total_chars += len(content)
            selected.insert(0, {"role": msg["role"], "content": [{"text": content}]})

        messages.extend(selected)

        # 3. 현재 질문
        messages.append({"role": "user", "content": [{"text": current_prompt}]})

        # 4. Bedrock 규칙: user/assistant 교대
        cleaned = self._clean_messages(messages)

        # 5. 요약 필요 여부 판단
        summarized_count = cp.message_count if cp else 0
        total_messages = len(valid_history) + 1  # +1 for current
        needs_summarize = (total_messages - summarized_count) >= self.SUMMARIZE_THRESHOLD

        return cleaned, needs_summarize

    def _clean_messages(self, messages: List[Dict]) -> List[Dict]:
        """Bedrock user/assistant 교대 규칙 적용."""
        if not messages:
            return messages
        cleaned = []
        last_role = None
        for m in messages:
            if m["role"] == last_role:
                cleaned[-1]["content"][0]["text"] += "\n" + m["content"][0]["text"]
            else:
                cleaned.append(m)
                last_role = m["role"]
        if cleaned and cleaned[0]["role"] == "assistant":
            cleaned = cleaned[1:]
        return cleaned

    async def summarize_and_checkpoint(
        self,
        session_id: str,
        chat_history: List[Dict],
        gateway_client,
        model_id: str = "anthropic.claude-haiku-4-5-20251001-v1:0",
    ):
        """대화를 요약하고 체크포인트 저장. Haiku로 요약 (빠르고 저렴)."""
        cp = self.load_checkpoint(session_id) or ConversationCheckpoint(session_id=session_id)

        # 요약할 메시지: 체크포인트 이후 ~ 최근 RECENT_WINDOW 이전
        valid = [
            m for m in (chat_history or [])
            if m.get("content") and m.get("role") in ("user", "assistant")
            and not m.get("content", "").startswith("[오류:")
        ]

        if len(valid) < self.SUMMARIZE_THRESHOLD:
            return

        # 요약 대상: 오래된 메시지들 (최근 RECENT_WINDOW개 제외)
        to_summarize = valid[:-self.RECENT_WINDOW] if len(valid) > self.RECENT_WINDOW else valid

        if not to_summarize:
            return

        # 요약 프롬프트
        conversation_text = ""
        for m in to_summarize:
            role_label = "사용자" if m["role"] == "user" else "AI"
            conversation_text += f"{role_label}: {m['content'][:500]}\n"

        existing_summary = f"기존 요약:\n{cp.summary}\n\n" if cp.summary else ""

        summary_prompt = f"""{existing_summary}다음 대화를 간결하게 요약하세요. 핵심 주제, 결정사항, 중요한 정보를 포함하세요.

대화:
{conversation_text[:4000]}

요약 (3~5문장):"""

        try:
            messages = [{"role": "user", "content": [{"text": summary_prompt}]}]
            result = await gateway_client.converse(
                model_id=model_id,
                messages=messages,
                system_prompt="대화를 간결하게 요약하는 전문가입니다. 핵심만 추출하세요.",
            )

            if result.get("decision") == "ALLOW":
                output = result.get("output", {}).get("message", {}).get("content", [])
                summary_text = "\n".join(c.get("text", "") for c in output if "text" in c)
                if summary_text:
                    # 핵심 사실 추출
                    facts = [line.strip("- ").strip() for line in summary_text.split("\n") if line.strip().startswith("-")]

                    cp.summary = summary_text[:self.MAX_SUMMARY_CHARS]
                    cp.key_facts = facts[:10]
                    cp.message_count = len(valid) - self.RECENT_WINDOW
                    cp.last_updated = time.time()
                    self.save_checkpoint(cp)
                    print(f"[Memory] 체크포인트 저장: {session_id}, {cp.message_count}개 메시지 요약")
        except Exception as e:
            print(f"[Memory] 요약 실패: {e}")


# 전역 인스턴스
_memory_instance: Optional[ConversationMemory] = None


def get_memory(storage_dir: str = "") -> ConversationMemory:
    global _memory_instance
    if _memory_instance is None:
        _memory_instance = ConversationMemory(storage_dir=storage_dir)
    return _memory_instance
