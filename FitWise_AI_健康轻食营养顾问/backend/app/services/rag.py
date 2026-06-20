"""向后兼容入口：所有逻辑已迁移至 rag/ 包。"""

from app.services.rag.rag_service import get_rag_service  # noqa: F401
