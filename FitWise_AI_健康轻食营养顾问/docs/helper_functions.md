# NutriFit 辅助工具函数参考手册

## 文件存储 (`storage.py`)

### `read_json(path, default)`

从 JSON 文件读取数据，文件不存在或解析失败时返回默认值。

| 参数      | 类型   | 说明                           |
| --------- | ------ | ------------------------------ |
| `path`    | `Path` | JSON 文件路径                  |
| `default` | `Any`  | 文件不存在或解析失败时的回退值 |

| 返回  | 说明                       |
| ----- | -------------------------- |
| `Any` | 解析后的数据，或 `default` |

```python
data = read_json(RECORDS_FILE, [])   # 文件不存在 → 返回空列表
```

### `write_json(path, data)`

将数据**全量覆写**到 JSON 文件（非增量追加）。自动创建父目录，`ensure_ascii=False` 保证中文可读，`indent=2` 格式化输出。

| 参数   | 类型   | 说明                             |
| ------ | ------ | -------------------------------- |
| `path` | `Path` | 目标 JSON 文件路径               |
| `data` | `Any`  | 要写入的数据（需可 JSON 序列化） |

### `now_text()`

返回当前时间的格式化字符串。

| 返回  | 示例                    |
| ----- | ----------------------- |
| `str` | `"2026-06-03 14:30:05"` |

---

## 饮食记录 (`food.py`)

### `load_foods()`

从 `data/food_database/common_foods.csv` 加载全部食物营养数据。

| 返回         | 类型     | 说明                                                                                                                              |
| ------------ | -------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `List[dict]` | 食物列表 | 每个元素包含 `food_name`, `category`, `calories_per_100g`, `protein_per_100g`, `fat_per_100g`, `carbs_per_100g`, `fiber_per_100g` |

### `find_food(food_name)`

在食物数据库中模糊搜索匹配的食物（双向包含匹配：查询名包含库中名 或 库中名包含查询名）。

| 参数        | 类型  | 说明                                |
| ----------- | ----- | ----------------------------------- |
| `food_name` | `str` | 搜索关键词，如 `"鸡胸肉"`、`"米饭"` |

| 返回             | 说明                                |
| ---------------- | ----------------------------------- |
| `dict` or `None` | 匹配到的食物记录，未找到返回 `None` |

### `estimate_item(item, source, confidence)`

根据食物名和克数估算一顿饭的营养素。优先从 `common_foods.csv` 查表（按 g/100g 折算），查不到则用保守兜底公式：

```
兜底估算：calories = grams × 1.2  |  protein = grams × 0.05  |  fat = grams × 0.03  |  carbs = grams × 0.15
```

| 参数         | 类型              | 说明                                                                          |
| ------------ | ----------------- | ----------------------------------------------------------------------------- |
| `item`       | `FoodLogItem`     | 包含 `food_name`, `grams`, `meal_type`                                        |
| `source`     | `str`             | 记录来源标签，如 `"manual"`, `"agent_text"`, `"image_ai"`, `"image_fallback"` |
| `confidence` | `float` or `None` | 置信度（0~1），手动录入可不传                                                 |

| 返回         | 类型           | 说明                                                                                  |
| ------------ | -------------- | ------------------------------------------------------------------------------------- |
| `MealRecord` | Pydantic Model | 含 `id`, `created_at`, `calories`, `protein`, `fat`, `carbs`, `confidence` 等完整字段 |

### `add_records(records)`

将一组 `MealRecord` 追加到 `meal_records.json`（读出现有记录 → 追加新记录 → 全量覆写）。

| 参数      | 类型               | 说明                 |
| --------- | ------------------ | -------------------- |
| `records` | `List[MealRecord]` | 待添加的饮食记录列表 |

| 返回         | 类型     | 说明             |
| ------------ | -------- | ---------------- |
| `List[dict]` | 所有记录 | 追加后的全量记录 |

### `list_records()`

返回 `meal_records.json` 中的全部饮食记录（无过滤）。

| 返回         | 类型     |
| ------------ | -------- |
| `List[dict]` | 全部记录 |

### `daily_summary()`

统计**当日**的饮食摄入汇总（按 `created_at[:10]` 过滤当天记录）。

