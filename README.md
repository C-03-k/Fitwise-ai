# NutriFit AI 健康轻食营养顾问平台

> Vue3 + FastAPI + RAG + 食物图片识别 + 饮食记录 + 健康数据看板的企业级健康轻食项目。

## 项目定位

NutriFit AI 是一个面向健康食品品牌、轻食门店、营养客服和体重管理场景的企业级 AI 应用。系统支持健康档案计算、饮食热量记录、上传食物图片识别热量、RAG 营养问答、引用证据追溯和数据驾驶舱。

项目只提供健康科普和饮食建议，不提供医疗诊断或治疗建议。

## 核心功能

- BMI / BMR / TDEE 计算
- 推荐热量与蛋白质区间
- 常见食物营养库
- 手动饮食记录
- 上传食物图片识别并估算热量
- AI 营养顾问问答
- RAG 引用证据表
- Vue3 企业级数据驾驶舱
- FastAPI 后端接口
- YAML 配置中心
- Plan-and-Solve + ReAct + Reflection Agent
- 短期会话记忆
- 长期身体指标记忆
- 相对路径与 `.env.example` 隐私规范

## 目录结构

```text
NutriFit_AI_健康轻食营养顾问平台/
├── backend/
│   ├── app/
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   ├── src/
│   ├── package.json
│   └── vite.config.ts
├── data/
│   ├── knowledge_base/
│   ├── food_database/
│   └── recipes/
└── docs/
```

## 后端启动

```bash
cd NutriFit_AI_健康轻食营养顾问平台/backend
conda activate nutrifit
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8008 --reload
```

## 前端启动

```bash
cd NutriFit_AI_健康轻食营养顾问平台/frontend
npm install
npm run dev
```

访问：

```text
http://127.0.0.1:5175
```

## API Key 配置

复制：

```text
backend/.env.example
```

创建 `.env` 或在系统环境变量中配置：

```text
OPENAI_API_KEY=your_api_key_here
OPENAI_BASE_URL= https://dashscope.aliyuncs.com/compatible-mode/v1
NUTRIFIT_CHAT_MODEL= your_model_here
NUTRIFIT_VISION_MODEL= your_model_here
NUTRIFIT_RERANK_MODEL= your_model_here
NUTRIFIT_EMBEDDING_MODEL= your_model_here
```

未配置 API Key 时，RAG 问答会返回抽取式摘要，图片识别会使用默认估算，不影响项目演示。

## YAML 配置中心

后端启动时读取：

```text
backend/config/config.yaml
```

该文件集中管理 API Key、Base URL、模型名称、RAG 参数、记忆参数、隐私开关和功能开关。环境变量优先级高于 YAML。

## Agent 模式

Agent 采用混合编排：

```text
Plan-and-Solve：先识别意图并制定计划
ReAct：按计划调用会话上下文、长期健康档案、RAG、身体指标记录、饮食记录等工具
Reflection：检查回答合规性、专业性和风险提示
```

前端在 `身体管理` 页面以折叠面板展示 AI 分析过程，普通用户看到的是方案推荐结果。

## 记忆能力

短期记忆保存当前会话最近多轮对话：

```text
backend/storage/memory/short_term
```

长期健康档案保存体重、腰围、体脂率、睡眠、步数和运动分钟：

```text
backend/storage/memory/long_term
```
