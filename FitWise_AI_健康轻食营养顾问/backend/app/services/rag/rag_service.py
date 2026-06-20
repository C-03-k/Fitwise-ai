"""核心 RAG 服务：父子块切分 → 多路召回 → 精排 → 事实校验 → 回答生成。

Parent-Child 检索流程：
  - 内存 BM25：索引 parent_content（关键词搜索）
  - Milvus：索引 child embedding（ANN 向量搜索）→ 返回 parent_idx
  - 检索 child → 取出 parent → 送入 rerank → LLM 生成
  - 降级：self.embeddings 存 parent 向量，用 numpy 做本地向量检索
"""

import csv
import logging
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np

from openai import OpenAI

from app.config import (
    CHAT_MODEL,
    DATA_DIR,
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
)

from .bm25_utils import BM25Index
from .text_processing import (
    DOMAIN_TERMS,
    chunk_csv_row,
    chunk_markdown,
    infer_domain,
    split_sentences,
)
from .utils import (
    BM25_RECALL_K,
    MILVUS_RECALL_K,
    extract_json,
    full_fallback_fuse,
    setup_logging,
    weighted_fuse,
)
from .vector_store import (
    EMBED_DIM,
    EmbeddingService,
    MilvusClient,
    vector_topk_numpy,
)

logger = setup_logging("rag")

# ── 常量 ────────────────────────────────────────────────────────────
EXCLUDED_KNOWLEDGE_FILES: set = set()
COARSE_TOP_K_DEFAULT: int = 15       # 粗排数量（父子模式下 parent 数增多，适度放大）

MAX_REWRITE_LENGTH: int = 150
MAX_REWRITE_TOKENS: int = 120
REWRITE_TIMEOUT: int = 10

FACT_CHECK_MAX_CONTENT: int = 400
FACT_CHECK_TIMEOUT: int = 20
FACT_CHECK_REPLACEMENT: str = "（暂无明确证据支持）"

_SUSPICIOUS_WORDS: list = ["抱歉", "对不起", "我是", "您好", "请问", "你可以"]