| 返回   | 类型 | 示例                                                                                    |
| ------ | ---- | --------------------------------------------------------------------------------------- |
| `dict` | 汇总 | `{"calories": 1680.5, "protein": 72.3, "fat": 45.1, "carbs": 210.0, "record_count": 5}` |

---

## 身体指标 (`body_metrics.py`)

### `add_body_metric(record)`

将一条身体指标记录写入 `body_metrics.json`。自动补齐 `date`（未填则取当天）和 `created_at`。如果提供了 `weight_kg`，会自动计算 BMI 并附带。

| 参数     | 类型               | 说明                                                                              |
| -------- | ------------------ | --------------------------------------------------------------------------------- |
| `record` | `BodyMetricRecord` | 含 `weight_kg`, `waist_cm`, `body_fat_percent`, `sleep_hours`, `steps` 等可选字段 |

| 返回   | 类型         | 说明                                             |
| ------ | ------------ | ------------------------------------------------ |
| `dict` | 写入后的记录 | 额外包含 `date`, `created_at`, `bmi`（如有体重） |

### `list_body_metrics(user_id)`

按 `user_id` 过滤身体指标记录，并按 `date`（测量日）升序排列。

| 参数      | 类型  | 默认值           |
| --------- | ----- | ---------------- |
| `user_id` | `str` | `"default_user"` |

| 返回         | 类型                         | 说明 |
| ------------ | ---------------------------- | ---- |
| `List[dict]` | 该用户的所有记录，按日期升序 |

### `trend_summary(user_id)`

生成该用户身体指标的**长期趋势摘要**，对比最新记录和首次记录的变化（体重、腰围、体脂率）。

| 参数      | 类型  | 默认值           |
| --------- | ----- | ---------------- |
| `user_id` | `str` | `"default_user"` |

| 返回   | 类型                                                                                                                              | 说明 |
| ------ | --------------------------------------------------------------------------------------------------------------------------------- | ---- |
| `dict` | `{"records": [...], "summary": "体重较首次记录下降 2.5 kg。腰围下降 3 cm。", "latest": {...}, "delta": {"weight_kg": -2.5, ...}}` |

### `parse_body_metric_from_text(text, user_id)`

用正则从自然语言文本中提取身体指标。支持体重（kg）、腰围（cm）、体脂率（%）、睡眠（小时）、步数。一个都没匹配到时返回 `None`。

| 参数      | 类型  | 说明                                             |
| --------- | ----- | ------------------------------------------------ |
| `text`    | `str` | 用户原始输入，如 `"今天体重72.5公斤，睡了7小时"` |
| `user_id` | `str` | 用户 ID，默认 `"default_user"`                   |

| 返回                         | 说明                                                                             |
| ---------------------------- | -------------------------------------------------------------------------------- |
| `BodyMetricRecord` or `None` | 解析成功返回模型实例（含 `note="由 Agent 从用户文本中提取"`），无匹配返回 `None` |

---

## 健康计算 (`health.py`)

### `calculate_profile(profile)`

基于 Mifflin-St Jeor 公式计算用户的 BMR（基础代谢率）、TDEE（每日总消耗）、目标热量和推荐蛋白质范围。

| 参数      | 类型            | 说明                                                                   |
| --------- | --------------- | ---------------------------------------------------------------------- |
| `profile` | `HealthProfile` | 含 `gender`, `age`, `height_cm`, `weight_kg`, `activity_level`, `goal` |

| 返回   | 类型     | 说明                                                                                          |
| ------ | -------- | --------------------------------------------------------------------------------------------- |
| `dict` | 计算结果 | 包含 `bmi`, `bmi_status`, `bmr`, `tdee`, `target_calories`, `protein_range`, `calorie_advice` |

关键公式：

```
BMR(男) = 10×体重 + 6.25×身高 - 5×年龄 + 5
BMR(女) = 10×体重 + 6.25×身高 - 5×年龄 - 161
TDEE = BMR × 活动系数
target_calories = TDEE - 400 (减脂目标) / TDEE (其他)
```

活动系数：`sedentary=1.2` → `light=1.375` → `moderate=1.55` → `active=1.725` → `very_active=1.9`

---

## 短期记忆 (`memory.py`)

