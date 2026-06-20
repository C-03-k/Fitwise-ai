"""向量存储层：Milvus 向量检索 + Embedding API。

- MilvusClient：Milvus Collection 管理、ANN 检索、增量写入
- EmbeddingService：批量调用 Embedding API，支持降级零向量
"""

import logging
from typing import Optional, Tuple

import numpy as np
import requests

from app.config import (
    EMBEDDING_MODEL,
    MILVUS_COLLECTION,
    MILVUS_HOST,
    MILVUS_PORT,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
)

logger = logging.getLogger(__name__)

# ── 常量 ────────────────────────────────────────────────────────────
EMBED_BATCH_SIZE: int = 20         # Embedding API 每批文本数
EMBED_DIM: int = 1536               # 默认向量维度（text-embedding-v1/v3 均为 1536）
MILVUS_INSERT_BATCH: int = 100     # Milvus 每批插入数
MILVUS_NLIST: int = 128            # IVF_FLAT 聚类数
MILVUS_NPROBE: int = 16            # ANN 搜索探测数
EMBED_TIMEOUT: int = 60            # Embedding API 超时（秒）

# 可选依赖
try:
    from pymilvus import (
        Collection,
        CollectionSchema,
        DataType,
        FieldSchema,
        connections,
        utility,
    )
    _MILVUS_AVAILABLE = True
except ModuleNotFoundError:
    _MILVUS_AVAILABLE = False
    logger.warning("pymilvus 未安装，向量检索将降级为内存 numpy。")

