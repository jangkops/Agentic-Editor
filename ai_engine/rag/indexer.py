"""프로젝트 파일 인덱서 — 파일을 청크로 분할하고 TF-IDF 기반 검색 지원.

외부 임베딩 API 없이 로컬에서 동작하는 경량 RAG.
Bedrock 임베딩이 필요하면 embed_with_bedrock()으로 전환 가능.
"""
import os
import re
import math
import json
import hashlib
from typing import List, Dict, Tuple, Optional
from dataclasses import dataclass, field


@dataclass
class Chunk:
    """인덱싱된 코드 청크."""
    file_path: str
    start_line: int
    end_line: int
    content: str
    language: str
    tokens: List[str] = field(default_factory=list)


class ProjectIndexer:
    """프로젝트 파일을 청크로 분할하고 TF-IDF 검색을 제공."""

    IGNORE_DIRS = {
        'node_modules', '__pycache__', '.git', '.venv', 'dist', 'build',
        '.next', 'coverage', '.nyc_output', '.DS_Store', 'venv', 'env',
    }
    CODE_EXTS = {
        'js', 'ts', 'jsx', 'tsx', 'py', 'java', 'go', 'rs', 'c', 'cpp',
        'h', 'rb', 'php', 'swift', 'kt', 'css', 'scss', 'html', 'vue',
        'json', 'yml', 'yaml', 'toml', 'md', 'txt', 'sh', 'sql',
    }
    CHUNK_LINES = 60  # 청크당 최대 줄 수
    OVERLAP_LINES = 10  # 청크 간 오버랩

    def __init__(self):
        self.chunks: List[Chunk] = []
        self.idf: Dict[str, float] = {}
        self.project_path: str = ""
        self._file_tree: str = ""
        self._indexed_hash: str = ""

    def index_project(self, project_path: str) -> int:
        """프로젝트 전체를 인덱싱. 반환: 청크 수."""
        self.project_path = project_path
        self.chunks = []

        # 파일 트리 생성
        tree_lines = []
        self._walk_and_index(project_path, "", tree_lines, depth=0)
        self._file_tree = "\n".join(tree_lines[:200])  # 최대 200줄

        # IDF 계산
        self._compute_idf()

        # 해시 저장 (변경 감지용)
        self._indexed_hash = self._project_hash(project_path)
        return len(self.chunks)

    def _walk_and_index(self, base: str, rel: str, tree: list, depth: int):
        """재귀적으로 파일 탐색 + 청크 생성."""
        full = os.path.join(base, rel) if rel else base
        if not os.path.isdir(full):
            return
        try:
            entries = sorted(os.listdir(full))
        except PermissionError:
            return

        dirs, files = [], []
        for e in entries:
            if e.startswith('.') and e not in ('.kiro',):
                continue
            fp = os.path.join(full, e)
            if os.path.isdir(fp):
                if e not in self.IGNORE_DIRS:
                    dirs.append(e)
            else:
                files.append(e)

        indent = "  " * depth
        for d in dirs:
            tree.append(f"{indent}▸ {d}/")
            self._walk_and_index(base, os.path.join(rel, d) if rel else d, tree, depth + 1)

        for f in files:
            ext = f.rsplit('.', 1)[-1].lower() if '.' in f else ''
            tree.append(f"{indent}  {f}")
            if ext in self.CODE_EXTS:
                file_rel = os.path.join(rel, f) if rel else f
                self._index_file(base, file_rel, ext)

    def _index_file(self, base: str, rel_path: str, ext: str):
        """파일을 청크로 분할."""
        full = os.path.join(base, rel_path)
        try:
            with open(full, 'r', encoding='utf-8', errors='ignore') as fh:
                content = fh.read()
        except Exception:
            return

        if len(content) > 500_000:  # 500KB 초과 파일 스킵
            return

        lines = content.split('\n')
        lang = self._detect_language(ext)

        # 함수/클래스 경계로 분할 시도
        boundaries = self._find_boundaries(lines, lang)
        if boundaries:
            self._chunk_by_boundaries(rel_path, lines, boundaries, lang)
        else:
            # 고정 크기 청크
            for start in range(0, len(lines), self.CHUNK_LINES - self.OVERLAP_LINES):
                end = min(start + self.CHUNK_LINES, len(lines))
                chunk_content = '\n'.join(lines[start:end])
                if chunk_content.strip():
                    tokens = self._tokenize(chunk_content)
                    self.chunks.append(Chunk(
                        file_path=rel_path, start_line=start + 1,
                        end_line=end, content=chunk_content,
                        language=lang, tokens=tokens,
                    ))

    def _find_boundaries(self, lines: list, lang: str) -> list:
        """함수/클래스 시작 줄 번호를 찾음."""
        boundaries = [0]
        patterns = {
            'python': r'^(class |def |async def )',
            'javascript': r'^(function |class |const \w+ = |export |async function )',
            'typescript': r'^(function |class |const \w+ = |export |interface |type )',
        }
        pat = patterns.get(lang, r'^(function |class )')
        for i, line in enumerate(lines):
            if re.match(pat, line.strip()):
                if i - boundaries[-1] > 5:  # 최소 5줄 간격
                    boundaries.append(i)
        return boundaries if len(boundaries) > 1 else []

    def _chunk_by_boundaries(self, path: str, lines: list, boundaries: list, lang: str):
        """함수/클래스 경계로 청크 생성."""
        boundaries.append(len(lines))
        for i in range(len(boundaries) - 1):
            start = max(0, boundaries[i] - 2)  # 2줄 위 컨텍스트
            end = min(len(lines), boundaries[i + 1] + 2)
            chunk_content = '\n'.join(lines[start:end])
            if chunk_content.strip() and len(chunk_content) > 20:
                self.chunks.append(Chunk(
                    file_path=path, start_line=start + 1,
                    end_line=end, content=chunk_content,
                    language=lang, tokens=self._tokenize(chunk_content),
                ))

    def _tokenize(self, text: str) -> List[str]:
        """텍스트를 토큰으로 분할 (소문자, 영숫자+한글)."""
        return re.findall(r'[a-z_][a-z0-9_]*|[가-힣]+', text.lower())

    def _compute_idf(self):
        """IDF (Inverse Document Frequency) 계산."""
        n = len(self.chunks)
        if n == 0:
            return
        df = {}
        for chunk in self.chunks:
            seen = set(chunk.tokens)
            for token in seen:
                df[token] = df.get(token, 0) + 1
        self.idf = {t: math.log(n / (1 + c)) for t, c in df.items()}

    def search(self, query: str, top_k: int = 5) -> List[Tuple[Chunk, float]]:
        """TF-IDF 기반 유사도 검색. 반환: [(chunk, score), ...]"""
        query_tokens = self._tokenize(query)
        if not query_tokens or not self.chunks:
            return []

        # 쿼리 TF-IDF 벡터
        query_tf = {}
        for t in query_tokens:
            query_tf[t] = query_tf.get(t, 0) + 1
        query_vec = {t: tf * self.idf.get(t, 0) for t, tf in query_tf.items()}
        query_norm = math.sqrt(sum(v * v for v in query_vec.values())) or 1

        # 각 청크와 코사인 유사도
        results = []
        for chunk in self.chunks:
            chunk_tf = {}
            for t in chunk.tokens:
                chunk_tf[t] = chunk_tf.get(t, 0) + 1
            dot = 0
            chunk_norm_sq = 0
            for t, tf in chunk_tf.items():
                tfidf = tf * self.idf.get(t, 0)
                chunk_norm_sq += tfidf * tfidf
                if t in query_vec:
                    dot += query_vec[t] * tfidf
            chunk_norm = math.sqrt(chunk_norm_sq) or 1
            score = dot / (query_norm * chunk_norm)
            if score > 0.01:
                results.append((chunk, score))

        results.sort(key=lambda x: -x[1])
        return results[:top_k]

    def get_file_tree(self) -> str:
        """인덱싱된 프로젝트의 파일 트리 반환."""
        return self._file_tree

    def get_file_content(self, rel_path: str) -> Optional[str]:
        """파일 전체 내용 반환."""
        full = os.path.join(self.project_path, rel_path)
        try:
            with open(full, 'r', encoding='utf-8', errors='ignore') as f:
                return f.read()
        except Exception:
            return None

    def needs_reindex(self, project_path: str) -> bool:
        """프로젝트가 변경되었는지 확인."""
        return self._project_hash(project_path) != self._indexed_hash

    def _project_hash(self, project_path: str) -> str:
        """프로젝트 파일 목록의 해시 (변경 감지용)."""
        h = hashlib.md5()
        for root, dirs, files in os.walk(project_path):
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS and not d.startswith('.')]
            for f in sorted(files):
                fp = os.path.join(root, f)
                try:
                    h.update(f"{fp}:{os.path.getmtime(fp)}".encode())
                except Exception:
                    pass
        return h.hexdigest()

    def _detect_language(self, ext: str) -> str:
        lang_map = {
            'py': 'python', 'js': 'javascript', 'ts': 'typescript',
            'jsx': 'javascript', 'tsx': 'typescript', 'java': 'java',
            'go': 'go', 'rs': 'rust', 'rb': 'ruby', 'php': 'php',
            'css': 'css', 'html': 'html', 'json': 'json',
            'yml': 'yaml', 'yaml': 'yaml', 'md': 'markdown',
            'sh': 'shell', 'sql': 'sql',
        }
        return lang_map.get(ext, 'text')