### `new_session_id()`

生成新的会话 ID。

| 返回  | 示例                     |
| ----- | ------------------------ |
| `str` | `"session_a1b2c3d4e5f6"` |

实现：`uuid4().hex[:12]` 取 UUID 的前 12 位十六进制字符，前缀 `session_`。

### `session_path(session_id)`

将 session*id 映射为对应的 JSON 文件路径，同时过滤掉路径遍历字符（只保留字母、数字、`*`、`-`）。

| 参数         | 类型  | 说明                        |
| ------------ | ----- | --------------------------- |
| `session_id` | `str` | 如 `"session_a1b2c3d4e5f6"` |

| 返回   | 示例                                                      |
| ------ | --------------------------------------------------------- |
| `Path` | `.../storage/memory/short_term/session_a1b2c3d4e5f6.json` |

### `load_session(session_id)`

加载指定会话的完整数据，文件不存在时返回空会话模板。

| 参数         | 类型  |
| ------------ | ----- |
| `session_id` | `str` |

| 返回   | 类型     | 说明                                                                     |
| ------ | -------- | ------------------------------------------------------------------------ |
| `dict` | 会话数据 | `{"session_id": str, "created_at": str, "turns": [...], "summary": str}` |

### `save_turn(session_id, user_message, assistant_answer, intent, tools_used)`

追加一轮对话到会话文件中。如果轮数超过 `SHORT_TERM_MAX_TURNS`，自动将被淘汰的旧轮次压缩进 `summary`（滑动窗口 + 摘要压缩）。

| 参数               | 类型        | 说明               |
| ------------------ | ----------- | ------------------ |
| `session_id`       | `str`       | 会话 ID            |
| `user_message`     | `str`       | 用户消息原文       |
| `assistant_answer` | `str`       | 助手回复全文       |
| `intent`           | `str`       | 本轮意图分类       |
| `tools_used`       | `List[str]` | 本轮使用的工具列表 |

### `build_summary(previous_summary, turns)`

将对话轮次压缩为摘要文本（取最近 6 轮的 user + intent），与已有摘要拼接，限制总长度 1200 字符。

| 参数               | 类型         |
| ------------------ | ------------ |
| `previous_summary` | `str`        |
| `turns`            | `List[dict]` |

| 返回  | 类型       |
| ----- | ---------- |
| `str` | 摘要字符串 |

### `get_recent_context(session_id)`

返回会话的摘要 + 最近 N 轮对话，供 LLM 调用时注入上下文。

| 参数         | 类型  |
| ------------ | ----- |
| `session_id` | `str` |

| 返回   | 类型                                    | 说明                                 |
| ------ | --------------------------------------- | ------------------------------------ |
| `dict` | `{"summary": str, "turns": List[dict]}` | turns 最多 `SHORT_TERM_MAX_TURNS` 条 |

### `list_sessions()`

列出所有会话的元信息摘要（不包含完整 turns 和 summary）。

| 返回         | 类型                 | 说明                                                            |
| ------------ | -------------------- | --------------------------------------------------------------- |
| `List[dict]` | 按 `created_at` 倒序 | 每条含 `session_id`, `created_at`, `turn_count`, `last_message` |

---

## RAG 包 — 文本预处理 (`rag/text_processing.py`)

### `normalize_text(text)`

规范化空白字符：连续空白 → 单个空格，去首尾空白。

| 参数   | 类型  |
| ------ | ----- |
| `text` | `str` |

| 返回  | 类型         |
| ----- | ------------ |
| `str` | 规范化后文本 |

### `tokenize(text)`

中文用 jieba 分词（不可用时退回正则），英文/数字保留整词，过滤标点和空白，为 BM25 检索服务。

| 参数   | 类型  |
| ------ | ----- |
| `text` | `str` |

| 返回        | 类型     |
| ----------- | -------- |
| `List[str]` | 词条列表 |

### `chunk_markdown(text)` ⭐ 新版

MD 知识库父子块切分：按空行/标题拆段落 → parent(≤512 chars，句边界保护) → child(160 chars + 40 overlap 滑动窗口)。

| 参数   | 类型  | 说明    |
| ------ | ----- | ------- |
| `text` | `str` | MD 原文 |

