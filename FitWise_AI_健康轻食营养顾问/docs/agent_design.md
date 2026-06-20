# NutriFit Agent — 设计文档

## 〇、项目完整流程图

```
                          ┌──────────────────────────────────────────┐
                          │          NutriFit AI 健康轻食顾问          │
                          └──────────────────────────────────────────┘

 ═══════════════════════════════ HTTP 入口 ═══════════════════════════════

    POST /api/agent/chat              POST /api/rag/chat
    { message, session_id, ... }      { question, top_k }
         │                                  │
         ▼                                  ▼
 ┌───────────────────┐            ┌───────────────────┐
 │  NutriFitAgent    │            │   RAGService      │
 │  (agent.py)       │            │   (rag/)          │
 └───────────────────┘            └───────────────────┘


 ══════════════════════════ Agent 完整管道 ═══════════════════════════════

 用户消息: "我体重72kg，减脂期每天吃多少热量合适？"
     │
     ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ ① classify_intent(message)                                          │
 │    关键词 + 数字 → "body_metric_recording"                           │
 │    （同时含"体重"+"72"触发身体指标记录；含"减脂"+"热量"触发营养问答） │
 └──────────────────────────────┬──────────────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ ② plan(intent)                                                      │
 │    → ["识别意图：body_metric_recording",                             │
 │        "读取会话上下文、身体档案、长期趋势、今日饮食、知识库证据",      │
 │        "从文本中抽取体重指标并写入长期档案",                           │
 │        "调用RAG检索生成带证据的专业回答",                              │
 │        "执行Reflection合规复核"]                                     │
 └──────────────────────────────┬──────────────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ ③ run_tools(intent, request, session_id)          ← ReAct 工具执行   │
 │                                                                      │
 │  ┌─ 必读 4 个数据源 ─────────────────────────────────────────────┐  │
 │  │  ShortTermMemory      get_recent_context(session_id)           │  │
 │  │                        → memory/short_term/session_xxx.json    │  │
 │  │  LongTermBodyMemory   trend_summary(user_id)                   │  │
 │  │                        → memory/long_term/body_metrics.json    │  │
 │  │  HealthProfileReader  load_profile_context() → calculate_profile│  │
 │  │                        → user_profile.json (BMI/BMR/TDEE)      │  │
 │  │  FoodRecordReader     load_food_context()                      │  │
 │  │                        → meal_records.json (今日饮食汇总)       │  │
 │  └────────────────────────────────────────────────────────────────┘  │
 │                                                                      │
 │  ┌─ 按需写入 ────────────────────────────────────────────────────┐  │
 │  │  trigger: body_metric_recording                                 │  │
 │  │    parse_body_metric_from_text("我体重72kg...")                  │  │
 │  │      → BodyMetricRecord(weight_kg=72.0)                         │  │
 │  │    add_body_metric(record) → body_metrics.json                  │  │
 │  │                                                                  │  │
 │  │  trigger: meal_logging                                          │  │
 │  │    parse_food_items("吃了200g鸡胸肉") → [FoodLogItem]            │  │
 │  │    estimate_item(item) → MealRecord(calories,protein,...)       │  │
 │  │    add_records([record]) → meal_records.json                    │  │
 │  └────────────────────────────────────────────────────────────────┘  │
 │                                                                      │
 │  ┌─ 按需检索 ────────────────────────────────────────────────────┐  │
 │  │  trigger: use_rag=True                                          │  │
 │  │    rag_service.retrieve(message, top_k=6)                       │  │
 │  │      → 知识库证据列表（详见下方 RAG 管道）                        │  │
 │  └────────────────────────────────────────────────────────────────┘  │
 └──────────────────────────────┬──────────────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ ④ generate_answer(request, intent, sources, tool_state)              │
 │    build_prompt() → 拼接完整上下文:                                   │
 │      ┌ 用户消息 / 身体档案 / BMR-TDEE / 长期趋势                      │
 │      └ 今日饮食 / 对话历史 / 已记录指标 / RAG证据                      │
 │    → OpenAI API (qwen3-max) 或 降级抽取式摘要                         │
 └──────────────────────────────┬──────────────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ ⑤ reflect(answer, intent)                    ← 合规复核              │
 │    禁用词过滤: "治疗"/"保证瘦"/"治愈" → "辅助健康管理"                 │
 │    风险提示补全: "以上内容仅用于健康科普..."                            │
 └──────────────────────────────┬──────────────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ ⑥ save_turn(session_id, message, answer, intent, tools_used)         │
 │    → memory/short_term/session_xxx.json                              │
 │    滑动窗口 + 摘要压缩（超出 SHORT_TERM_MAX_TURNS 则压缩旧轮次）        │
 └──────────────────────────────┬──────────────────────────────────────┘
                                ▼
 ┌─────────────────────────────────────────────────────────────────────┐
 │ ⑦ return AgentChatResponse {                                         │
 │      session_id, answer, intent, tools_used,                        │
 │      sources, memory_used, agent_trace                              │
 │    }                                                                 │
 └─────────────────────────────────────────────────────────────────────┘


 ═══════════════════════ RAG 检索增强生成管道 ═══════════════════════════

 用户问题: "减脂期每天吃多少热量合适？"
     │
     ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ Step 1: _rewrite_query(question)       查询改写 + 领域词增强   │
 │   LLM (qwen3-max, max_tokens=120)                             │
 │   "减脂期每天吃多少热量合适？"                                   │
 │     → "减脂 热量缺口 基础代谢 每日摄入量 TDEE"                   │
 │   失败降级: 返回原问题                                          │
 └──────────────────────────┬───────────────────────────────────┘
                            ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ Step 2: retrieve(search_query, top_k=15)    多路混合召回       │
 │                                                               │
 │   ┌─── ES BM25 ──────────────────────────────────────────┐   │
 │   │  query → ES.match(content=search_query)               │   │
 │   │  → [(parent_idx, bm25_score), ...] top10              │   │
 │   │  父块全文检索，专业倒排索引                              │   │
 │   └───────────────────────────────────────────────────────┘   │
 │                            ⊕                                    │
 │   ┌─── Milvus ANN ───────────────────────────────────────┐   │
 │   │  embed(query) → child ANN search (COSINE, IVF_FLAT)   │   │
 │   │  → [(parent_idx, cos_score), ...] top10               │   │
 │   │  子块向量检索，细粒度语义匹配                            │   │
 │   └───────────────────────────────────────────────────────┘   │
 │                            │                                    │
 │                    去重合并（按 parent_idx）                      │
 │                    65%向量 + 35%BM25 加权融合                     │
 │                    → top15 粗排结果                              │
 │                                                                 │
 │   四级降级链:                                                    │
 │     ES✅+MV✅ → ES✅+numpy✅ → BM25+MV✅ → BM25+numpy✅          │
 └──────────────────────────┬───────────────────────────────────┘
                            ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ Step 3: rerank(search_query, candidates, top_k=3)  Cross-Encoder精排 │
 │   POST /v1/rerank { model: "qwen3-rerank",                    │
 │     query, documents: [parent_content[:800], ...] }           │
 │   → [{index: 2, relevance_score: 0.92}, ...]                  │
 │   失败降级: 粗排分数排序                                        │
 └──────────────────────────┬───────────────────────────────────┘
                            ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ Step 4: LLM 生成                                              │
 │   prompt = 系统角色 + 原始用户问题 + 3条精排后的parent证据       │
 │   → OpenAI API (qwen3-max, temperature=0.2)                    │
 │   → "减脂期建议每日摄入热量为TDEE减去300-500kcal..."            │
 └──────────────────────────┬───────────────────────────────────┘
                            ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ Step 5: _fact_check(answer, sources)     事实溯源校验          │
 │   _split_sentences(answer) → 逐句 × 证据 → LLM判定可信/可疑     │
 │   可疑句 → "（暂无明确证据支持）"                                │
 │   失败降级: 返回原回答，checked=false                           │
 └──────────────────────────┬───────────────────────────────────┘
                            ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ return { "answer": "校验后回答",                                │
 │          "sources": [精排后3条证据],                            │
 │          "fact_check": { suspicious_spans, checked } }         │
 └──────────────────────────────────────────────────────────────┘


 ═══════════════════════ 存储架构 ═════════════════════════════════════

  ┌──────────────────────────────────────────────────────┐
  │                    文件存储层 (JSON)                    │
  │                                                       │
  │  storage/                                             │
  │  ├── meal_records.json      饮食记录                   │
  │  ├── user_profile.json      用户身体档案               │
  │  ├── memory/                                          │
  │  │   ├── short_term/        短期会话记忆               │
  │  │   │   ├── session_xxx.json  (滑动窗口 + 摘要压缩)   │
  │  │   │   └── ...                                      │
  │  │   └── long_term/         长期身体指标               │
  │  │       └── body_metrics.json                        │
  │  └── uploads/               用户上传图片               │
  └──────────────────────────────────────────────────────┘

  ┌──────────────────────────┐  ┌──────────────────────────┐
  │   Milvus 向量数据库        │  │  Elasticsearch BM25      │
  │                           │  │                          │
  │   Collection:              │  │  Index:                  │
  │   nutrifit_knowledge       │  │  nutrifit_knowledge      │
  │                           │  │                          │
  │   存储: 子块向量 (child)    │  │  存储: 父块全文 (parent)  │
  │   parent_idx + child_      │  │  parent_idx + content    │
  │   content + embedding      │  │  + source + domain       │
  │   (1024-dim, COSINE,       │  │  (standard analyzer)     │
  │    IVF_FLAT, nlist=128)    │  │                          │
  │                           │  │                          │
  │   不可用时: numpy内存降级   │  │  不可用时: 内存BM25降级   │
  └──────────────────────────┘  └──────────────────────────┘

  ┌──────────────────────────────────────────────────────┐
  │                 知识库原始文件 (data/)                   │
  │                                                       │
  │  data/                                                │
  │  ├── knowledge_base/    .md / .txt  健康知识文章        │
  │  ├── recipes/           .csv        食谱数据            │
  │  └── food_database/     .csv        食物营养数据        │
  └──────────────────────────────────────────────────────┘


 ═══════════════════════ 数据流向总览 ═════════════════════════════════

 用户发消息
     │
     ├─→ classify_intent ───→ 意图分类
     │
     ├─→ run_tools
     │     ├─ 短期记忆 ← JSON ← 会话文件
     │     ├─ 长期指标 ← JSON ← body_metrics.json
     │     ├─ 身体档案 ← JSON ← user_profile.json
     │     ├─ 今日饮食 ← JSON ← meal_records.json
     │     ├─ 指标写入 → JSON → body_metrics.json
     │     ├─ 饮食写入 → JSON → meal_records.json
     │     └─ RAG 检索 → ES + Milvus → 知识库证据
     │
     ├─→ generate_answer → LLM (qwen3-max)
     │
     ├─→ reflect → 合规过滤
     │
     ├─→ save_turn → JSON → 会话文件
     │
     └─→ AgentChatResponse → 返回用户

 读取操作: JSON / ES / Milvus → 内存 → LLM prompt
 写入操作: 用户数据 → JSON → 持久化
 检索操作: 改写 → ES(BM25) + Milvus(ANN) → 精排 → LLM生成 → 事实校验


## 一、架构概览

## 二、为什么这么设计

### 2.1 意图分类 (`classify_intent`)

```
用户消息 → 关键词 + 数字信号 → 7 种意图之一
```

| 意图 | 触发条件 | 后续行为 |
|------|---------|---------|
| `body_metric_recording` | 含"体重/腰围/体脂/睡眠/步数" **且** 有数字 | 解析指标 → 写入长期档案 |
| `meal_logging` | 含"吃了/记录/早/午/晚/加餐" **且** 有数字 | 解析食物 → 估算营养 → 写入饮食记录 |
| `meal_plan` | 含"食谱/方案/推荐/计划" | 查档案 + 查知识库 → 生成方案 |
| `exercise_advice` | 含"运动/跑步/力量/训练" | RAG 检索 → 运动建议 |
| `sleep_stress_advice` | 含"睡眠/熬夜/压力/暴食" | RAG 检索 → 生活方式建议 |
| `compliance_check` | 含"话术/合规/宣传" | 合规审查 |
| `nutrition_qa` | 含"热量/减脂/蛋白质/控糖" | RAG 检索 → 营养问答 |
| `general_health_qa` | 兜底 | RAG 检索 → 通用健康问答 |

**设计理由**：
- 意图决定了是否需要写数据（body_metric / meal_logging 有副作用，其他只读）
- 关键词简单高效，无需额外 LLM 调用，零延迟
- 数字信号作为必要条件是关键判断 — 防止"我想减肥"被误判为体重记录

### 2.2 Plan 规划 (`plan`)

```python
① 识别意图
② 读取上下文（会话记忆 + 身体档案 + 长期趋势 + 今日饮食 + 知识库）
③ 根据意图执行具体动作（解析指标 / 记录饮食 / 生成方案 / RAG 检索）
④ Reflection 合规复核
```

**设计理由**：Plan 不控制执行，只生成可追踪的 blueprint。前端通过 `agent_trace` 渲染 Agent 的思考过程，用户可以看到每一步在做什么。

### 2.3 工具执行 (`run_tools`)

**4 个必读数据源**（任何意图都读）：

```
ShortTermMemory   → 最近 N 轮对话（跨轮上下文）
LongTermBodyMemory → 体重/腰围/体脂历史趋势
HealthProfileReader → 身高/体重/年龄/目标 → BMR/TDEE/BMI
FoodRecordReader   → 今日饮食汇总 + 最近 8 条记录
```

**2 个按需写入工具**（仅特定意图触发）：

| 工具 | 触发意图 | 作用 |
|------|---------|------|
| `BodyMetricRecorder` | `body_metric_recording` | 正则解析文本 → `add_body_metric()` 写入 JSON |
| `MealRecordTool` | `meal_logging` | 正则解析食物名+克数 → `estimate_item()` 估算营养 → `add_records()` 写入 JSON |

**1 个按需检索工具**：

| 工具 | 触发条件 | 作用 |
|------|---------|------|
| `RAGRetrieverTool` | `request.use_rag = True` | 混合检索 top_k 条知识库证据 |

**设计理由**：
- 必读数据源的设计保证了回答总是基于上下文，不会出现"我不知道你的身高体重"这种低质量回复
- 写入和检索按需触发，避免不必要的副作用和开销

### 2.4 食物解析 (`parse_food_items`)

```python
# 正则：中文/英文名称 2-12 字 + 数字 + 可选单位
r"([一-鿿A-Za-z]{2,12})\s*(\d+(?:\.\d+)?)\s*(?:g|克|公斤|千克)?"
```

特殊处理：
- `"公斤"/"千克"` → 克数 × 1000（统一为克）
- 过滤掉误匹配词：`"今天"、"中午"、"晚餐"` 等不是食物名的词
- 餐次推断：按关键词推断早/午/晚/加餐，默认午餐
- 最多 6 项，防止异常输入撑爆记录

### 2.5 回答生成 (`generate_answer`)

**有 API Key**：调 LLM，用 `build_prompt` 拼接完整上下文。

**Prompt 设计原则**：

| 原则 | 目的 |
|------|------|
| 优先使用用户消息中的表单数据 | 即使未保存，也要用当前页面填的数据推荐方案 |
| 显式引用具体数值 | 防止 LLM 泛泛而谈"注意饮食" |
| 只要有任何数据就不说"你还没提供" | 提升用户体验，避免知识盲区式拒绝 |
| `[1][2]` 引用格式 | 可追溯来源 |

**无 API Key 降级**：抽取式摘要，拼接已有结构化数据，直接返回文本。

### 2.6 Reflection 合规复核 (`reflect`)

两层检查：

**第一层：禁用词过滤**

```python
FORBIDDEN_TERMS = ["治疗", "治愈", "保证瘦", "一定瘦", "替代药物", "降糖药替代", "吃了就瘦"]
```

命中则替换为 `"辅助健康管理"`。

**第二层：风险提示补全**

如果回答中没有提 `"医生"`，且意图涉及营养/身体/方案，自动追加：

> 风险提示：以上内容仅用于健康科普和生活方式建议，如有基础疾病、孕期、长期失眠、进食障碍或用药情况，请咨询医生或注册营养师。

### 2.7 回合保存 (`save_turn`)

每轮对话结束后写入短期记忆，供下次对话的 `get_recent_context` 读取，实现跨轮上下文关联。

## 三、数据流全景

```
AgentChatRequest { message, session_id?, user_id, use_memory, use_rag, top_k }
    │
    ├─ session_id  →  new_session_id() 或复用前端传入
    ├─ intent      →  classify_intent(message)
    ├─ trace       →  plan(intent) → [...] → run_tools → [...] → reflect → [...]
    │
    ├─ ShortTermMemory    ─── get_recent_context(session_id)
    ├─ LongTermBodyMemory ─── trend_summary(user_id)
    ├─ HealthProfile      ─── read_json(PROFILE_FILE) → calculate_profile()
    ├─ FoodRecords        ─── list_records() → daily_summary()
    │
    ├─ (按需) BodyMetricRecorder → parse_body_metric_from_text() → add_body_metric()
    ├─ (按需) MealRecordTool     → parse_food_items() → estimate_item() → add_records()
    ├─ (按需) RAGRetrieverTool   → rag_service.retrieve(message, top_k)
    │
    ├─ generate_answer()  → OpenAI Chat Completions
    ├─ reflect()          → 合规过滤 + 风险提示补全
    ├─ save_turn()        → 写入短期记忆
    │
    ▼
