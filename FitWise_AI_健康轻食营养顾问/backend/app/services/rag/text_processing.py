"""文本预处理工具：分词、父子块切分、领域推断、句子拆分。

Parent-Child Chunking 策略（三种文档分别处理）：
  - MD 知识库:  段落 → parent(≤512 chars, 句边界保护) → children(160 chars, 40 overlap)
  - CSV 食物库: 一行 = parent = child（不切分）
  - CSV 食谱库: 一行 = parent = child（不切分）
"""

import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 父子块切分常量 ─────────────────────────────────────────────────
MD_PARENT_MAX: int = 512          # MD parent 最大字符数
MD_CHILD_SIZE: int = 160          # MD child 窗口大小
MD_CHILD_OVERLAP: int = 40        # MD child 重叠字符数

# 降级向量常量（兼容旧接口）
CHUNK_SIZE: int = 520
CHUNK_OVERLAP: int = 90

DOMAIN_TERMS: list = [
    "热量缺口", "基础代谢", "总消耗", "体脂率", "蛋白质",
    "力量训练", "有氧运动", "控糖", "轻食", "膳食纤维",
    "饱腹感", "BMI", "BMR", "TDEE", "kcal",
]

_PUNCTUATION_FILTER: set = {
    "，", "。", "！", "？", "；", "：", "“", "”", "（", "）", "、", " ", "\t", "\n",
}

# 句子边界标点（用于保护语义完整性）
_SENTENCE_BOUNDARY = re.compile(r"[。！？.!?\n]")

# ── jieba 可选导入 ──────────────────────────────────────────────────
try:
    import jieba
    _JIEBA_OK = True
    for term in DOMAIN_TERMS:
        jieba.add_word(term)
except ModuleNotFoundError:
    jieba = None  # type: ignore
    _JIEBA_OK = False
    logger.warning("jieba 未安装，中文分词退回到正则模式。")


def normalize_text(text: str) -> str:
    """规范化空白字符：连续空白 → 单个空格，去首尾空白。"""
    return re.sub(r"\s+", " ", text or "").strip()


def tokenize(text: str) -> list:
    """中文用 jieba 分词，未安装时退回正则；为 BM25 检索服务。"""
    text_lower = (text or "").lower()
    if _JIEBA_OK and jieba is not None:
        tokens = jieba.lcut(text_lower)
    else:
        tokens = re.findall(r"[一-鿿]|[a-zA-Z0-9]+", text_lower)
    return [
        token.strip() for token in tokens
        if token.strip() and token.strip() not in _PUNCTUATION_FILTER
    ]


def infer_domain(path: Path) -> str:
    """从文件路径推断知识领域分类。"""
    name = path.name
    if "体重" in name or "减脂" in name:
        return "体重管理"
    if "食谱" in name:
        return "食谱推荐"
    if "运动" in name:
        return "运动建议"
    if "睡眠" in name:
        return "生活方式"
    if "客服" in name or "合规" in name:
        return "客服合规"
    if path.parent.name == "food_database":
        return "食物营养库"
    return "健康知识"


def split_sentences(text: str) -> list:
    """按中英文句末标点拆分句子，保留标点。
    例如："我来了！今天天气真好。你好吗？" -> ["我来了！", "今天天气真好。", "你好吗？"]
    """
    parts = re.split(r"(?<=[。！？.!?])", text or "")
    return [s.strip() for s in parts if s.strip()]


# ═══════════════════════════════════════════════════════════════════
#  MD 知识库 — 父子块切分
# ═══════════════════════════════════════════════════════════════════
def _split_into_paragraphs(text: str) -> list:
    """将 MD 文本拆分为段落：以空行或 markdown 标题为界。"""
    # 先按空行拆
    raw = re.split(r"\n\s*\n", text or "")
    paragraphs = []
    for block in raw:
        block = block.strip()
        if not block:
            continue
        # 如果 block 内包含 markdown 标题，按标题再拆
        sub = re.split(r"(?=\n#{1,4}\s)", block)
        # 只保留有实际意义的正文段落，纯标题、空行全部扔掉
        for s in sub:
            s = s.strip()
            if s and not s.startswith("#"):  # 跳过纯标题行本身
                paragraphs.append(s)
            elif s and len(s) > 5:  # 带内容的标题行保留
                paragraphs.append(s)
    return [p for p in paragraphs if p]


