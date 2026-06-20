"""RAG 包：工业级多路混合召回。

使用方式:
    from app.services.rag import get_rag_service
    result = get_rag_service().answer("减脂期每天应该摄入多少热量？")
"""

from .rag_service import RAGService, get_rag_service

__all__ = ["RAGService", "get_rag_service"]
