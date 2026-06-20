import json
import logging
import re
from typing import Dict, Iterator, List, Tuple

from openai import OpenAI

from app.config import CHAT_MODEL, LLM_TEMPERATURE, LLM_TIMEOUT, OPENAI_API_KEY, OPENAI_BASE_URL
from app.schemas import AgentChatRequest, AgentChatResponse, FoodLogItem, HealthProfile
from app.services.body_metrics import add_body_metric, parse_body_metric_from_text, trend_summary
from app.services.food import add_user_records, daily_summary, estimate_item, list_records
from app.services.health import calculate_profile
from app.services.memory import get_recent_context, new_session_id, save_turn
from app.services.profile import get_profile
from app.services.rag import get_rag_service


FORBIDDEN_TERMS = ["治疗", "治愈", "保证瘦", "一定瘦", "替代药物", "降糖药替代", "吃了就瘦"]
logger = logging.getLogger(__name__)


class NutriFitAgent:
    """Plan-and-Solve + ReAct + Reflection agent for nutrition workflows."""

    def classify_intent(self, message: str) -> str:
        """基于关键词 + 数字信号的意图分类。

        优先级：身体指标记录 > 饮食记录 > 食谱推荐 > 运动建议 >
                睡眠压力 > 合规检查 > 营养问答 > 通用问答
        """
        # ── 文本预处理：全角→半角、去标点、小写 ──
        _FULL_TO_HALF = str.maketrans(
            "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ",
            "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        )
        cleaned = (message or "").translate(_FULL_TO_HALF).lower()
        cleaned = re.sub(r"[，。！？、；：""''（）【】《》\s]+", "", cleaned)
        has_digit = bool(re.search(r"\d", cleaned))

        # ── 否定词拦截：含否定词直接落通用问答 ──
        _NEGATIONS = ["不想", "不要", "不用", "不记录", "不记", "别记", "算了", "没事"]
        if any(w in cleaned for w in _NEGATIONS):
            return "general_health_qa"

        # ── ① 身体指标记录（关键词 + 数字双重条件） ──
        _BODY_KEYS = [
            "体重", "腰围", "体脂", "步数", "睡眠", "睡了",
            "称", "秤", "多重", "几斤", "多少斤", "bmi", "围度",
            "体脂率", "身材", "体型", "三围", "胸围", "臀围",
        ]
        if any(w in cleaned for w in _BODY_KEYS) and has_digit:
            return "body_metric_recording"

        # ── ② 饮食记录（关键词 + 数字双重条件） ──
        _MEAL_LOG_KEYS = [
            "吃了", "记录", "早餐", "午餐", "晚餐", "加餐", "下午茶", "夜宵",
            "喝了", "摄入", "进食", "吃了什么", "记一下", "打卡",
        ]
        if any(w in cleaned for w in _MEAL_LOG_KEYS) and has_digit:
            return "meal_logging"

        # ── ③ 食谱推荐 ──
        _MEAL_PLAN_KEYS = [
            "食谱", "一周", "安排", "搭配", "方案", "推荐", "计划",
            "菜谱", "怎么吃", "吃什么", "三餐", "每餐", "营养餐",
        ]
        if any(w in cleaned for w in _MEAL_PLAN_KEYS):
            return "meal_plan"

        # ── ④ 运动建议 ──
        _EXERCISE_KEYS = [
            "运动", "跑步", "力量", "训练", "有氧", "健身",
            "练", "举铁", "hiit", "哑铃", "深蹲", "核心", "拉伸",
            "增肌", "塑形", "燃脂运动", "体能", "间歇",
        ]
        if any(w in cleaned for w in _EXERCISE_KEYS):
            return "exercise_advice"

        # ── ⑤ 睡眠压力 ──
        _SLEEP_STRESS_KEYS = [
            "睡眠", "熬夜", "压力", "暴食",
            "失眠", "睡不着", "焦虑", "困", "疲劳", "精力差",
            "情绪", "抑郁", "emo", "心情", "烦躁", "没精神",
        ]
        if any(w in cleaned for w in _SLEEP_STRESS_KEYS):
            return "sleep_stress_advice"

        # ── ⑥ 合规检查 ──
        _COMPLIANCE_KEYS = [
            "话术", "能不能说", "合规", "宣传", "广告", "合法",
            "违规", "审核", "禁用", "敏感词",
        ]
        if any(w in cleaned for w in _COMPLIANCE_KEYS):
            return "compliance_check"

        # ── ⑦ 营养问答 ──
        _NUTRITION_KEYS = [
            "热量", "减脂", "减肥", "蛋白质", "轻食", "控糖",
            "碳水", "脂肪", "瘦身", "卡路里", "营养", "膳食",
            "低卡", "低脂", "代餐", "升糖", "gi值", "代谢",
            "减重", "掉秤", "燃脂", "刷脂",
        ]
        if any(w in cleaned for w in _NUTRITION_KEYS):
            return "nutrition_qa"

        # ── ⑧ 兜底 ──
        return "general_health_qa"

    def plan(self, intent: str) -> List[Dict]:
        steps = [
            {"stage": "Plan", "message": f"识别用户意图：{intent}"},
            {"stage": "Plan", "message": "读取会话上下文、身体档案、长期健康档案、今日饮食记录和知识库证据。"},
        ]
        if intent == "body_metric_recording":
            steps.append(
                {"stage": "Plan", "message": "从文本中抽取体重、腰围、体脂、睡眠、步数等指标，并写入长期健康档案。"})
        elif intent == "meal_logging":
            steps.append(
                {"stage": "Plan", "message": "解析饮食文本，估算热量与营养素，并写入热量记录。"})
        elif intent == "meal_plan":
            steps.append(
                {"stage": "Plan", "message": "结合当前页面表单、已保存档案、长期趋势、今日饮食和 RAG 证据生成个性化方案。"})
        else:
            steps.append(
                {"stage": "Plan", "message": "调用 RAG 检索健康知识库，生成带证据的专业回答。"})
        steps.append(
            {"stage": "Plan", "message": "执行 Reflection，检查合规性、可执行性和风险提示。"})
        return steps

    def parse_food_items(self, message: str) -> List[FoodLogItem]:
        items = []
        # re.findall 批量找出所有名称 + 重量的组合，返回一个列表，例如:[("米饭", "150"), ("鸡胸肉", "200")]
        for name, grams in re.findall(r"([\u4e00-\u9fffA-Za-z]{2,12})\s*(\d+(?:\.\d+)?)\s*(?:g|克|公斤|千克)?", message):
            value = float(grams)
            if "公斤" in message or "千克" in message:
                value *= 1000
            if name not in {"今天", "中午", "晚上", "早上", "早餐", "午餐", "晚餐", "体重", "腰围", "体脂"}:
                meal = "午餐"
                if "早餐" in message or "早上" in message:
                    meal = "早餐"
                elif "晚餐" in message or "晚上" in message:
                    meal = "晚餐"
                elif "加餐" in message:
                    meal = "加餐"
                items.append(FoodLogItem(food_name=name,
                             grams=value, meal_type=meal))
        return items[:6]

    def load_profile_context(self, user_id: str = "default_user") -> Dict:
        saved_profile = get_profile(user_id)
        if saved_profile:
            try:
                profile = HealthProfile(**saved_profile)
                return {"profile": saved_profile, "analysis": calculate_profile(profile)}
            except Exception:
                return {"profile": saved_profile, "analysis": None}
        default_profile = HealthProfile()
        return {"profile": None, "analysis": calculate_profile(default_profile)}

    def load_food_context(self, user_id: str = "default_user") -> Dict:
        records = list_records(user_id)
        return {
            "today_summary": daily_summary(user_id),
            "recent_records": records[-8:],
        }

    def run_tools(self, intent: str, request: AgentChatRequest, session_id: str) -> Tuple[List[str], List[Dict], Dict, List[Dict]]:
        """
        根据用户意图执行工具调用，读取记忆/档案/饮食记录，检索RAG
        """
        trace = []  # 展示工具调用的思考过程和结果
        tools_used = []  # 本轮使用的工具列表
        sources: List[Dict] = []  # 召回的知识库证据列表
        tool_state: Dict = {}  # 工具执行后的结构化状态，后面构建prompt时会用到

        short_context = get_recent_context(session_id, request.user_id) if request.use_memory else {
            "summary": "", "turns": []}   # 短期会话记忆
        long_memory = trend_summary(request.user_id)  # 长期健康指标趋势
        profile_context = self.load_profile_context(request.user_id)  # 用户身体档案和健康计算结果
        food_context = self.load_food_context(request.user_id)  # 今日饮食汇总和最近饮食记录

        tools_used.extend(["ShortTermMemory", "LongTermBodyMemory",
                          "HealthProfileReader", "FoodRecordReader"])  # 记录上面记忆使用过的工具

        # 类似React中的思考过程展示，说明为什么需要调用这些工具，以及工具调用后的观察结果
        trace.append({
            "stage": "ReAct",
            "thought": "方案推荐需要同时读取当前会话、身体档案、长期身体趋势和今日饮食记录。",
            "action": "ReadUserContext",
            "observation": (
                f"会话 {len(short_context['turns'])} 轮；"
                f"长期身体记录 {len(long_memory['records'])} 条；"
                f"今日饮食记录 {food_context['today_summary'].get('record_count', 0)} 条。"
            ),
        })

        # 保存工具调用后的状态，供后续生成回答时使用
        tool_state["short_context"] = short_context
        tool_state["long_memory"] = long_memory
        tool_state["profile_context"] = profile_context
        tool_state["food_context"] = food_context

        metric_record = parse_body_metric_from_text(
            request.message, request.user_id)
        # 如果用户意图是身体指标记录，解析出身体指标后写入长期记忆
        if intent == "body_metric_recording" and metric_record:
            saved = add_body_metric(metric_record)
            tools_used.append("BodyMetricRecorder")
            tool_state["saved_metric"] = saved
            trace.append({
                "stage": "ReAct",
                "thought": "用户提供了身体指标。",
                "action": "BodyMetricRecorder",
                "observation": "已写入长期健康档案。",
            })

        """
        如果用户意图是饮食记录，
        解析文本中的食物和克数(parse_food_items)，估算热量和营养元素(estimate_item)，
        并写入饮食记录(add_records)"""
        if intent == "meal_logging":
            items = self.parse_food_items(request.message)
            if items:
                records = [estimate_item(item, source="agent_text")
                           for item in items]
                add_user_records(records, request.user_id)
                tools_used.append("MealRecordTool")
                tool_state["meal_records"] = [record.model_dump()
                                              for record in records]
                # 更新饮食上下文以反映新记录
                tool_state["food_context"] = self.load_food_context(request.user_id)
                trace.append({
                    "stage": "ReAct",
                    "thought": "用户提供了饮食文本。",
                    "action": "MealRecordTool",
                    "observation": f"已记录 {len(records)} 个食物。",
                })

        # 如果启用RAG，走完整检索管道：改写 → 多路召回 → 精排
        if request.use_rag:
            rag_service = get_rag_service()
            search_query = rag_service._rewrite_query(request.message)
            coarse_sources = rag_service.retrieve(search_query, top_k=15)
            sources = rag_service.rerank(search_query, coarse_sources, top_k=3)
            tools_used.extend(["RAGRetrieverTool", "RAGRerankTool"])
            trace.append({
                "stage": "ReAct",
                "thought": "需要知识库证据支撑回答，先改写查询再检索。",
                "action": "RAGRetrieverTool → RAGRerankTool",
                "observation": f"改写为「{search_query}」，粗排召回 {len(coarse_sources)} 条，精排后保留 {len(sources)} 条。",
            })
        # 登记合规检查工具（必须），但是真正的执行是后面的 reflect()里面
        tools_used.append("ComplianceGuardTool")
        return tools_used, sources, tool_state, trace

    # 根据工具调用的结果构建生成回答的prompt，要求直接给结论，并说明依据和原因，给出饮食、运动、睡眠和记录建议，以及风险提示和适用边界。回答中必须使用 [1][2] 形式引用知识库证据。
    def build_prompt(self, request: AgentChatRequest, intent: str, sources: List[Dict], tool_state: Dict) -> str:
        short_turns = tool_state.get("short_context", {}).get("turns", [])
        short_text = "\n".join(
            [f"用户：{x.get('user')}\n助手：{x.get('assistant')}" for x in short_turns[-4:]])
        long_memory = tool_state.get("long_memory", {})
        profile_context = tool_state.get("profile_context", {})
        food_context = tool_state.get("food_context", {})
        source_text = "\n\n".join(
            [f"[{i}] {src['source']} | {src['domain']}\n{src['content']}" for i, src in enumerate(sources, 1)])
        saved_metric = tool_state.get("saved_metric")
        meal_records = tool_state.get("meal_records")

        return f"""你是 NutriFit AI 的专业健康体重管理 Agent。你遵循 Plan-and-Execute + ReAct + Reflection 工作流：先理解目标，再基于工具结果回答，最后进行健康合规复核。

非常重要：
1. 下面的“用户消息”中可能包含前端当前表单里的身体数据，即使这些数据还没有点击保存，也必须用于方案推荐。
2. 如果用户消息、已保存身体档案、长期健康档案之间存在差异，优先使用“用户消息中的当前页面表单数据”，并在回答中说明“以下基于当前页面填写的数据估算”。
3. 生成方案时必须显式参考已有的身高、体重、腰围、体脂率、睡眠时长、步数、运动分钟、活动水平、推荐热量、今日饮食记录等数据。
4. 只要任意来源里有身体或饮食数据，就不要说“你还未提供身体数据”。

专业表达要求：
1. 先给直接结论。
2. 再说明依据和原因。
3. 给出饮食、运动、睡眠和记录建议。
4. 给出风险提示和适用边界。
5. 使用 [1][2] 形式引用知识库证据。
6. 不做医疗诊断，不承诺治疗，不承诺快速瘦身。

用户意图：{intent}

用户消息：
{request.message}

已保存身体档案：
{profile_context.get("profile") or "暂无已保存身体档案"}

已保存身体档案计算结果：
{profile_context.get("analysis") or "暂无"}

长期健康档案摘要：
{long_memory.get("summary", "暂无")}

长期健康档案最新记录：
{long_memory.get("latest") or "暂无"}

长期健康档案记录数：
{len(long_memory.get("records", []))}

今日饮食汇总：
{food_context.get("today_summary") or "暂无"}

最近饮食记录：
{food_context.get("recent_records") or "暂无"}

短期会话上下文：
{short_text or "暂无"}

本轮自动写入的身体指标：
{saved_metric or "无"}

本轮自动写入的饮食：
{meal_records or "无"}

检索证据：
{source_text or "无"}
"""

    # 根据 run_tools() 准备好的工具结果，生成最终给用户看的回答（初稿）
    def generate_answer(self, request: AgentChatRequest, intent: str, sources: List[Dict], tool_state: Dict) -> str:
        if OPENAI_API_KEY:
            client = OpenAI(api_key=OPENAI_API_KEY, base_url=OPENAI_BASE_URL)
            response = client.chat.completions.create(
                model=CHAT_MODEL,
                temperature=LLM_TEMPERATURE,
                timeout=LLM_TIMEOUT,
                messages=[
                    {
                        "role": "system",
                        "content": "你是专业、审慎、合规的健康体重管理顾问。必须优先使用用户当前提供的身体数据和饮食记录。",
                    },
                    {"role": "user",
                        "content": self.build_prompt(request, intent, sources, tool_state)
                     },
                ],
            )
            if not getattr(response, "choices", None):
                raise RuntimeError("模型返回为空：choices 为空")
            return response.choices[0].message.content

        # 没有配置API key时的兜底回答（fallback 机制）
        profile_context = tool_state.get("profile_context", {})
        food_context = tool_state.get("food_context", {})
        long_memory = tool_state.get("long_memory", {})
        lines = ["结论：模型生成暂不可用，系统先基于当前身体数据、饮食记录和知识库证据给出抽取式建议。"]
        lines.append(f"当前页面数据已接收：{request.message[:800]}")
        lines.append(
            f"已读取身体档案：{profile_context.get('profile') or '暂无已保存档案，请以前端当前填写数据为准。'}")
        lines.append(f"健康指标估算：{profile_context.get('analysis') or '暂无'}")
        lines.append(f"长期趋势：{long_memory.get('summary', '暂无')}")
        lines.append(f"今日饮食：{food_context.get('today_summary') or '暂无'}")
        if tool_state.get("saved_metric"):
            lines.append(f"已记录身体指标：{tool_state['saved_metric']}")
        if tool_state.get("meal_records"):
            lines.append(f"已记录饮食：{len(tool_state['meal_records'])} 项。")
        for idx, src in enumerate(sources[:4], 1):
            lines.append(f"{idx}. {src['content'][:180]}... [{idx}]")
        lines.append("风险提示：以上为健康科普和生活方式建议，特殊疾病、孕期或进食障碍风险请咨询医生或注册营养师。")
        return "\n".join(lines)

    def _fallback_answer_lines(self, request: AgentChatRequest, sources: List[Dict], tool_state: Dict) -> List[str]:
        profile_context = tool_state.get("profile_context", {})
        food_context = tool_state.get("food_context", {})
        long_memory = tool_state.get("long_memory", {})
        lines = ["结论：模型生成暂不可用，系统先基于当前身体数据、饮食记录和知识库证据给出抽取式建议。"]
        lines.append(f"当前页面数据已接收：{request.message[:800]}")
        lines.append(
            f"已读取身体档案：{profile_context.get('profile') or '暂无已保存档案，请以前端当前填写数据为准。'}")
        lines.append(f"健康指标估算：{profile_context.get('analysis') or '暂无'}")
        lines.append(f"长期趋势：{long_memory.get('summary', '暂无')}")
        lines.append(f"今日饮食：{food_context.get('today_summary') or '暂无'}")
        if tool_state.get("saved_metric"):
            lines.append(f"已记录身体指标：{tool_state['saved_metric']}")
        if tool_state.get("meal_records"):
            lines.append(f"已记录饮食：{len(tool_state['meal_records'])} 项。")
        for idx, src in enumerate(sources[:4], 1):
            lines.append(f"{idx}. {src['content'][:180]}... [{idx}]")
        lines.append("风险提示：以上为健康科普和生活方式建议，特殊疾病、孕期或进食障碍风险请咨询医生或注册营养师。")
        return lines

    def _memory_used(self, tool_state: Dict) -> Dict:
        return {
            "short_term": bool(tool_state.get("short_context", {}).get("turns")),
            "long_term_records": len(tool_state.get("long_memory", {}).get("records", [])),
            "profile_loaded": bool(tool_state.get("profile_context", {}).get("profile")),
            "food_records": tool_state.get("food_context", {}).get("today_summary", {}).get("record_count", 0),
        }

    def _stream_event(self, event: Dict) -> str:
        return json.dumps(event, ensure_ascii=False, default=str) + "\n"

    def stream_chat(self, request: AgentChatRequest) -> Iterator[str]:
        session_id = request.session_id or new_session_id()
        yield self._stream_event({
            "type": "status",
            "message": "已收到请求，正在识别目标...",
            "session_id": session_id,
        })

        intent = self.classify_intent(request.message)
        trace = self.plan(intent)
        yield self._stream_event({
            "type": "status",
            "message": "正在读取身体档案、饮食记录和长期趋势...",
            "session_id": session_id,
            "intent": intent,
            "agent_trace": trace,
        })

        if request.use_rag:
            yield self._stream_event({
                "type": "status",
                "message": "正在检索知识库证据...",
                "session_id": session_id,
                "intent": intent,
            })

        tools_used, sources, tool_state, react_trace = self.run_tools(
            intent, request, session_id)
        trace.extend(react_trace)

        yield self._stream_event({
            "type": "meta",
            "session_id": session_id,
            "intent": intent,
            "tools_used": tools_used,
            "sources": sources,
            "agent_trace": trace,
        })
        yield self._stream_event({
            "type": "status",
            "message": "证据准备完成，正在流式生成方案...",
            "session_id": session_id,
        })

        answer_parts: List[str] = []
        try:
            if OPENAI_API_KEY:
                try:
                    client = OpenAI(api_key=OPENAI_API_KEY,
                                    base_url=OPENAI_BASE_URL)
                    stream = client.chat.completions.create(
                        model=CHAT_MODEL,
                        temperature=LLM_TEMPERATURE,
                        timeout=LLM_TIMEOUT,
                        stream=True,
                        messages=[
                            {
                                "role": "system",
                                "content": "你是专业、审慎、合规的健康体重管理顾问。必须优先使用用户当前提供的身体数据和饮食记录。",
                            },
                            {"role": "user",
                             "content": self.build_prompt(request, intent, sources, tool_state)
                             },
                        ],
                    )
                    for chunk in stream:
                        if not getattr(chunk, "choices", None):
                            continue
                        delta = chunk.choices[0].delta
                        content = (
                            getattr(delta, "content", None)
                            or getattr(delta, "reasoning_content", None)
                            or ""
                        )
                        if not content:
                            continue
                        answer_parts.append(content)
                        yield self._stream_event({"type": "chunk", "content": content})
                except Exception:
                    logger.exception("Agent 流式生成失败，降级为抽取式建议")
                    answer_parts = []
                    for line in self._fallback_answer_lines(request, sources, tool_state):
                        content = line + "\n"
                        answer_parts.append(content)
                        yield self._stream_event({"type": "chunk", "content": content})
            else:
                for line in self._fallback_answer_lines(request, sources, tool_state):
                    content = line + "\n"
                    answer_parts.append(content)
                    yield self._stream_event({"type": "chunk", "content": content})

            answer = "".join(answer_parts)
            answer, reflection_trace = self.reflect(answer, intent)
            trace.extend(reflection_trace)
            save_turn(session_id, request.message, answer, intent, tools_used, request.user_id)
            yield self._stream_event({
                "type": "final",
                "session_id": session_id,
                "intent": intent,
                "answer": answer,
                "sources": sources,
                "tools_used": tools_used,
                "agent_trace": trace,
                "memory_used": self._memory_used(tool_state),
            })
        except Exception as exc:
            yield self._stream_event({
                "type": "error",
                "message": f"流式生成失败：{exc.__class__.__name__}",
            })

    def reflect(self, answer: str, intent: str) -> Tuple[str, List[Dict]]:
        trace = []
        revised = answer
        hit_terms = [term for term in FORBIDDEN_TERMS if term in revised]
        if hit_terms:
            for term in hit_terms:
                revised = revised.replace(term, "辅助健康管理")
            trace.append({"stage": "Reflection", "check": "合规词检查",
                          "observation": f"替换潜在风险表达：{hit_terms}"})
        else:
            trace.append({"stage": "Reflection", "check": "合规词检查",
                         "observation": "未发现明显医疗化或绝对化表达。"})
        if "医生" not in revised and intent in {"nutrition_qa", "body_metric_recording", "sleep_stress_advice", "meal_plan"}:
            revised += "\n\n风险提示：以上内容仅用于健康科普和生活方式建议，如有基础疾病、孕期、长期失眠、进食障碍或用药情况，请咨询医生或注册营养师。"
            trace.append({"stage": "Reflection", "check": "风险提示",
                         "observation": "已补充专业边界提示。"})
        return revised, trace

    def chat(self, request: AgentChatRequest) -> AgentChatResponse:
        """
        用户请求过来后，Agent 会经历以下步骤：
        1. 生成session_id（如果前端没有传过来），并识别用户意图。
        2. 制定计划，明确需要调用哪些工具和数据源。
        3. 执行工具调用，获取身体档案、长期健康趋势、今日饮食记录和知识库证据等信息。
        4. 基于工具结果生成回答，要求直接给结论，并说明依据和原因，给出饮食、运动、睡眠和记录建议，以及风险提示和适用边界。回答中必须使用 [1][2] 形式引用知识库证据。
        5. 执行 Reflection，检查回答中是否存在医疗化、绝对化的表达，如果有则进行替换，并补充专业边界和风险提示。
        6. 保存本轮对话记录，包括用户消息、生成的回答、识别的意图和使用的工具等信息，以供后续会话上下文调用和长期趋势分析使用。
        """
        session_id = request.session_id or new_session_id()
        intent = self.classify_intent(request.message)
        trace = self.plan(intent)
        tools_used, sources, tool_state, react_trace = self.run_tools(
            intent, request, session_id)

        trace.extend(react_trace)
        answer = self.generate_answer(request, intent, sources, tool_state)
        answer, reflection_trace = self.reflect(answer, intent)
        trace.extend(reflection_trace)

        # 事实溯源校验（复用 RAG 管道的能力）
        fact_check = get_rag_service()._fact_check(answer, sources) if request.use_rag else {
            "verified_answer": answer, "suspicious_spans": [], "checked": False}
        if fact_check.get("checked"):
            answer = fact_check["verified_answer"]
            if fact_check.get("suspicious_spans"):
                trace.append({
                    "stage": "FactCheck",
                    "check": "事实溯源校验",
                    "observation": f"标记 {len(fact_check['suspicious_spans'])} 处可疑表述。",
                })

        save_turn(session_id, request.message, answer, intent, tools_used, request.user_id)
        return AgentChatResponse(
            session_id=session_id,
            intent=intent,
            answer=answer,
            sources=sources,
            tools_used=tools_used,
            agent_trace=trace,
            memory_used=self._memory_used(tool_state),
        )


agent_service = NutriFitAgent()
agent = agent_service
