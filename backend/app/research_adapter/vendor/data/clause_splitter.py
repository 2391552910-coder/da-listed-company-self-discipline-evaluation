"""
文本切分策略（实验消融对照的核心变量）

本方法：clause 条款级切分——按法规"第X条"边界切分，保证每条检索单元是
完整法条，引用时可精确定位到条款号（证据溯源的前提）。
消融对照：window 固定长度滑窗切分——RAG 常用基线做法。
"""
import re
from dataclasses import dataclass, field

CLAUSE_PATTERN = re.compile(
    r"(第[一二三四五六七八九十百千零0-9]+条(?:之[一二三四五六七八九十0-9]+)?)"
)


@dataclass
class Chunk:
    """检索单元"""
    chunk_id: str          # 全局唯一 ID，如 DOC001-C03（评测集标注引用它）
    doc_id: str
    doc_title: str
    doc_type: str          # regulation / penalty / announcement
    source: str
    clause_no: str | None  # 条款号（条款级切分时可定位；滑窗时为 None）
    text: str
    dimension: str | None = None  # 维度路由标签 D1-D4
    meta: dict = field(default_factory=dict)


def split_by_clause(doc_id: str, doc_title: str, doc_type: str, source: str,
                    text: str, dimension: str | None = None) -> list[Chunk]:
    """
    条款级切分（本方法）：
    按"第X条"边界切分，条款号写入 chunk.clause_no，引用可追溯。
    条款前的总则/附则等非条款文本并入相邻块。
    """
    chunks: list[Chunk] = []
    matches = list(CLAUSE_PATTERN.finditer(text))
    headings = [m for m in matches
                if not text[text.rfind("\n", 0, m.start()) + 1:m.start()].strip()]

    def flush(clause_no: str | None, start: int, body_start: int, end: int):
        body = text[body_start:end]
        leading = len(body) - len(body.lstrip())
        body = body.strip()
        if not body:
            return
        body_start += leading
        raw_end = body_start + len(body)
        prefix = clause_no or ""
        visible_end = body_start + min(len(body), 2000 - len(prefix))
        if clause_no is None:
            start = body_start
        is_heading = clause_no is not None and any(m.start() == start for m in headings)
        owner = [m.group() for m in headings if m.start() <= start]
        idx = len(chunks) + 1
        chunks.append(Chunk(
            chunk_id=f"{doc_id}-C{idx:03d}",
            doc_id=doc_id, doc_title=doc_title, doc_type=doc_type, source=source,
            clause_no=clause_no,
            text=(f"{clause_no}{body}" if clause_no else body)[:2000],
            dimension=dimension,
            meta={"source_start": start, "source_end": visible_end,
                  "raw_end": raw_end, "truncated": visible_end < raw_end,
                  "is_clause_heading": is_heading,
                  "owner_clause_no": owner[-1] if owner else None},
        ))

    # 保留历史 C 编号与文本，避免改变已有 gold/合成集的标注含义。
    # 旧切分器也在正文引用处切分；审计通过元信息揭示此问题，不将其视为完整法条。
    if not matches:
        flush(None, 0, 0, len(text))
    else:
        flush(None, 0, 0, matches[0].start())
        for i, match in enumerate(matches):
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            flush(match.group(), match.start(), match.end(), end)
    return chunks


def split_by_window(doc_id: str, doc_title: str, doc_type: str, source: str,
                    text: str, size: int = 512, overlap: int = 64,
                    dimension: str | None = None) -> list[Chunk]:
    """固定长度滑窗切分（消融对照）"""
    chunks: list[Chunk] = []
    step = max(size - overlap, 1)
    for start in range(0, len(text), step):
        piece = text[start:start + size].strip()
        if not piece:
            continue
        chunks.append(Chunk(
            chunk_id=f"{doc_id}-W{len(chunks) + 1:03d}",
            doc_id=doc_id, doc_title=doc_title, doc_type=doc_type, source=source,
            clause_no=None, text=piece, dimension=dimension,
            meta={"source_start": start + len(text[start:start + size]) - len(text[start:start + size].lstrip()),
                  "source_end": start + len(text[start:start + size].rstrip())},
        ))
    return chunks


def split_document(doc: dict, strategy: str = "clause", **kw) -> list[Chunk]:
    """统一入口：doc 为 knowledge_loader 读取的文档字典"""
    if strategy == "clause":
        return split_by_clause(doc["doc_id"], doc["title"], doc["doc_type"],
                               doc["source"], doc["text"], doc.get("dimension"))
    if strategy == "window":
        return split_by_window(doc["doc_id"], doc["title"], doc["doc_type"],
                               doc["source"], doc["text"],
                               size=kw.get("window_size", 512),
                               overlap=kw.get("window_overlap", 64),
                               dimension=doc.get("dimension"))
    raise ValueError(f"未知切分策略: {strategy}")
