# NutriFit AI 模块功能参考手册

## 一、项目架构总览

```
用户请求 → FastAPI (main.py)
              │
              ├─ /api/agent/chat ──→ NutriFitAgent (agent.py)   ← 核心对话 Agent
              ├─ /api/rag/chat   ──→ RAGService (rag/)           ← RAG 问答
              ├─ /api/food/*     ──→ food.py                     ← 饮食记录
              ├─ /api/memory/*   ──→ body_metrics.py             ← 身体指标
              ├─ /api/profile/*  ──→ health.py                   ← 健康计算
              ├─ /api/chat/*     ──→ memory.py                   ← 会话管理
              └─ /api/food/recognize → vision.py                 ← 视觉识别
```

---

## 二、配置层

### `config.py`

应用启动时第一个加载的模块，负责：

1. **环境加载**：`load_dotenv()` 加载 `.env` 文件
2. **YAML 合并**：`config.example.yaml`（默认）→ `config.yaml`（覆盖）→ 环境变量（最终覆盖）
3. **日志初始化**：`configure_root_logging()` 配置根 Logger，双输出：
   - 控制台（INFO 级别）
   - 文件 `logs/app_YYYY_MM_DD.log`（DEBUG 级别）
4. **目录创建**：`storage/`、`uploads/`、`memory/short_term/`、`memory/long_term/`、`logs/` 自动创建

**导出常量**（供全项目引用）：

