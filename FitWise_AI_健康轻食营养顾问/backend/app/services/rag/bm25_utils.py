"""内存 BM25 实现：构建索引、计算分数，用于关键词检索。"""

import math
import logging
from collections import Counter, defaultdict

from .text_processing import tokenize

logger = logging.getLogger(__name__)

# BM25 参数常量
BM25_K1: float = 1.5       # 词频饱和度参数
BM25_B: float = 0.75       # 文档长度归一化参数


class BM25Index:
    """内存 BM25 倒排索引。

    用法:
        bm25 = BM25Index()
        bm25.build(texts)
        scores = bm25.score(query)
    """

    def __init__(self):
        self.doc_tokens: list = []
        self.idf: dict = {}
        self.avgdl: float = 1.0
        self._doc_count: int = 0

    def build(self, texts: list) -> None:
        """构建 BM25 索引：分词 → 文档频率 → IDF → 平均文档长度。

        Args:
            texts: 文档内容列表
        """
        if not texts:
            logger.warning("BM25 build: texts 为空，跳过索引构建。")
            self.doc_tokens = []
            self.idf = {}
            self.avgdl = 1.0
            self._doc_count = 0
            return

        self.doc_tokens = [tokenize(text) for text in texts]
        self._doc_count = len(texts)

        # 文档频率（DF）：每个词出现在多少个文档中
        df = defaultdict(int)
        for tokens in self.doc_tokens:
            for token in set(tokens):
                df[token] += 1

        # IDF：BM25 经典平滑公式
        total = max(self._doc_count, 1)
        self.idf = {
            token: math.log(1 + (total - count + 0.5) / (count + 0.5))
            for token, count in df.items()
        }

        # 平均文档长度
        self.avgdl = sum(len(tokens) for tokens in self.doc_tokens) / total
        logger.info(
            "BM25 索引构建完成：%d 篇文档，avgdl=%.1f，词汇量=%d。",
            self._doc_count, self.avgdl, len(self.idf),
        )

    def score(self, query: str) -> list:
        """计算查询与所有文档的 BM25 分数。

        Args:
            query: 查询文本

        Returns:
            list[float]: 每个文档的 BM25 分数
        """
        if not self.doc_tokens:
            return []

        q_tokens = tokenize(query)
        if not q_tokens:
            return [0.0] * len(self.doc_tokens)

        scores = []
        for tokens in self.doc_tokens:
            tf = Counter(tokens)
            doc_len = max(len(tokens), 1)
            score = 0.0
            for token in q_tokens:
                if token not in tf:
                    continue
                score += self.idf.get(token, 0.0) * (tf[token] * (BM25_K1 + 1)) / (
                    tf[token] + BM25_K1 * (1 - BM25_B + BM25_B * doc_len / max(self.avgdl, 1))
                )
            scores.append(score)
        return scores