| 返回                          | 类型                                          | 说明 |
| ----------------------------- | --------------------------------------------- | ---- |
| `List[Tuple[str, List[str]]]` | `[(parent_text, [child1, child2, ...]), ...]` |

### `chunk_csv_row(text)` ⭐ 新版

CSV 食物库/食谱库父子等长切分：一行 = parent = child，不切分。

| 参数   | 类型  | 说明                |
| ------ | ----- | ------------------- |
| `text` | `str` | 单行 CSV 格式化文本 |

| 返回                    | 类型                                            | 说明 |
| ----------------------- | ----------------------------------------------- | ---- |
| `Tuple[str, List[str]]` | `(parent_text, [child_text])` — child == parent |

### `chunk_text(text, chunk_size, overlap)` 兼容旧版

固定字符长度 + 重叠滑动窗口切分（旧版兼容接口）。

### `infer_domain(path)`

根据文件路径推断知识领域分类。

| 文件名关键词               | 归入领域           |
| -------------------------- | ------------------ |
| "体重" / "减脂"            | `体重管理`         |
| "食谱"                     | `食谱推荐`         |
| "运动"                     | `运动建议`         |
| "睡眠"                     | `生活方式`         |
| "客服" / "合规"            | `客服合规`         |
| 父目录名为 `food_database` | `食物营养库`       |
| 其他                       | `健康知识`（兜底） |

### `split_sentences(text)`

按中英文句末标点（`。！？.!?`）拆分句子，保留标点。

| 参数   | 类型  |
| ------ | ----- |
| `text` | `str` |

| 返回        | 类型               |
| ----------- | ------------------ |
| `List[str]` | 句子列表（含标点） |

**常量**：

| 常量               | 值                            | 说明                  |
| ------------------ | ----------------------------- | --------------------- |
| `MD_PARENT_MAX`    | 512                           | MD 父块最大字符数     |
| `MD_CHILD_SIZE`    | 160                           | MD 子块窗口大小       |
| `MD_CHILD_OVERLAP` | 40                            | MD 子块重叠字符数     |
| `DOMAIN_TERMS`     | `["热量缺口","基础代谢",...]` | 领域术语列表（15 个） |

---

## RAG 包 — 内存 BM25 (`rag/bm25_utils.py`)

### `BM25Index` 类

纯 Python BM25 全文索引，Elasticsearch 不可用时的降级方案。

| 方法                               | 功能                                                     |
| ---------------------------------- | -------------------------------------------------------- |
| `build(texts: List[str])`          | 分词 → 文档频率(IDF) → 平均文档长度，构建完整索引        |
| `score(query: str) -> List[float]` | 对全部文档计算 BM25 分数（k1=1.5, b=0.75, IDF 平滑公式） |

---

## RAG 包 — 存储层 (`rag/vector_store.py`)

### `EmbeddingService` 类

| 方法                               | 功能                                                                                                             |
| ---------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| `batch_embed(texts) -> np.ndarray` | 批量调用 Embedding API (`text-embedding-v3`)，返回 `(N, 1024)` float32 数组。失败槽位填零向量，无 API Key 时全零 |

### `MilvusClient` 类

Milvus 向量数据库客户端。Schema：`parent_idx` + `child_content` + `source` + `domain` + `chunk_id` + `embedding(1024-dim, COSINE + IVF_FLAT)`。

| 方法                                                  | 功能                                                |
| ----------------------------------------------------- | --------------------------------------------------- |
| `insert_children(children, embeddings) -> bool`       | 批量写入子块向量（含 parent_idx 映射回父块）        |
| `search(query_emb, top_k) -> List[Tuple[int, float]]` | ANN 向量检索，返回 `[(parent_idx, cos_score), ...]` |
| `delete_all() -> bool`                                | 清空 Collection                                     |
| `count` (property)                                    | Collection 中的实体数量                             |
| `ok` (property)                                       | Milvus 是否可用                                     |

### `ESClient` 类

Elasticsearch BM25 检索客户端。Mapping：`parent_idx(integer)` + `content(text)` + `source` + `domain` + `chunk_id`。