| 类别 | 常量 | 说明 |
|------|------|------|
| LLM | `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `CHAT_MODEL`, `VISION_MODEL`, `LLM_TEMPERATURE` | API 配置 |
| 检索 | `RERANK_MODEL`, `EMBEDDING_MODEL` | 精排和向量化模型 |
| Milvus | `MILVUS_HOST`, `MILVUS_PORT`, `MILVUS_COLLECTION` | 向量数据库 |
| ES | `ES_HOST`, `ES_PORT`, `ES_INDEX` | 全文检索引擎 |
| 路径 | `DATA_DIR`, `STORAGE_DIR`, `UPLOAD_DIR`, `RECORDS_FILE`, `PROFILE_FILE`, `BODY_METRICS_FILE` | 数据和存储路径 |
| 内存 | `SHORT_TERM_MAX_TURNS` | 会话轮次上限 |

---

## 三、API 层

### `main.py`

FastAPI 应用入口，定义所有 REST 端点。

| 端点 | 方法 | 处理函数 | 功能 |
|------|------|---------|------|
| `/api/dashboard/summary` | GET | `dashboard_summary` | 首页仪表盘聚合统计 |
| `/api/profile/calculate` | POST | `calculate_health` | 保存身体档案并计算 BMI/BMR/TDEE |
| `/api/food/search` | GET | `search_food` | 按名称搜索食物库 |
| `/api/food/log` | POST | `log_food` | 手动记录饮食 |
| `/api/food/records` | GET | `get_food_records` | 查询今日饮食记录和汇总 |
| `/api/food/recognize` | POST | `recognize_food` | 上传食物图片 → AI 识别 |
| `/api/rag/retrieve` | POST | `rag_retrieve` | 仅检索知识库证据（不生成回答） |
| `/api/rag/chat` | POST | `rag_chat` | RAG 端到端问答 |
| `/api/agent/chat` | POST | `agent_chat` | **核心 Agent 对话**（完整管道） |
| `/api/agent/chat/stream` | POST | `agent_chat_stream` | Agent 流式对话（NDJSON） |
| `/api/chat/session` | POST | `create_session` | 创建新会话 |
| `/api/chat/sessions` | GET | `get_sessions` | 列出历史会话 |
| `/api/chat/history/{session_id}` | GET | `get_history` | 查看会话历史 |
| `/api/memory/body-metrics` | POST | `record_body_metric` | 记录身体指标 |
| `/api/memory/body-metrics` | GET | `get_body_metrics` | 查询身体指标趋势 |
| `/api/config/public` | GET | `public_config` | 暴露前端所需的公开配置 |
| `/api/admin/clear-food-records` | POST | `clear_food_records` | 清空饮食记录 |
| `/api/admin/clear-all-data` | POST | `clear_all_data` | 重置所有数据 |
| `/api/knowledge/stats` | GET | `knowledge_stats` | 知识库统计（按领域） |
| `/` | GET | `root` | 健康检查 |

---

## 四、数据模型层

### `schemas.py`

Pydantic 数据模型定义。

| 模型 | 用途 | 关键字段 |
|------|------|---------|
| `HealthProfile` | 用户身体档案 | gender, age, height_cm, weight_kg, target_weight_kg, activity_level, goal |
| `ChatRequest` | RAG 问答请求 | question, top_k |
| `FoodLogItem` | 单条饮食项 | food_name, grams, meal_type |
| `FoodLogRequest` | 饮食记录请求 | items: List[FoodLogItem], note |
| `MealRecord` | 完整饮食记录 | id, food_name, grams, calories, protein, fat, carbs, confidence, source |
| `BodyMetricRecord` | 身体指标记录 | weight_kg, waist_cm, body_fat_percent, sleep_hours, steps, exercise_minutes, date |
| `AgentChatRequest` | Agent 对话请求 | message, session_id, user_id, use_memory, use_rag, top_k |
| `AgentChatResponse` | Agent 对话响应 | session_id, answer, intent, tools_used, sources, memory_used, agent_trace |

---

## 五、服务层 — RAG 检索增强生成包

```
services/rag/
├── __init__.py           # 包入口，导出 RAGService 和 rag_service 单例
├── text_processing.py    # 文本预处理：分词、父子块切分、领域推断、句子拆分
├── bm25_utils.py         # 内存 BM25 索引（ES 降级方案）
├── vector_store.py       # 存储层：Milvus 客户端 + ES 客户端 + Embedding 服务
├── utils.py              # 通用工具：JSON 提取、分数归一化、多路融合、日志
└── rag_service.py        # 核心编排：检索 → 精排 → 生成 → 事实校验
```

### `text_processing.py` — 文本预处理

| 函数 | 输入 | 输出 | 功能 |
|------|------|------|------|
| `normalize_text` | 任意文本 | 规范化文本 | 连续空白→单空格，去首尾空白 |
| `tokenize` | 任意文本 | `List[str]` | jieba 中文分词（降级正则），过滤标点，为 BM25 服务 |
| `chunk_markdown` | MD 原文 | `[(parent, [child,...]), ...]` | **MD 父子切分**：段落拆分→句边界保护→滑动窗口子块 |
| `chunk_csv_row` | CSV 行文本 | `(parent, [child])` | **CSV 等长切分**：parent = child，不切分 |
| `infer_domain` | `Path` | `str` | 从文件名/目录推断领域标签 |
| `split_sentences` | 文本 | `List[str]` | 按句末标点拆分为句子列表 |

**三种文档切分策略**：

| 文档类型 | parent 策略 | child 策略 |
|---------|-----------|-----------|
| MD 知识库 | 按空行/标题拆段落 → ≤512 chars → 超长按句边界保护 | 滑动窗口 160 chars + 40 overlap |
| CSV 食物库 | 一行 = 一个 parent | child = parent（等长） |
| CSV 食谱库 | 一行 = 一个 parent | child = parent（等长） |

**领域标签映射**：

| 文件名关键词 | 领域 |
|-------------|------|
| 体重 / 减脂 | 体重管理 |
| 食谱 | 食谱推荐 |
| 运动 | 运动建议 |
| 睡眠 | 生活方式 |
| 客服 / 合规 | 客服合规 |
| 父目录 = food_database | 食物营养库 |
| 其他 | 健康知识 |

### `bm25_utils.py` — 内存 BM25 索引

**`BM25Index` 类**：纯 Python BM25 全文检索引擎，Elasticsearch 不可用时的降级方案。

| 方法 | 功能 |
|------|------|
| `build(texts)` | 分词→文档频率→IDF（平滑公式）→平均文档长度 |
| `score(query)` | 对所有文档计算 BM25 分数（k1=1.5, b=0.75） |

### `vector_store.py` — 存储层

**`EmbeddingService` 类**：

| 方法 | 功能 |
|------|------|
| `batch_embed(texts)` | 批量调用 Embedding API，返回 `(N, 1024)` numpy 数组。失败槽位填零向量，无 API Key 时全零 |

**`MilvusClient` 类**：Milvus 向量数据库客户端。

| 方法 | 功能 |
|------|------|
| `insert_children(children, embeddings)` | 批量写入子块向量（含 parent_idx 映射） |
| `search(query_emb, top_k)` | ANN 向量检索，返回 `[(parent_idx, cos_score), ...]` |
| `delete_all()` | 清空 Collection |
| `count` | 实体数量 |

Schema：`parent_idx` + `child_content` + `source` + `domain` + `chunk_id` + `embedding(1024-dim, COSINE, IVF_FLAT)`

**`ESClient` 类**：Elasticsearch BM25 检索客户端。

| 方法 | 功能 |
|------|------|
| `index_parents(parents)` | 批量索引父块全文 |
| `search(query, top_k)` | BM25 全文检索，返回 `[(parent_idx, bm25_score), ...]` |
| `delete_index()` | 删除并重建索引 |

Mapping：`parent_idx(integer)` + `content(text)` + `source(keyword)` + `domain(keyword)` + `chunk_id(integer)`

**降级函数**：

| 函数 | 功能 |
|------|------|
| `vector_topk_numpy(query_emb, doc_embeddings, top_k)` | numpy 全量余弦相似度 + top-k |

### `utils.py` — 通用工具

| 函数 | 功能 |
|------|------|
| `extract_json(text)` | 从文本中提取首个 JSON 对象（容忍 markdown/额外文本） |
| `normalize_scores(scores, max_val)` | 分数归一化到 [0, 1] |
| `weighted_fuse(es_hits, mv_hits, top_k)` | 多路召回去重合并 + 65%向量/35%BM25 加权融合 |
| `full_fallback_fuse(bm25_scores, vector_scores, top_k)` | 全量融合降级（ES+Milvus 均不可用时） |
| `setup_logging(name)` | 获取模块 Logger（根 Logger 已在 config 配置） |

常量：`ES_RECALL_K=10`、`MILVUS_RECALL_K=10`、`VECTOR_WEIGHT=0.65`、`BM25_WEIGHT=0.35`

### `rag_service.py` — 核心编排

**`RAGService` 类**：Parent-Child + 多路召回 + 精排 + 事实校验 的完整 RAG 管道。

| 方法 | 功能 | 流程 |
|------|------|------|
| `_scan_parent_child()` | 扫描知识库目录 → 三种文档分别做父子切分 | → `(parents, children)` |
| `load(rebuild)` | 父子 Embedding → Milvus(子块) + ES(父块) + BM25 降级索引 | 启动时调用 |
| `_rewrite_query(question)` | LLM 改写 + 领域词增强 | 口语→检索关键词 |
| `retrieve(question, top_k)` | ES BM25 + Milvus ANN → 按 parent_idx 去重融合 | 多路召回 |
| `rerank(question, candidates, top_k)` | Rerank 模型交叉编码重排序 | 粗排→精排 |
| `_fact_check(answer, sources)` | 逐句溯源校验，可疑句标记替换 | 防幻觉 |
| `answer(question, top_k, coarse_top_k)` | **端到端管道入口** | 改写→召回→精排→生成→校验 |

**完整数据流**：

```
用户问题
  │
  ├─ _rewrite_query()          口语改写 + 领域词增强
  │
  ├─ retrieve()                多路混合召回
  │   ├─ ES.search(parent_content)  ──→ [(parent_idx, bm25), ...]
  │   ├─ Milvus.search(child_emb)   ──→ [(parent_idx, cos), ...]
  │   └─ weighted_fuse()            ──→ top_k 粗排结果
  │
  ├─ rerank()                  qwen3-rerank 交叉编码精排
  │
  ├─ LLM 生成                   基于精排后的 parent 证据
  │
  └─ _fact_check()             逐句溯源 → 可疑句替换
