"""通用工具：JSON 提取、分数归一化、加权融合、日志配置。"""

import json as _json
import logging
import re
from typing import Dict, List, Optional, Tuple

# ── 融合权重常量 ────────────────────────────────────────────────────
VECTOR_WEIGHT: float = 0.65     # 语义向量权重
BM25_WEIGHT: float = 0.35       # BM25 关键词权重
EPSILON: float = 1e-8           # 防止除零

# ── 多路召回常量 ────────────────────────────────────────────────────
BM25_RECALL_K: int = 10         # 内存 BM25 召回数
MILVUS_RECALL_K: int = 10       # 向量召回数（Milvus 或 numpy）


def setup_logging(name: str = "rag") -> logging.Logger:
    """获取模块级 Logger（根 Logger 已在 config.configure_root_logging 中配置）。"""
    return logging.getLogger(name)


def extract_json(text: str) -> Optional[dict]:
    """从文本中提取首个 JSON 对象（容忍 markdown/额外文本）。

    Args:
        text: 可能包含 JSON 的原始文本

    Returns:
        解析后的 dict，失败返回 None
    """
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return None
    try:
        return _json.loads(match.group(0))
    except _json.JSONDecodeError:
        return None


def normalize_scores(scores: list, max_val: float = 0.0) -> list:
    """将分数列表归一化到 [0,1] 区间。

    Args:
        scores: 原始分数列表
        max_val: 使用的最大值（0 表示自动取 max(scores)）

    Returns:
        归一化后的分数列表
    """
    if not scores:
        return []
    if max_val <= 0:
        max_val = max(scores)
    if max_val == 0:
        return [0.0] * len(scores)
    return [s / max_val for s in scores]


def weighted_fuse(
    bm25_hits: List[Tuple[int, float]],
    mv_hits: List[Tuple[int, float]],
    top_k: int = 10,
) -> List[Tuple[int, float]]:
    """多路召回去重合并 + 加权融合。

    保留原 65/35 权重策略。对于每路未命中的文档，该路分数视为 0。

    Args:
        bm25_hits: [(doc_idx, bm25_score), ...]  内存 BM25 结果
        mv_hits: [(doc_idx, cos_score), ...]     向量结果
        top_k: 最终返回数量

    Returns:
        [(doc_idx, fused_score), ...] 按融合分降序排列
    """
    if not bm25_hits and not mv_hits:
        return []

    # 去重合并
    combined: Dict[int, Dict[str, float]] = {}
    for doc_idx, score in bm25_hits:
        entry = combined.setdefault(doc_idx, {"bm25": 0.0, "vector": 0.0})
        entry["bm25"] = max(entry["bm25"], score)
    for doc_idx, score in mv_hits:
        entry = combined.setdefault(doc_idx, {"bm25": 0.0, "vector": 0.0})
        entry["vector"] = max(entry["vector"], score)

    # 归一化
    max_b = max((v["bm25"] for v in combined.values()), default=1.0)
    max_v = max((v["vector"] for v in combined.values()), default=1.0)

    # 加权融合
    rows = []
    for doc_idx, scores in combined.items():
        norm_b = scores["bm25"] / max_b if max_b else 0.0
        norm_v = scores["vector"] / max_v if max_v else 0.0
        fused = BM25_WEIGHT * norm_b + VECTOR_WEIGHT * norm_v
        rows.append((doc_idx, fused))

    rows.sort(key=lambda x: x[1], reverse=True)
    return rows[:top_k]


def full_fallback_fuse(
    bm25_scores: list,
    vector_scores: list,
    top_k: int = 10,
) -> List[Tuple[int, float]]:
    """全量融合回退：内存 BM25 + numpy 向量 → 加权融合。

    Args:
        bm25_scores: 每个文档的 BM25 分（len=N）
        vector_scores: [(doc_idx, cos_score), ...]
        top_k: 返回数量

    Returns:
        [(doc_idx, fused_score), ...]
    """
    n = len(bm25_scores)
    if n == 0:
        return []

    vec_dict: Dict[int, float] = {doc_idx: score for doc_idx, score in vector_scores}
    max_b = max(bm25_scores) if bm25_scores else 1.0
    max_v = max((s for _, s in vector_scores), default=1.0)

    rows = []
    for idx in range(n):
        norm_b = bm25_scores[idx] / max_b if max_b else 0.0
        norm_v = vec_dict.get(idx, 0.0) / max_v if max_v else 0.0
        fused = BM25_WEIGHT * norm_b + VECTOR_WEIGHT * norm_v
        rows.append((idx, fused))

    rows.sort(key=lambda x: x[1], reverse=True)
    return rows[:top_k]