AgentChatResponse { session_id, answer, intent, tools_used, sources, agent_trace, memory_used }
```

## 四、局限

### 4.1 意图分类

| 局限 | 影响 |
|------|------|
| **纯关键词匹配** | "我体重掉了5斤" 会被误判为 `body_metric_recording`（有"体重"+数字），但用户可能只是闲聊 |
| **无法处理复合意图** | "我体重72了，帮我规划减肥食谱" — 两个意图，只命中第一个 |
| **中文歧义** | "睡了" 触发 `sleep_stress_advice`，但 "睡了午觉" 可能只是叙述而非咨询 |
| **词汇表有限** | 用户说 "称了一下"（未提"体重"），或 "撸铁"（未提"运动"），无法匹配 |

### 4.2 食物解析

| 局限 | 影响 |
|------|------|
| **正则匹配整词** | "吃了番茄炒蛋 200g" → 可能只抓到"炒蛋"或整个词组，`find_food` 在 CSV 里找不到复合菜名 |
| **无烹饪方式感知** | "炸鸡排" 和 "蒸鸡胸" 营养价值差异大，但 CSV 没有对应条目时会走兜底估算 |
| **兜底估算粗糙** | 未命中的食物按 `1.2 cal/g` 估算，完全不区分高热量和低热量食物 |

### 4.3 工具执行

| 局限 | 影响 |
|------|------|
| **线性顺序，无并行** | 4 个必读操作 + 可能的写入操作串行执行 |
| **无错误恢复** | `read_json` 失败返回空列表，Agent 静默继续，可能产生错误回答 |
| **工具和 LLM 解耦** | LLM 不直接调用工具，而是由代码预先写好执行逻辑 — 不是真正的 ReAct loop |

### 4.4 真正的 ReAct 缺失

当前只是「披着 ReAct 皮」的固定流程。真正的 ReAct 是 LLM 在循环中自主决定下一步调用哪个工具、观察结果、再决定继续还是结束。这里的 `run_tools` 完全由代码硬编码了调用顺序，不涉及 LLM 的自主推理。

### 4.5 Reflection

| 局限 | 影响 |
|------|------|
| **只能用词替换** | "治愈"→"辅助健康管理" 是粗暴替换，可能导致语句不通 |
| **无语义级审核** | 不检查 LLM 是否给出了医疗建议（如推荐具体药物），只做字面匹配 |
| **风险提示机械追加** | 即使回答本身已经非常安全，也会重复追加提示，显得冗余 |

### 4.6 上下文窗口

所有上下文（会话记忆 + 档案 + 长期趋势 + 饮食记录 + 知识库证据）全部塞进一个 prompt。数据和证据量增长后（如 100+ 轮对话、50+ 条饮食记录），prompt 可能超出模型上下文窗口。

## 五、升级路线

### 第一阶段：快速改进

- [ ] **意图分类升级为 LLM 小模型**：用 `gpt-4o-mini` 做零样本分类，处理歧义和复合意图，成本极低
- [ ] **食物解析接入中文分词**：用 jieba 拆出食物名，搭配模糊匹配提高 CSV 命中率
- [ ] **兜底估算引入食物类别**：CSV 未命中时，根据食物名中的关键词（"鸡"/"鱼"/"炸"）按均值估算
- [ ] **修复复合意图**：一个消息可能触发多步操作（记录体重 + 推荐食谱），应该按顺序串行而非互斥

### 第二阶段：真正的 Agentic 架构

- [ ] **实现真正的 ReAct loop**：
  ```
  while not finished:
      thought = llm.decide(observation_history)
      action = thought.action          # llm 自主选择工具
      observation = execute(action)    # 观察结果
      history.append(observation)
  ```
- [ ] **Function Calling 替代硬编码工具**：LLM 通过 OpenAI tool calling 自主决定调用 `add_body_metric` / `estimate_food` / `rag_retrieve`，而非由 `classify_intent` 预先决定
- [ ] **多步推理**：复杂问题（"我体重上升了3斤，是不是我最近吃太多了？"）先查体重记录 → 再查饮食记录 → 对照分析 → 回答

### 第三阶段：高级能力

- [ ] **Reflection 语义审查**：用第二个 LLM 对回答做医疗合规/安全性的语义级审查，而非正则匹配
- [ ] **上下文窗口管理**：根据可用窗口大小动态调整 `SHORT_TERM_MAX_TURNS` 和 `top_k`
- [ ] **个性化记忆**：从对话中提炼用户偏好（喜欢什么食物、运动习惯），存储在长期记忆，下次推荐时自动参考
- [ ] **流式输出**：`generate_answer` 改为 streaming，让用户实时看到回答生成，降低等待感知
- [ ] **多模态输入**：支持用户拍照上传食物图片，通过视觉模型直接识别食物和估算份量

---

*最后更新：2026-06-01*