# ═══════════════════════════════════════════════════════════════════
#  Embedding 服务
# ═══════════════════════════════════════════════════════════════════
class EmbeddingService:
    """调用 Embedding API 生成向量。"""

    @staticmethod
    def batch_embed(texts: list) -> np.ndarray:
        """批量生成 Embedding，返回 numpy 向量数组 (N, dim)。

        Args:
            texts: 文本列表

        Returns:
            shape=(len(texts), dim) 的 float32 数组，
            无 API Key 或全部失败时返回零向量矩阵。
        """
        n = len(texts)
        if n == 0:
            return np.zeros((0, EMBED_DIM), dtype=np.float32)
        if not OPENAI_API_KEY:
            return np.zeros((n, EMBED_DIM), dtype=np.float32)

        embeddings: list = [None] * n
        embed_url = f"{OPENAI_BASE_URL.rstrip('/')}/embeddings"

        for start in range(0, n, EMBED_BATCH_SIZE):
            batch = texts[start:start + EMBED_BATCH_SIZE]
            try:
                resp = requests.post(
                    embed_url,
                    headers={
                        "Authorization": f"Bearer {OPENAI_API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={"model": EMBEDDING_MODEL, "input": batch},
                    timeout=EMBED_TIMEOUT,
                )
                resp.raise_for_status()
                for item in resp.json().get("data", []):
                    idx = item.get("index", -1)
                    if 0 <= idx < len(batch):
                        embeddings[start + idx] = np.array(
                            item["embedding"], dtype=np.float32)
            except Exception as exc:
                logger.warning("Embedding batch [%d:%d] 失败: %s", start, start + len(batch), exc)
                continue

        dim = len(embeddings[0]) if embeddings[0] is not None else EMBED_DIM
        result = np.array([
            e if e is not None else np.zeros(dim, dtype=np.float32)
            for e in embeddings
        ])
        logger.debug("Embedding 完成: %d/%d 条成功。", sum(1 for e in embeddings if e is not None), n)
        return result


# ═══════════════════════════════════════════════════════════════════
#  Milvus 客户端
# ═══════════════════════════════════════════════════════════════════
class MilvusClient:
    """Milvus 向量数据库客户端。

    管理 Collection 生命周期、ANN 检索、增量写入。
    不可用时所有操作静默返回空/False，由调用方降级处理。
    """

    def __init__(self):
        self._ok = False
        self._collection = None
        if not _MILVUS_AVAILABLE:
            return
        self._connect()

    def __del__(self):
        """释放 Milvus 连接。"""
        if self._ok:
            try:
                connections.disconnect("default")
            except Exception:
                pass

    @property
    def ok(self) -> bool:
        return self._ok

    def _connect(self) -> None:
        try:
            try:
                connections.disconnect("default")
            except Exception:
                pass
            connections.connect(
                alias="default",
                host=MILVUS_HOST,
                port=str(MILVUS_PORT),
                timeout=10,
            )
            if utility.has_collection(MILVUS_COLLECTION):
                self._collection = Collection(MILVUS_COLLECTION)
                self._ensure_index()
                self._collection.load()
            else:
                self._create_collection()
            self._ok = True
            logger.info("Milvus 连接成功，Collection: %s。", MILVUS_COLLECTION)
        except Exception as exc:
            self._ok = False
            self._collection = None
            logger.warning("Milvus 连接失败，降级为内存 numpy: %s", exc)

    def _ensure_index(self) -> None:
        if self._collection is None:
            return
        if not self._collection.has_index():
            self._collection.create_index(
                field_name="embedding",
                index_params={
                    "metric_type": "COSINE",
                    "index_type": "IVF_FLAT",
                    "params": {"nlist": MILVUS_NLIST},
                },
            )
            logger.info("Milvus 索引已补建（COSINE + IVF_FLAT, nlist=%d）。", MILVUS_NLIST)

    def _create_collection(self) -> None:
        fields = [
            FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema(name="parent_idx", dtype=DataType.INT64),
            FieldSchema(name="child_content", dtype=DataType.VARCHAR, max_length=2000),
            FieldSchema(name="source", dtype=DataType.VARCHAR, max_length=256),
            FieldSchema(name="domain", dtype=DataType.VARCHAR, max_length=64),
            FieldSchema(name="chunk_id", dtype=DataType.INT64),
            FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=EMBED_DIM),
        ]
        schema = CollectionSchema(fields, description="NutriFit Parent-Child 向量存储")
        self._collection = Collection(MILVUS_COLLECTION, schema)
        self._ensure_index()
        self._collection.load()
        logger.info("Milvus Collection '%s' 已创建（父子块模式）。", MILVUS_COLLECTION)

    @property
    def count(self) -> int:
        if not self._ok or self._collection is None:
            return 0
        try:
            return self._collection.num_entities
        except Exception:
            return 0

    def insert_children(self, children: list, embeddings: np.ndarray) -> bool:
        """批量插入子块向量到 Milvus。

        Args:
            children: [{"parent_idx","child_content","source","domain","chunk_id"}, ...]
            embeddings: shape=(len(children), dim) 子块的 embedding
        """
        if not self._ok or self._collection is None:
            return False
        n = len(children)
        try:
            insert_data = [
                [c.get("parent_idx", -1) for c in children],
                [c.get("child_content", "") for c in children],
                [c.get("source", "") for c in children],
                [c.get("domain", "") for c in children],
                [c.get("chunk_id", 0) for c in children],
                [embeddings[i].tolist() for i in range(n)],
            ]
            for start in range(0, n, MILVUS_INSERT_BATCH):
                end = start + MILVUS_INSERT_BATCH
                self._collection.insert([col[start:end] for col in insert_data])
            self._collection.flush()
            self._collection.load()
            logger.info("Milvus 插入 %d 条子块。", n)
            return True
        except Exception as exc:
            logger.error("Milvus 插入子块失败: %s", exc)
            self._ok = False
            return False

    def delete_all(self) -> bool:
        """清空 Collection。"""
        if not self._ok or self._collection is None:
            return False
        try:
            if self._collection.num_entities > 0:
                self._collection.delete(expr="id >= 0")
                self._collection.flush()
            return True
        except Exception as exc:
            logger.error("Milvus 清空失败: %s", exc)
            return False

    def search(self, query_emb: np.ndarray, top_k: int = 10) -> list:
        """ANN 向量检索（子块级别），返回 [(parent_idx, cos_score), ...]。

        Args:
            query_emb: shape=(dim,) 的查询向量
            top_k: 返回数量
        """
        if not self._ok or self._collection is None:
            return []
        try:
            self._collection.load()
            results = self._collection.search(
                data=[query_emb.tolist()],
                anns_field="embedding",
                param={"metric_type": "COSINE", "params": {"nprobe": MILVUS_NPROBE}},
                limit=min(top_k, max(self.count, 1)),
                output_fields=["parent_idx"],
            )
            hits = []
            for hit in results[0]:
                parent_idx = hit.entity.get("parent_idx", -1)
                if parent_idx >= 0:
                    hits.append((int(parent_idx), float(hit.distance)))
            return hits
        except Exception as exc:
            logger.warning("Milvus 搜索异常: %s", exc)
            return []


# ═══════════════════════════════════════════════════════════════════
#  向量相似度工具
# ═══════════════════════════════════════════════════════════════════
def vector_topk_numpy(query_emb: np.ndarray, doc_embeddings: np.ndarray, top_k: int = 10) -> list:
    """numpy 余弦相似度 + top_k（Milvus 降级用）。

    Args:
        query_emb: (dim,) 查询向量
        doc_embeddings: (N, dim) 文档向量矩阵
        top_k: 返回数量

    Returns:
        [(doc_idx, cos_score), ...]
    """
    if doc_embeddings is None or len(doc_embeddings) == 0:
        return []
    query_norm = np.linalg.norm(query_emb)
    if query_norm == 0:
        return []
    docs_norm = np.linalg.norm(doc_embeddings, axis=1)
    scores = np.dot(doc_embeddings, query_emb) / (docs_norm * query_norm + 1e-8)
    indices = np.argsort(scores)[::-1][:top_k]
    return [(int(i), float(scores[i])) for i in indices if scores[i] > 0]