```

**四级降级链**：

| ES | Milvus | 向量检索 | BM25 检索 |
|----|--------|---------|----------|
| ✅ | ✅ | Milvus child ANN | ES parent BM25 |
| ✅ | ❌ | numpy parent 全量 | ES parent BM25 |
| ❌ | ✅ | Milvus child ANN | 内存 parent BM25 |
| ❌ | ❌ | numpy parent 全量 | 内存 parent BM25 |

---

## 六、服务层 — NutriFitAgent

### `agent.py` — 核心对话 Agent

**`NutriFitAgent` 类**：Plan-and-Solve + ReAct + Reflection 架构的 AI 营养顾问。

**完整处理管道**（`chat(request)` 方法）：

```
用户消息
  │
  ├─ ① classify_intent(message)                           关键词+数字 → 8 种意图
  │
  ├─ ② plan(intent)                                      生成可追踪执行计划
  │
  ├─ ③ run_tools(intent, request, session_id)            ReAct 工具执行
  │   ├─ 必读：短期记忆 + 长期身体趋势 + 身体档案 + 今日饮食
  │   ├─ 按需写：body_metric_recording → parse + add_body_metric
  │   ├─ 按需写：meal_logging → parse + estimate + add_records
  │   └─ 按需搜：use_rag → rag_service.retrieve()
  │
  ├─ ④ generate_answer(request, intent, sources, tool_state)   拼 prompt → LLM 生成
  │
  ├─ ⑤ reflect(answer, intent)                                合规复核
  │   ├─ 禁用词替换（"治疗"→"辅助健康管理"）
  │   └─ 风险提示补全
  │
  ├─ ⑥ save_turn(session_id, ...)                             写入短期记忆
  │
  └─ ⑦ return AgentChatResponse                               返回完整响应
