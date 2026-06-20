import logging
import os
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parents[2]

load_dotenv(BASE_DIR / "backend" / ".env")
DATA_DIR = BASE_DIR / "data"
STORAGE_DIR = BASE_DIR / "backend" / "storage"
LOGS_DIR = STORAGE_DIR / "logs"
UPLOAD_DIR = STORAGE_DIR / "uploads"
RECORDS_FILE = STORAGE_DIR / "meal_records.json"
PROFILE_FILE = STORAGE_DIR / "user_profile.json"
USERS_FILE = STORAGE_DIR / "users.json"
CONFIG_DIR = BASE_DIR / "backend" / "config"
CONFIG_FILE = CONFIG_DIR / "config.yaml"
CONFIG_EXAMPLE_FILE = CONFIG_DIR / "config.example.yaml"
MEMORY_DIR = STORAGE_DIR / "memory"
SHORT_MEMORY_DIR = MEMORY_DIR / "short_term"
LONG_MEMORY_DIR = MEMORY_DIR / "long_term"
BODY_METRICS_FILE = LONG_MEMORY_DIR / "body_metrics.json"


def configure_root_logging() -> None:
    """配置全局日志：控制台 + 按天滚动的文件日志。

    日志文件命名：logs/app_2026_06_04.log
    根 Logger 设 INFO 级别，所有子模块自动继承。
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_filename = LOGS_DIR / f"app_{datetime.now().strftime('%Y_%m_%d')}.log"

    root = logging.getLogger()
    # 避免重复添加 handler
    if root.handlers:
        return

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 控制台 handler
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)
    root.addHandler(console)

    # 文件 handler（每天新建，保留 30 天）
    file_handler = logging.FileHandler(str(log_filename), encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)  # 文件记录 DEBUG 级别，比控制台更详细
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    root.setLevel(logging.DEBUG)
    root.info("日志系统初始化完成，文件: %s", log_filename)


configure_root_logging()


def deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_yaml(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as file:
        return yaml.safe_load(file) or {}


def load_settings() -> dict:
    settings = load_yaml(CONFIG_EXAMPLE_FILE)
    settings = deep_merge(settings, load_yaml(CONFIG_FILE))
    llm = settings.setdefault("llm", {})
    server = settings.setdefault("server", {})
    rag = settings.setdefault("rag", {})
    memory = settings.setdefault("memory", {})
    auth = settings.setdefault("auth", {})

    llm["api_key"] = os.getenv("OPENAI_API_KEY") or os.getenv("RAG_API_KEY") or llm.get("api_key", "")
    llm["base_url"] = os.getenv("OPENAI_BASE_URL") or os.getenv("RAG_BASE_URL") or llm.get("base_url", "https://www.dmxapi.cn/v1")
    llm["chat_model"] = os.getenv("NUTRIFIT_CHAT_MODEL") or llm.get("chat_model", "gpt-5-mini")
    llm["vision_model"] = os.getenv("NUTRIFIT_VISION_MODEL") or llm.get("vision_model", "gpt-5-mini")
    llm["embedding_model"] = os.getenv("NUTRIFIT_EMBEDDING_MODEL") or llm.get("embedding_model", "text-embedding-v3")
    server["host"] = os.getenv("NUTRIFIT_HOST") or server.get("host", "127.0.0.1")
    server["port"] = int(os.getenv("NUTRIFIT_PORT") or server.get("port", 8008))
    rag["top_k"] = int(rag.get("top_k", 6))
    auth["jwt_secret"] = os.getenv("NUTRIFIT_JWT_SECRET") or auth.get("jwt_secret", "dev-only-change-me")
    milvus = settings.setdefault("milvus", {})
    milvus["host"] = os.getenv("MILVUS_HOST") or milvus.get("host", "localhost")
    milvus["port"] = int(os.getenv("MILVUS_PORT") or milvus.get("port", 19530))
    milvus["collection"] = os.getenv("MILVUS_COLLECTION") or milvus.get("collection", "nutrifit_knowledge")
    memory["short_term_max_turns"] = int(memory.get("short_term_max_turns", 12))
    return settings


SETTINGS = load_settings()
OPENAI_API_KEY = SETTINGS["llm"].get("api_key", "")
OPENAI_BASE_URL = SETTINGS["llm"].get("base_url", "https://www.dmxapi.cn/v1")
CHAT_MODEL = SETTINGS["llm"].get("chat_model", "gpt-5-mini")
VISION_MODEL = SETTINGS["llm"].get("vision_model", "gpt-5-mini")
EMBEDDING_MODEL = SETTINGS["llm"].get("embedding_model", "text-embedding-v3")
MILVUS_HOST = SETTINGS["milvus"].get("host", "localhost")
MILVUS_PORT = int(SETTINGS["milvus"].get("port", 19530))
MILVUS_COLLECTION = SETTINGS["milvus"].get("collection", "nutrifit_knowledge")
LLM_TEMPERATURE = float(SETTINGS["llm"].get("temperature", 0.2))
LLM_TIMEOUT = float(SETTINGS["llm"].get("timeout", 60))
SHORT_TERM_MAX_TURNS = int(SETTINGS["memory"].get("short_term_max_turns", 12))
JWT_SECRET = SETTINGS["auth"].get("jwt_secret", "dev-only-change-me")

UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
STORAGE_DIR.mkdir(parents=True, exist_ok=True)
SHORT_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
LONG_MEMORY_DIR.mkdir(parents=True, exist_ok=True)
