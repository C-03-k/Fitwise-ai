import csv

from fastapi import Depends, FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.config import DATA_DIR, SETTINGS, STORAGE_DIR
from app.schemas import AgentChatRequest, BodyMetricRecord, ChatRequest, FoodLogRequest, HealthProfile, LoginRequest
from app.services.auth import create_default_user, create_token, find_user, get_current_user, verify_password
from app.services.body_metrics import add_body_metric, clear_body_metrics, list_body_metrics, trend_summary
from app.services.food import add_user_records, clear_records, daily_summary, estimate_item, list_records, load_foods
from app.services.health import calculate_profile
from app.services.memory import clear_user_sessions, get_recent_context, get_user_recent_context, list_sessions, new_session_id
from app.services.profile import clear_profile, get_profile, save_profile
from app.services.rag.text_processing import chunk_markdown, infer_domain
from app.services.vision import recognize_food_image


app = FastAPI(title="NutriFit AI API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=SETTINGS.get("server", {}).get("cors_origins", ["*"]) or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/storage", StaticFiles(directory=str(STORAGE_DIR)), name="storage")


@app.on_event("startup")
def startup_auth():
    create_default_user()


def _knowledge_stats_light():
    domains = {}
    chunks = 0
    folders = [
        DATA_DIR / "knowledge_base",
        DATA_DIR / "recipes",
        DATA_DIR / "food_database",
    ]
    for folder in folders:
        if not folder.exists():
            continue
        for path in sorted(folder.glob("*")):
            if path.is_dir() or path.name.startswith("."):
                continue
            domain = infer_domain(path)
            count = 0
            if path.suffix.lower() in {".md", ".txt"}:
                text = path.read_text(encoding="utf-8", errors="ignore")
                count = len(chunk_markdown(text))
            elif path.suffix.lower() == ".csv":
                with path.open("r", encoding="utf-8-sig", newline="") as file:
                    count = sum(1 for row in csv.DictReader(file) if any(row.values()))
            if count:
                chunks += count
                domains[domain] = domains.get(domain, 0) + count
    return {"chunks": chunks, "domains": domains}


@app.post("/api/auth/login")
def auth_login(req: LoginRequest):
    user = find_user(req.username)
    if user is None or not verify_password(req.password, user.get("password_hash", "")):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    return {
        "access_token": create_token(user["id"], user["username"]),
        "token_type": "bearer",
        "user_id": user["id"],
        "username": user["username"],
    }


@app.get("/api/auth/me")
def auth_me(current_user: dict = Depends(get_current_user)):
    return current_user


def _dashboard_summary(user_id: str):
    profile = get_profile(user_id)
    health = calculate_profile(HealthProfile(**profile)) if profile else calculate_profile(HealthProfile())
    records = daily_summary(user_id)
    knowledge = _knowledge_stats_light()
    return {
        "knowledge_files": 6,
        "food_items": len(load_foods()),
        "knowledge_chunks": knowledge["chunks"],
        "profile": profile,
        "health": health,
        "today": records,
        "memory": trend_summary(user_id),
    }


@app.get("/api/dashboard/summary")
def dashboard_summary(current_user: dict = Depends(get_current_user)):
    return _dashboard_summary(current_user["user_id"])


@app.post("/api/profile/calculate")
def profile_calculate(profile: HealthProfile, current_user: dict = Depends(get_current_user)):
    save_profile(current_user["user_id"], profile)
    return calculate_profile(profile)


@app.get("/api/food/search")
def food_search(q: str = ""):
    foods = load_foods()
    if q:
        foods = [x for x in foods if q in x["food_name"] or q.lower() in x["food_name"].lower()]
    return foods[:30]