```

| 方法 | 功能 |
|------|------|
| `classify_intent(message)` | 关键词+数字判定 8 种意图之一 |
| `plan(intent)` | 根据意图生成执行步骤（agent_trace） |
| `parse_food_items(message)` | 正则提取食物名+克数（最多 6 项） |
| `load_profile_context()` | 读取身体档案 + calculate_profile 计算结果 |
| `load_food_context()` | 读取今日饮食汇总 + 最近 8 条记录 |
| `run_tools(intent, request, session_id)` | 执行 4 必读 + 2 按需写 + 1 按需搜 |
| `build_prompt(...)` | 拼接完整上下文的 LLM prompt |
| `generate_answer(...)` | 调 OpenAI API（或降级抽取式摘要） |
| `reflect(answer, intent)` | 合规词过滤 + 风险提示补充 |
| `chat(request)` | 非流式完整管道入口 |
| `stream_chat(request)` | 流式版本（NDJSON） |

**意图分类规则**：

| 意图 | 触发条件 |
|------|---------|
| `body_metric_recording` | 含"体重/腰围/体脂/睡眠/步数" **且** 有数字 |
| `meal_logging` | 含"吃了/记录/早/午/晚/加餐" **且** 有数字 |
| `meal_plan` | 含"食谱/方案/推荐/计划" |
| `exercise_advice` | 含"运动/跑步/力量/训练" |
| `sleep_stress_advice` | 含"睡眠/熬夜/压力/暴食" |
| `compliance_check` | 含"话术/合规/宣传" |
| `nutrition_qa` | 含"热量/减脂/蛋白质/控糖" |
| `general_health_qa` | 兜底 |

---

## 七、服务层 — 辅助模块

### `food.py` — 饮食管理

| 函数 | 输入 | 输出 | 功能 |
|------|------|------|------|
| `load_foods()` | — | `List[dict]` | 从 `common_foods.csv` 加载全部食物数据 |
| `find_food(food_name)` | 食物名 | `dict` or `None` | 双向包含模糊匹配 |
| `estimate_item(item, source, confidence)` | `FoodLogItem` | `MealRecord` | 查表折算营养素（未命中用保守兜底公式） |
| `add_records(records)` | `List[MealRecord]` | `List[dict]` | 追加饮食记录到 JSON |
| `list_records()` | — | `List[dict]` | 读取全部饮食记录 |
| `daily_summary()` | — | `dict` | 统计**当日**热量/蛋白质/脂肪/碳水汇总 |

### `body_metrics.py` — 身体指标管理

| 函数 | 功能 |
|------|------|
| `add_body_metric(record)` | 写入指标记录（自动补 date + created_at，有体重时算 BMI） |
| `list_body_metrics(user_id)` | 按用户过滤 + 按测量日期升序排列 |
| `trend_summary(user_id)` | 对比首尾记录，生成变化趋势摘要 |
| `parse_body_metric_from_text(text, user_id)` | 正则从自然语言提取体重/腰围/体脂/睡眠/步数 |

### `health.py` — 健康计算

| 函数 | 输入 | 输出 | 公式 |
|------|------|------|------|
| `calculate_profile(profile)` | `HealthProfile` | `{bmi, bmi_status, bmr, tdee, target_calories, protein_range}` | Mifflin-St Jeor |

```
BMR(男) = 10×体重 + 6.25×身高 - 5×年龄 + 5
BMR(女) = 10×体重 + 6.25×身高 - 5×年龄 - 161
TDEE = BMR × 活动系数（1.2 ~ 1.9）
target_calories = TDEE - 400（减脂）
```

### `memory.py` — 短期会话记忆

**设计思路**：滑动窗口 + 摘要压缩。`turns` 保留最近 N 轮完整对话，`summary` 累积更早历史的摘要。

| 函数 | 功能 |
|------|------|
| `new_session_id()` | 生成 UUID 截断格式的会话 ID（`session_` + 12位hex） |
| `session_path(session_id)` | ID → JSON 文件路径（含安全过滤） |
| `load_session(session_id)` | 加载会话数据（不存在返回空模板） |
| `save_turn(session_id, ...)` | 追加一轮对话，超出上限时压缩旧轮次进 summary |
| `build_summary(prev, turns)` | 将轮次提炼为摘要（取最近 6 轮，限制 1200 字符） |
| `get_recent_context(sid)` | 返回 `summary + 最近 N 轮`，供 LLM 注入上下文 |
| `list_sessions()` | 列出所有会话元信息，按时间倒序 |

**Session JSON 结构**：

```json
{
  "session_id": "session_a1b2c3d4e5f6",
  "created_at": "2026-06-04 14:30:00",
  "turns": [
    {
      "time": "2026-06-04 14:30:05",
      "user": "今天吃了什么好？",
      "assistant": "建议你...",
      "intent": "nutrition_qa",
      "tools_used": ["ShortTermMemory", "RAGRetrieverTool"]
    }
  ],
  "summary": "用户问题：昨天体重72kg；助手回复主题：身体指标记录；..."
}
```

### `storage.py` — JSON 文件持久化

| 函数 | 功能 |
|------|------|
| `read_json(path, default)` | 读取 JSON，不存在/损坏返回默认值 |
| `write_json(path, data)` | 全量覆写 JSON（`ensure_ascii=False, indent=2`） |
| `now_text()` | 返回 `YYYY-MM-DD HH:MM:SS` 格式当前时间 |

### `vision.py` — 食物图片识别

| 函数 | 功能 |
|------|------|
| `recognize_food_image(file, meal_type)` | 接收图片 → Base64编码 → OpenAI Vision API → 解析食物+估算营养素 → 写入记录 |

降级逻辑：无 API Key → 默认估算（300g、置信度 0.2）；API 调用异常 → 保守兜底

---

## 八、模块依赖关系

```
config.py  ←── 所有模块的共同依赖（最先加载，配置全局日志）
    │
schemas.py  ←── agent.py, main.py（数据模型定义）
    │
storage.py  ←── food.py, body_metrics.py, memory.py, agent.py（JSON 读写）
    │
health.py  ←── agent.py, body_metrics.py（健康计算）
    │
food.py, body_metrics.py, memory.py  ←── agent.py（数据读写服务）
    │
text_processing.py  ←── bm25_utils.py, rag_service.py（文本预处理）
    │
bm25_utils.py  ←── rag_service.py（BM25 降级）
    │
vector_store.py  ←── rag_service.py（Milvus + ES + Embedding）
    │
utils.py  ←── rag_service.py（融合 + 日志）
    │
rag_service.py  ←── agent.py, main.py（RAG 核心）
    │
vision.py  ←── main.py（视觉识别）
    │
agent.py  ←── main.py（核心 Agent）
    │
main.py  ←── 用户请求入口
```

---

*最后更新：2026-06-04*