# ═══════════════════════════════════════════════════════════════════
#  RAGService
# ═══════════════════════════════════════════════════════════════════
class RAGService:
    """Parent-Child + 多路混合召回 RAG 服务。

    数据结构：
        self.docs[i]   = 父块（parent），用于 LLM 生成时的证据
        self.embeddings = 父块的向量（numpy 降级矩阵，与 self.docs 下标一致）
        Milvus          = 子块向量（child embedding → parent_idx 映射回父块）
        BM25Index       = 父块全文（关键词搜索 parent_content → parent_idx）
    """

    def __init__(self):
        self.docs: List[dict] = []        # 父块列表
        self.embeddings: np.ndarray = np.zeros((0, EMBED_DIM), dtype=np.float32)

        self._milvus = MilvusClient()
        self._embedder = EmbeddingService()
        self._bm25_index = BM25Index()

        self.load()

    def __del__(self):
        pass

    # ═══════════════════════════════════════════════════════════
    #  知识库扫描（父子结构）
    # ═══════════════════════════════════════════════════════════
    def _scan_parent_child(self) -> Tuple[List[dict], List[dict]]:
        """扫描目录 → 按三种文档类型做父子切分。

        Returns:
            (parents, children)
            parents[i]  = {"source","domain","chunk_id","content","parent_id",...}
            children[j] = {"parent_idx","child_content","source","domain","chunk_id"}
        """
        parents: List[dict] = []
        children: List[dict] = []
        chunk_counter: Dict[str, int] = {}  # source → 递增 chunk_id

        folders = [
            ("knowledge_base", DATA_DIR / "knowledge_base"),
            ("recipes", DATA_DIR / "recipes"),
            ("food_database", DATA_DIR / "food_database"),
        ]
        for folder_name, folder in folders:
            if not folder.exists():
                logger.warning("知识库目录不存在: %s", folder)
                continue
            for path in sorted(folder.glob("*")):
                if path.is_dir() or path.name.startswith(".") or path.name in EXCLUDED_KNOWLEDGE_FILES:
                    continue

                source = path.name
                domain = infer_domain(path)
                chunk_counter.setdefault(source, 0)

                # ── MD / TXT：段落级父块 + 滑动子块 ──
                if path.suffix.lower() in {".md", ".txt"}:
                    raw_text = path.read_text(encoding="utf-8", errors="ignore")
                    pc_pairs = chunk_markdown(raw_text)
                    for parent_text, child_list in pc_pairs:
                        if not parent_text.strip():
                            continue
                        chunk_counter[source] += 1
                        chunk_id = chunk_counter[source]
                        parent_idx = len(parents)
                        parent = {
                            "source": source,
                            "domain": domain,
                            "chunk_id": chunk_id,
                            "parent_id": f"{source}#{chunk_id}",
                            "content": parent_text,  # 父块全文（→ LLM）
                        }
                        parents.append(parent)
                        for child_text in child_list:
                            children.append({
                                "parent_idx": parent_idx,
                                "child_content": child_text,
                                "source": source,
                                "domain": domain,
                                "chunk_id": chunk_id,
                            })

                # ── CSV：一行一父一子（等长） ──
                elif path.suffix.lower() == ".csv":
                    with path.open("r", encoding="utf-8-sig", newline="") as file:
                        reader = csv.DictReader(file)
                        for row in reader:
                            row_text = "；".join([f"{k}：{v}" for k, v in row.items() if v])
                            if not row_text.strip():
                                continue
                            parent_text, _ = chunk_csv_row(row_text)
                            chunk_counter[source] += 1
                            chunk_id = chunk_counter[source]
                            parent_idx = len(parents)
                            parent = {
                                "source": source,
                                "domain": domain,
                                "chunk_id": chunk_id,
                                "parent_id": f"{source}#{chunk_id}",
                                "content": parent_text,
                            }
                            parents.append(parent)
                            children.append({
                                "parent_idx": parent_idx,
                                "child_content": parent_text,  # CSV: child == parent
                                "source": source,
                                "domain": domain,
                                "chunk_id": chunk_id,
                            })
        logger.info(
            "知识库扫描完成: %d 个父块, %d 个子块。", len(parents), len(children))
        return parents, children

    # ═══════════════════════════════════════════════════════════
    #  加载
    # ═══════════════════════════════════════════════════════════
    def load(self, rebuild: bool = False) -> "RAGService":
        """扫描 → 父子切分 → Embedding → 写入 Milvus(子块) → 内存 BM25。
        """
        parents, children = self._scan_parent_child()
        if not parents:
            logger.warning("知识库扫描结果为空。")
            self.docs = []
            self.embeddings = np.zeros((0, EMBED_DIM), dtype=np.float32)
            self._bm25_index.build([])
            return self

        self.docs = parents
        parent_texts = [p["content"] for p in parents]
        child_texts = [c["child_content"] for c in children]
        n_parents = len(parents)
        n_children = len(children)

        # 重建判断：Milvus 存子块；本地 embeddings 存父块向量。
        need_rebuild = rebuild or self._need_rebuild(n_children, n_parents)

        if need_rebuild:
            logger.info("开始重建知识库索引（%d父块 → %d子块）…", n_parents, n_children)

            if OPENAI_API_KEY:
                # 父块 Embedding 用于 Milvus 不可用时的 numpy 向量检索。
                self.embeddings = self._embedder.batch_embed(parent_texts)
                if self._milvus.ok:
                    child_embeddings = self._embedder.batch_embed(child_texts)
                else:
                    child_embeddings = np.zeros((n_children, EMBED_DIM), dtype=np.float32)
            else:
                logger.warning("未配置 API Key，向量置为零向量，检索退化为纯 BM25。")
                self.embeddings = np.zeros((n_parents, EMBED_DIM), dtype=np.float32)
                child_embeddings = np.zeros((n_children, EMBED_DIM), dtype=np.float32)

            # Milvus：写入子块
            if self._milvus.ok and OPENAI_API_KEY:
                self._milvus.delete_all()
                self._milvus.insert_children(children, child_embeddings)
        else:
            logger.info("知识库索引已就绪（%d 父块, %d 子块），跳过重建。", n_parents, n_children)
            if self.embeddings is None or len(self.embeddings) != n_parents:
                self.embeddings = np.zeros((n_parents, EMBED_DIM), dtype=np.float32)

        # 降级 BM25 索引（父块全文）
        self._bm25_index.build(parent_texts)
        logger.info("知识库加载完成：%d 父块。", n_parents)
        return self

    def _need_rebuild(self, n_children: int, n_parents: int) -> bool:
        """检查外部存储数据量是否与实际一致。"""
        if self.embeddings is None or len(self.embeddings) != n_parents:
            return True
        if self._milvus.ok:
            try:
                if self._milvus.count != n_children:
                    return True
            except Exception:
                return True
        return False

    # ═══════════════════════════════════════════════════════════
    #  查询改写（不变）
    # ═══════════════════════════════════════════════════════════
    def _rewrite_query(self, question: str) -> str:
        rewritten = question
        if OPENAI_API_KEY:
            client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
            prompt = (
                "你是信息检索查询改写助手。将用户的自然语言问题改写成简洁、关键词明确、"
                "适合健康管理知识库检索的查询语句，优先使用以下领域术语："
                f"{'、'.join(DOMAIN_TERMS)}\n"
                "输出越短越好，只保留核心意图。\n"
                "只返回改写后的句子，不要解释、不要对话、不要多余内容。\n"
                f"用户问题：{question}\n"
                "改写后："
            )
            try:
                resp = client.chat.completions.create(
                    model=CHAT_MODEL, temperature=0, max_tokens=MAX_REWRITE_TOKENS,
                    timeout=REWRITE_TIMEOUT,
                    messages=[{"role": "user", "content": prompt}],
                )
                raw = (resp.choices[0].message.content or "").strip()
            except Exception as exc:
                logger.warning("查询改写调用失败: %s，使用原问题。", exc)
                raw = ""
            if raw and len(raw) <= max(MAX_REWRITE_LENGTH, len(question) * 2):
                if not any(w in raw for w in _SUSPICIOUS_WORDS) and "？" not in raw and "?" not in raw:
                    rewritten = raw
        combined_text = f"{question} {rewritten}".lower()
        boosted = [t for t in DOMAIN_TERMS if t not in rewritten and t.lower() in combined_text][:3]
        if boosted:
            rewritten = f"{rewritten} {' '.join(boosted)}"
        return rewritten

    # ═══════════════════════════════════════════════════════════
    #  多路混合召回（父子模式）
    # ═══════════════════════════════════════════════════════════
    def retrieve(self, question: str, top_k: int = 6) -> List[dict]:
        """多路召回：内存 BM25(parent) + 向量检索(child 或 parent) → 去重融合 → top_k。

        两路返回的都是 parent_idx，weighted_fuse 按 parent_idx 去重。
        降级时 self.embeddings 为父块向量，与 self.docs 下标一致。
        """
        n = len(self.docs)
        if n == 0:
            logger.warning("知识库为空，检索返回空。")
            return []

        query_emb = self._embedder.batch_embed([question])[0]

        # 内存 BM25 路（搜索父块全文 → 返回 parent_idx）
        bm25_scores = self._bm25_index.score(question)
        bm25_hits: List[Tuple[int, float]] = [
            (idx, score)
            for idx, score in sorted(
                enumerate(bm25_scores), key=lambda item: item[1], reverse=True
            )[:BM25_RECALL_K]
            if score > 0
        ]

        # Milvus 向量路（搜索子块向量 → 返回 parent_idx）
        if self._milvus.ok:
            mv_hits: List[Tuple[int, float]] = self._milvus.search(query_emb, top_k=MILVUS_RECALL_K)
        else:
            # 降级：父块向量全量检索（self.embeddings 与 self.docs 下标一致）
            mv_hits = vector_topk_numpy(query_emb, self.embeddings, top_k=MILVUS_RECALL_K)

        # 多路融合（按 parent_idx 去重）
        if bm25_hits or mv_hits:
            rows = weighted_fuse(bm25_hits, mv_hits, top_k=top_k)
            logger.debug("多路召回: BM25=%d, Vector=%d → 融合后=%d。", len(bm25_hits), len(mv_hits), len(rows))
        else:
            logger.warning("多路召回均失败，降级为全量融合。")
            vector_scores = vector_topk_numpy(query_emb, self.embeddings, top_k=len(self.docs))
            rows = full_fallback_fuse(bm25_scores, vector_scores, top_k=top_k)

        return [
            {"rank": rank, **self.docs[idx], "score": round(float(score), 4)}
            for rank, (idx, score) in enumerate(rows, 1)
        ]

    # ═══════════════════════════════════════════════════════════
    #  精排（不变）
    # ═══════════════════════════════════════════════════════════
    def rerank(self, question: str, candidates: List[dict], top_k: int = 3) -> List[dict]:
        """按混合检索粗排分数截断，不再调用外部 rerank API。"""
        if not candidates:
            return []
        ranked = sorted(candidates, key=lambda x: x.get("score", 0), reverse=True)[:top_k]
        for item in ranked:
            item["rerank_score"] = item.get("score", 0)
        return ranked

    # ═══════════════════════════════════════════════════════════
    #  事实溯源校验（不变）
    # ═══════════════════════════════════════════════════════════
    def _fact_check(self, answer: str, sources: List[dict]) -> dict:
        sentences = split_sentences(answer)
        if not sentences or not OPENAI_API_KEY:
            return {"verified_answer": answer, "suspicious_spans": [], "checked": False}

        evidence = "\n---\n".join([
            f"[E{i+1}] {s['content'][:FACT_CHECK_MAX_CONTENT]}" for i, s in enumerate(sources[:6])
        ])
        sentences_text = "\n".join([f"S{i+1}：{s}" for i, s in enumerate(sentences)])

        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        prompt = (
            "你是事实溯源校验助手。逐一检查回答中的每个句子是否有证据支持。\n"
            f"证据（来自知识库）：\n{evidence}\n\n"
            f"回答句子：\n{sentences_text}\n\n"
            "对每个句子判断：证据明确支持 → \"可信\"；证据中找不到或矛盾 → \"可疑\"。"
            "风险提示、通用建议、免责声明等安全兜底句子 → \"可信\"。\n"
            "只返回 JSON：{\"results\": [{\"index\": 1, \"verdict\": \"可信\"}, ...]}"
        )
        try:
            resp = client.chat.completions.create(
                model=CHAT_MODEL, temperature=0, max_tokens=512, timeout=FACT_CHECK_TIMEOUT,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = resp.choices[0].message.content or ""
            parsed = extract_json(raw) or {"results": []}
            verdicts = {r.get("index"): r.get("verdict") for r in parsed.get("results", [])}
        except Exception as exc:
            logger.warning("事实校验失败: %s，返回原回答。", exc)
            return {"verified_answer": answer, "suspicious_spans": [], "checked": False}

        verified_parts, suspicious_spans = [], []
        for i, sent in enumerate(sentences):
            v = verdicts.get(i + 1, "可信")
            if v == "可疑":
                suspicious_spans.append({"sentence_index": i + 1, "original": sent, "verdict": "可疑"})
                punct = sent[-1] if sent.endswith(("。", "！", "？", ".", "!", "?")) else ""
                verified_parts.append(f"{FACT_CHECK_REPLACEMENT}{punct}")
            else:
                verified_parts.append(sent)
        return {
            "verified_answer": "".join(verified_parts),
            "suspicious_spans": suspicious_spans,
            "checked": True,
        }

    # ═══════════════════════════════════════════════════════════
    #  回答生成（不变）
    # ═══════════════════════════════════════════════════════════
    def answer(self, question: str, top_k: int = 3, coarse_top_k: int = COARSE_TOP_K_DEFAULT) -> dict:
        """端到端 RAG 问答。

        Pipeline: 改写 → 多路召回(child→parent) → 精排 → 生成 → 事实校验
        """
        search_query = self._rewrite_query(question)
        sources = self.retrieve(search_query, top_k=coarse_top_k)
        sources = self.rerank(search_query, sources, top_k=top_k)
        context = "\n\n".join(
            [f"[{i}] {x['source']} | {x['domain']}\n{x['content']}" for i, x in enumerate(sources, 1)])

        if not OPENAI_API_KEY:
            answer = "模型生成暂不可用，先给出基于知识库证据的抽取式摘要：\n" + "\n".join(
                [f"- {x['content'][:160]}... [{i}]" for i, x in enumerate(sources[:4], 1)]
            )
            logger.warning("无 API Key，返回抽取式摘要。")
            return {
                "answer": answer, "sources": sources,
                "fact_check": {"suspicious_spans": [], "checked": False},
            }

        client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
        prompt = f"""你是健康管理平台的 AI 营养顾问。请基于证据回答，不做医疗诊断，不承诺治疗或快速瘦身。
回答结构：直接结论、执行建议、风险提示、引用来源。

用户问题：{question}

证据：
{context}
"""
        resp = client.chat.completions.create(
            model=CHAT_MODEL, temperature=0.2,
            messages=[{"role": "user", "content": prompt}],
        )
        answer: str = resp.choices[0].message.content or ""

        fact_check = self._fact_check(answer, sources)
        return {
            "answer": fact_check["verified_answer"],
            "sources": sources,
            "fact_check": {
                "suspicious_spans": fact_check["suspicious_spans"],
                "checked": fact_check["checked"],
            },
        }


_rag_service_instance: RAGService | None = None


def get_rag_service() -> RAGService:
    global _rag_service_instance
    if _rag_service_instance is None:
        _rag_service_instance = RAGService()
    return _rag_service_instance