| 方法                                              | 功能                                                  |
| ------------------------------------------------- | ----------------------------------------------------- |
| `index_parents(parents) -> bool`                  | 批量索引父块全文到 ES                                 |
| `search(query, top_k) -> List[Tuple[int, float]]` | BM25 全文检索，返回 `[(parent_idx, bm25_score), ...]` |
| `delete_index() -> bool`                          | 删除并重建索引                                        |
| `count` (property)                                | 索引中的文档数量                                      |
| `ok` (property)                                   | ES 是否可用                                           |

### `vector_topk_numpy(query_emb, doc_embeddings, top_k)`

numpy 全量余弦相似度 + top-k（Milvus 降级用）。

| 参数             | 类型         | 说明                  |
| ---------------- | ------------ | --------------------- |
| `query_emb`      | `np.ndarray` | (dim,) 查询向量       |
| `doc_embeddings` | `np.ndarray` | (N, dim) 文档向量矩阵 |
| `top_k`          | `int`        | 返回数量              |

| 返回                      | 类型                          |
| ------------------------- | ----------------------------- |
| `List[Tuple[int, float]]` | `[(doc_idx, cos_score), ...]` |

---

## RAG 包 — 通用工具 (`rag/utils.py`)

### `extract_json(text)`

从 LLM 返回文本中提取首个 JSON 对象（容忍 markdown/额外文本）。

### `weighted_fuse(es_hits, mv_hits, top_k)`

多路召回去重合并 + 65%向量/35%BM25 加权融合。按 parent_idx 去重，同一 parent 取最高分。

### `full_fallback_fuse(bm25_scores, vector_scores, top_k)`

全量融合回退（ES + Milvus 均不可用时）：内存 BM25 + numpy 向量 → 加权融合。

### `setup_logging(name)`

获取模块 Logger（根 Logger 已在 config.py 配置好控制台+文件双输出）。

**常量**：

| 常量              | 值   | 说明                  |
| ----------------- | ---- | --------------------- |
| `VECTOR_WEIGHT`   | 0.65 | 语义向量融合权重      |
| `BM25_WEIGHT`     | 0.35 | BM25 关键词融合权重   |
| `ES_RECALL_K`     | 10   | ES BM25 单路召回数    |
| `MILVUS_RECALL_K` | 10   | Milvus 向量单路召回数 |

---

## 视觉识别 (`vision.py`)

### `_extract_json(text)`

从 LLM 返回文本中提取 JSON 对象（容忍 markdown 包裹和多余文本）。用正则 `\{.*\}` 匹配第一个花括号块，`re.S` 允许跨行。

| 参数   | 类型  | 说明             |
| ------ | ----- | ---------------- |
| `text` | `str` | LLM 原始返回文本 |

| 返回             | 说明                                    |
| ---------------- | --------------------------------------- |
| `dict` or `None` | 解析成功返回 JSON 对象，失败返回 `None` |

### `recognize_food_image(file, meal_type)`

异步函数。接收用户上传的食物图片，调用 OpenAI Vision API 识别食物名、估算克数和营养素。无 API Key 时走兜底估算。

| 参数        | 类型         | 说明                        |
| ----------- | ------------ | --------------------------- |
| `file`      | `UploadFile` | FastAPI 上传文件对象        |
| `meal_type` | `str`        | 餐次标签，默认 `"拍照记录"` |

| 返回   | 类型                                               | 说明                                              |
| ------ | -------------------------------------------------- | ------------------------------------------------- |
| `dict` | `{"image": str, "items": List[dict], "raw": dict}` | `raw` 为 API 原始返回，`items` 为已写入的饮食记录 |

---

## NutriFitAgent 类 (`agent.py`)

### `classify_intent(message)`

用关键词 + 数字信号将用户消息归为 7 种意图之一。

| 返回                    | 触发条件                                    |
| ----------------------- | ------------------------------------------- |
| `body_metric_recording` | 含 "体重/腰围/体脂/睡眠/步数" 且有数字      |
| `meal_logging`          | 含 "吃了/记录/早餐/午餐/晚餐/加餐" 且有数字 |
| `meal_plan`             | 含 "食谱/方案/推荐/计划"                    |
| `exercise_advice`       | 含 "运动/跑步/力量/训练"                    |
| `sleep_stress_advice`   | 含 "睡眠/熬夜/压力/暴食"                    |
| `compliance_check`      | 含 "话术/合规/宣传"                         |
| `nutrition_qa`          | 含 "热量/减脂/蛋白质/控糖"                  |
| `general_health_qa`     | 兜底                                        |