def _split_long_paragraph(text: str, max_len: int = MD_PARENT_MAX) -> list:
    """将超长段落按句子边界切分为不超过 max_len 的子段。"""
    if len(text) <= max_len:
        return [text]

    parts = []
    sentences = re.split(r"(?<=[。！？.!?])", text)
    current = ""
    for sent in sentences:
        sent = sent.strip()
        if not sent:
            continue
        if len(current) + len(sent) <= max_len:
            current += sent
        else:
            if current:
                parts.append(current)
            # 如果单个句子超过 max_len，强制切分
            if len(sent) > max_len:
                parts.extend(_force_split_long_sentence(sent, max_len))
                current = ""
            else:
                current = sent
    if current:
        parts.append(current)
    return parts or [text]


def _force_split_long_sentence(text: str, chunk_size: int) -> list:
    """对超长句子的兜底切分（按字符滑动窗口，保护句子边界）。"""
    chunks = []
    start = 0
    n = len(text)
    while start < n:
        end = min(start + chunk_size, n)
        chunk = text[start:end]
        chunks.append(chunk)
        if end >= n:
            break
        # 尽量在标点处断开
        # 当 chunk size小于41时，触发 start + 1，避免死循环
        overlap_start = max(end - 40, start + 1)
        start = overlap_start
    return chunks


def _sliding_children(parent_text: str, child_size: int = MD_CHILD_SIZE,
                      overlap: int = MD_CHILD_OVERLAP) -> list:
    """从 parent 文本中滑动生成 child 片段。"""
    text = normalize_text(parent_text)
    n = len(text)
    if n <= child_size:
        return [text]

    children = []
    start = 0
    while start < n:
        end = min(start + child_size, n)
        child = text[start:end]
        children.append(child)
        if end >= n:
            break
        start = max(end - overlap, start + 1)
    return children


def chunk_markdown(text: str) -> list:
    """MD 知识库父子切分。

    Returns:
        [(parent_text, [child1, child2, ...]), ...]
    """
    paragraphs = _split_into_paragraphs(text)
    if not paragraphs:
        return []

    result = []
    for para in paragraphs:
        para = normalize_text(para)
        if not para:
            continue
        sub_paras = _split_long_paragraph(para, MD_PARENT_MAX)
        for sub in sub_paras:
            if not sub.strip():
                continue
            children = _sliding_children(sub, MD_CHILD_SIZE, MD_CHILD_OVERLAP)
            result.append((sub, children))
    return result


# ═══════════════════════════════════════════════════════════════════
#  CSV — 父子等长（不切分）
# ═══════════════════════════════════════════════════════════════════
def chunk_csv_row(text: str) -> tuple:
    """CSV 食物库/食谱库：一行 = parent = child。

    Returns:
        (parent_text, [child_text])  — child == parent
    """
    text = normalize_text(text)
    return (text, [text])


# ═══════════════════════════════════════════════════════════════════
#  兼容旧接口：固定大小切分（无 parent-child 时降级用）
# ═══════════════════════════════════════════════════════════════════
def chunk_text(text: str, chunk_size: int = CHUNK_SIZE,
               overlap: int = CHUNK_OVERLAP) -> list:
    """固定字符长度 + 重叠滑动窗口切分文本（兼容旧版接口）。"""
    text = normalize_text(text)
    if not text:
        return []
    chunks = []
    start = 0
    text_len = len(text)
    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunks.append(text[start:end])
        if end >= text_len:
            break
        start = max(end - overlap, start + 1)
    return chunks