@app.post("/api/food/log")
def food_log(req: FoodLogRequest, current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    records = [estimate_item(item, source="manual") for item in req.items]
    for record in records:
        record.note = req.note
    add_user_records(records, user_id)
    return {"records": [x.model_dump() for x in records], "summary": daily_summary(user_id), "total_records": len(list_records(user_id))}


@app.get("/api/food/records")
def food_records(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    return {"records": list_records(user_id), "summary": daily_summary(user_id)}


@app.post("/api/food/recognize")
async def food_recognize(
    file: UploadFile = File(...),
    meal_type: str = Form("拍照记录"),
    current_user: dict = Depends(get_current_user),
):
    return await recognize_food_image(file, meal_type, current_user["user_id"])


@app.post("/api/rag/retrieve")
def rag_retrieve(req: ChatRequest):
    from app.services.rag import get_rag_service
    rag_service = get_rag_service()
    return {"sources": rag_service.retrieve(req.question, req.top_k)}


@app.post("/api/rag/chat")
def rag_chat(req: ChatRequest):
    from app.services.rag import get_rag_service
    rag_service = get_rag_service()
    return rag_service.answer(req.question, req.top_k)


@app.post("/api/agent/chat")
def agent_chat(req: AgentChatRequest, current_user: dict = Depends(get_current_user)):
    from app.services.agent import agent
    req = req.model_copy(update={"user_id": current_user["user_id"]})
    return agent.chat(req)


@app.post("/api/agent/chat/stream")
def agent_chat_stream(req: AgentChatRequest, current_user: dict = Depends(get_current_user)):
    from app.services.agent import agent
    req = req.model_copy(update={"user_id": current_user["user_id"]})
    return StreamingResponse(
        agent.stream_chat(req),
        media_type="application/x-ndjson",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/chat/session")
def create_chat_session(current_user: dict = Depends(get_current_user)):
    session_id = new_session_id()
    user_id = current_user["user_id"]
    return {"session_id": session_id, "history": get_recent_context(session_id, user_id)}


@app.get("/api/chat/sessions")
def chat_sessions(current_user: dict = Depends(get_current_user)):
    return {"sessions": list_sessions(current_user["user_id"])}


@app.get("/api/chat/history/{session_id}")
def chat_history(session_id: str, current_user: dict = Depends(get_current_user)):
    return get_recent_context(session_id, current_user["user_id"])


@app.get("/api/chat/user-context")
def chat_user_context(current_user: dict = Depends(get_current_user)):
    return get_user_recent_context(current_user["user_id"])


@app.post("/api/memory/body-metrics")
def save_body_metric(record: BodyMetricRecord, current_user: dict = Depends(get_current_user)):
    record = record.model_copy(update={"user_id": current_user["user_id"]})
    saved = add_body_metric(record)
    return {"record": saved, "trend": trend_summary(record.user_id)}


@app.get("/api/memory/body-metrics")
def get_body_metrics(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    return {"records": list_body_metrics(user_id), "trend": trend_summary(user_id)}


@app.get("/api/memory/user-context")
def get_user_memory_context(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    return {
        "user_id": user_id,
        "short_term": get_user_recent_context(user_id),
        "long_term_body": trend_summary(user_id),
    }


@app.get("/api/config/public")
def public_config():
    llm = SETTINGS.get("llm", {})
    return {
        "base_url": llm.get("base_url"),
        "chat_model": llm.get("chat_model"),
        "vision_model": llm.get("vision_model"),
        "has_api_key": bool(llm.get("api_key")),
        "features": SETTINGS.get("features", {}),
    }


@app.post("/api/admin/clear-food-records")
def clear_food_records(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    removed_records = clear_records(user_id)
    return {
        "message": "当前用户热量记录已清空",
        "cleared": {
            "meal_records": removed_records,
            "uploaded_images": 0,
        },
        "summary": daily_summary(user_id),
    }


@app.post("/api/admin/clear-all-data")
def clear_all_data(current_user: dict = Depends(get_current_user)):
    user_id = current_user["user_id"]
    removed_records = clear_records(user_id)
    removed_metrics = clear_body_metrics(user_id)
    removed_profile = clear_profile(user_id)
    removed_sessions = clear_user_sessions(user_id)
    return {
        "message": "当前用户运行态数据已清空",
        "cleared": {
            "meal_records": removed_records,
            "body_metrics": removed_metrics,
            "user_profile": removed_profile,
            "chat_sessions": removed_sessions,
            "uploaded_images": 0,
        },
        "summary": _dashboard_summary(user_id),
    }


@app.get("/api/knowledge/stats")
def knowledge_stats():
    return _knowledge_stats_light()


@app.get("/")
def root():
    return {"name": "NutriFit AI", "docs": "/docs"}