### `plan(intent)`

根据意图生成可追踪的执行计划（`agent_trace` 的第一步）。

### `parse_food_items(message)`

从用户消息中正则提取食物名 + 克数 + 餐次，返回最多 6 个 `FoodLogItem`。

### `load_profile_context()` / `load_food_context()`

分别读取用户身体档案（含 `calculate_profile` 计算结果）和今日饮食记录 + 最近 8 条。

### `run_tools(intent, request, session_id)`

执行工具调用：读取 4 个必读数据源（短期记忆、长期身体趋势、身体档案、饮食记录），按需触发身体指标写入 / 饮食记录写入 / RAG 检索。

### `build_prompt(request, intent, sources, tool_state)`

将 `run_tools` 的结果拼接为发给 LLM 的完整 prompt。

### `generate_answer(request, intent, sources, tool_state)`

调用 OpenAI Chat Completions API 生成回答；无 API Key 时降级为抽取式摘要。

### `reflect(answer, intent)`

合规复核：检查禁用词（"治疗"/"治愈"/"保证瘦" 等）并替换为 "辅助健康管理"，必要时补全风险提示。

### `chat(request)`

完整流水线入口：classify → plan → run_tools → generate → reflect → save_turn，返回 `AgentChatResponse`。

---

## RAGService 类 (`rag/rag_service.py`)

核心 RAG 编排器，协调 Parent-Child 父子块检索 + 多路召回 + 精排 + 事实校验的完整管道。

### `load(rebuild: bool = False) -> RAGService`

扫描知识库三种目录 → 分别做父子切分 → Embedding API 生成父块+子块向量 → Milvus 写入子块、ES 写入父块 → 构建内存 BM25 降级索引。

| 参数      | 类型   | 默认值  | 说明                                        |
| --------- | ------ | ------- | ------------------------------------------- |
| `rebuild` | `bool` | `False` | True 强制清空重建；False 智能跳过已就绪数据 |

### `_rewrite_query(question: str) -> str` ⭐ 新增

查询改写 + 领域词增强：LLM 把口语化问题转成关键词明确的短查询，附加领域术语提升 BM25 召回。失败时返回原问题。

### `retrieve(question: str, top_k: int = 6) -> List[dict]` ⭐ 多路召回

ES BM25（父块全文, top10）+ Milvus ANN（子块向量, top10） → 按 parent_idx 去重 → 65%向量/35%BM25 加权融合 → top_k。

四级降级链：ES+MV → ES+numpy → 内存BM25+MV → 内存BM25+numpy。

### `rerank(question: str, candidates: List[dict], top_k: int = 3) -> List[dict]`

Rerank 模型（`qwen3-rerank`）交叉编码精排。失败时降级为粗排分数排序。

### `_fact_check(answer: str, sources: List[dict]) -> dict` ⭐ 新增

生成后逐句溯源校验：拆分句子 → 每句 × 证据 → LLM 判断可信/可疑 → 可疑句标记替换为「暂无明确证据支持」。

### `answer(question: str, top_k: int = 3, coarse_top_k: int = 15) -> dict`

端到端 RAG 问答入口。Pipeline：`_rewrite_query → retrieve(coarse_top_k) → rerank(top_k) → LLM 生成 → _fact_check`。

| 返回值                        | 类型         | 说明                               |
| ----------------------------- | ------------ | ---------------------------------- |
| `answer`                      | `str`        | 校验后的最终回答                   |
| `sources`                     | `List[dict]` | 精排后的证据列表                   |
| `fact_check.suspicious_spans` | `List[dict]` | 被标记为可疑的句子（含原文和索引） |
| `fact_check.checked`          | `bool`       | 是否执行了事实校验                 |

### `add_docs(new_docs: List[dict], embed: bool = True) -> int` ⭐ 新增

增量添加文档到 ES + Milvus + 内存索引，支持运行时更新知识库而无需重启。

---

_最后更新：2026-06-04_
