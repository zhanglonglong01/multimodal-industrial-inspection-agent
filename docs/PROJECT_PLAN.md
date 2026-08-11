# 多模态工业设备智能巡检与故障诊断 Agent：项目计划

> English name: Multimodal Industrial Inspection & Fault Diagnosis Agent  
> 当前状态：Phase 0-5 已完成，形成 Portfolio MVP 1.0.0 候选版本；尚未创建 Git tag，外部 CI、CI Docker smoke 与真实 Vision live smoke 的状态以发布门禁记录为准。
> 项目定位：面向计算机硕士校招简历与 GitHub 展示的个人工程实践，不宣称真实工厂部署、商业效果或未经验证的准确率。

## 1. 项目背景

工业设备巡检的信息天然分散：现场人员看到的是漏液、锈蚀、松动、异常颜色等外观迹象；监控系统保存的是温度、压力、电流和振动等时序；维修判断还需要故障模式、设备说明书和 SOP。单一模型通常只能覆盖其中一类数据，也难以把分析结果转成可追踪的维修动作。

本项目计划实现一个小而完整的交互应用。它使用模拟设备、合成传感器数据、可合法分发的示例图片和自编维修资料，在没有真实工厂接入的情况下演示以下闭环：

```text
选择设备 -> 上传巡检图片 -> 获取历史传感器 -> 视觉/时序分析
-> 故障模式与维修知识检索 -> 证据融合诊断
-> 风险分级 -> 人工确认 -> 创建维修工单
```

重点不在“模型越多越好”，而在于边界、证据、可恢复工作流、确定性算法、测试和可演示性。

## 2. 用户场景

### 2.1 主要用户

- 模拟的现场巡检人员：选择设备并上传当次巡检图片。
- 模拟的维修工程师：查看视觉发现、传感器曲线、故障候选和文档证据。
- 模拟的维修主管：对高风险工单建议批准、拒绝或要求修改。

这些是产品角色，不暗示项目已服务真实工厂或真实企业用户。

### 2.2 核心故事

以 `PUMP-001` 离心泵为例：巡检人员上传一张泵体附近存在液体痕迹的图片。系统读取同期的振动、轴承温度和出口压力历史，确定性算法发现振动升高及压力下滑区间。Failure Mode 模块返回“密封泄漏、气蚀、轴承磨损”等受控候选，RAG 返回泵说明书和泄漏检查 SOP 的具体章节。Diagnosis Agent 依据每条 evidence ID 排序候选，输出疑似故障、可能原因、不确定性、风险和建议。若策略判定为高风险，工作流暂停；主管批准后，WorkOrder Tool 才能创建带证据来源的工单。

### 2.3 异常与降级场景

- 图片格式非法或图片质量不足：停止视觉分支并提示重新上传。
- 某传感器缺失或时窗没有数据：保留数据质量警告，不让 LLM 填补数值。
- Vision provider 没有 API Key：demo fixture 模式仍可运行，但 UI 明确标记为预置结果。
- RAG 没有足够相关证据：诊断报告必须标记“知识证据不足”。
- 视觉和传感器都不可用：不生成确定性故障结论，不创建工单。
- 审批被拒绝：保存诊断和拒绝原因，禁止工单写入。

## 3. 要解决的问题

1. **多源证据无法统一**：把图片发现、时序异常、故障模式和文档片段规范成可引用 Evidence。
2. **LLM 容易替代算法猜数值**：异常检测、风险矩阵、权限与工单写入使用确定性代码。
3. **答案缺乏可追踪性**：每个诊断候选必须引用 evidence ID，工单反向关联诊断、审批和 workflow run。
4. **高风险副作用不安全**：高风险工单通过 LangGraph interrupt 暂停，并在写工具内部再次验证审批。
5. **Demo 依赖真实工业系统**：提供自包含模拟资产、传感器、图片 fixture、故障库和维修文档。
6. **项目容易变成聊天壳**：用显式工作流、领域服务、Tool schema、持久化和评测形成工程闭环。

## 4. 为什么使用 Agent

这个任务包含动态路由而不是固定的一次模型调用：

- 不同设备拥有不同传感器、故障模式和知识文档。
- 图片和时序分支可能独立成功或失败，需要按证据可用性降级。
- 诊断查询需要根据前一步发现动态构造，而不是预先写死。
- 风险结果决定是否暂停等待人工输入。
- 工具结果、失败和审批状态需要在多个步骤间持续保存。

LangGraph 适合表达“有状态、可分支、可暂停、可恢复”的执行图。Agent 的职责限定为：选择工具、生成查询、综合多模态证据、排序诊断候选并形成解释。它不直接访问数据库，不计算异常统计，不决定是否绕过审批。

如果只是固定地顺序执行所有步骤，普通 application service 足够；本项目使用 Agent 的理由必须通过动态降级、证据驱动检索、条件路由和 Human-in-the-loop 体现，而不是为了在名称里加入 Agent。

## 5. 为什么不是普通 RAG Chatbot

普通 RAG Chatbot 的主路径通常是“问题 -> 检索文本 -> 生成回答”。本项目还必须：

- 接受并管理图片 artifact。
- 查询设备上下文和时序历史。
- 运行确定性异常检测并生成异常区间。
- 将视觉/时序结果映射到受控 failure mode 候选。
- 用检索文档支撑维修建议，而不是只回答文档问答。
- 执行风险策略和持久化审批状态。
- 通过有副作用的 Tool 创建工单并保证幂等。
- 保存完整轨迹，用场景和确定性断言评测。

RAG 只是证据来源之一，不能代表整个系统。

## 6. 项目目标与非目标

### 6.1 目标

- 一条在本地可复现、可视化、可测试的多模态巡检闭环。
- 明确区分观察、算法结果、检索证据、Agent 推断和人工决定。
- 默认离线 demo 可运行；配置真实 provider 后可调用多模态模型。
- 使用 LangGraph 实现可恢复审批，而不是伪造流程图。
- 使用 FastAPI 暴露 API，并提供轻量 Web Dashboard。
- 提供 pytest 与小型 scenario evaluation，不虚构测试结果。

### 6.2 非目标

- 不控制真实设备、PLC、SCADA 或 Maximo。
- 不提供安全认证、合规认证或生产 SLA。
- 不声称替代维修工程师或给出安全保证。
- 不在 MVP 支持任意设备类别、视频流、实时流处理或多租户。
- 不在 MVP 训练视觉模型、时序 foundation model 或 embedding model。
- 不复刻 AssetOpsBench 的 leaderboard、模型目录或大规模场景平台。

## 7. MVP 范围

### 7.1 Portfolio MVP 必须包含

1. 两类 demo 设备：离心泵与感应电机，每类至少一个资产实例。
2. 资产详情与已安装传感器展示。
3. 巡检图片上传、校验、哈希和 artifact 记录。
4. Vision provider 抽象：`fixture` 离线模式与一个真实多模态 provider adapter。
5. 时序曲线和确定性异常检测：operating-limit rule + rolling median/MAD。
6. 数据质量报告：缺失率、时间范围、采样间隔与不可用原因。
7. 受控 failure mode 库，包含症状、关联传感器、影响和来源。
8. 基于 FAISS 的小型维修知识库，回答返回文档/章节/chunk 引用。
9. LangGraph 证据融合诊断，输出故障候选、原因、风险、建议和不确定性。
10. 确定性风险矩阵，不由 LLM 单独决定最终 risk level。
11. 高风险路径的 Human-in-the-loop interrupt、审批记录与恢复。
12. WorkOrder draft 与受保护的 create tool；工单关联诊断和 evidence。
13. FastAPI + server-rendered Dashboard，展示曲线、证据卡片、审批和工单状态。
14. Docker 一键启动、`.env.example`、无密钥默认 demo。
15. pytest 单元/集成测试与小型端到端 scenario evaluation。

### 7.2 Demo 数据集

计划先提供三个演示案例：

| 场景 | 视觉证据 | 传感器证据 | 预期系统行为 |
| --- | --- | --- | --- |
| 泵密封泄漏 | 泵体下方液体痕迹 | 压力下降、振动升高 | 排序“密封泄漏”为主要候选，检索泄漏 SOP，进入高风险审批样例 |
| 电机轴承异常 | 轴承区域异常变色或维护标记 | 轴承温度与振动上升 | 输出轴承相关候选和停机检查建议 |
| 正常巡检 | 无明显异常 | 数据在运行范围内 | 明确“未发现足够异常证据”，不自动生成高风险工单 |

所有 demo 资产、故障注入和文档会标记为 synthetic/demo。fixture vision 只对配套样例返回预置标注，不能伪装成通用模型推理。

### 7.3 MVP 成功定义

Portfolio MVP 不是以某个虚构准确率验收，而以可重复行为验收：从干净环境启动后，用户能完成至少两个不同结局的端到端案例；所有关键中间证据可见；高风险写入无法绕过审批；同一 create 请求不会产生重复工单；评测命令能生成真实报告。

## 8. 技术选择与理由

| 选择 | MVP 决策 | 为什么 |
| --- | --- | --- |
| Python | 采用 | 数据分析、LLM 生态、FastAPI/LangGraph/pytest 一致 |
| FastAPI | 采用 | 类型化 API、文件上传、异步 provider 调用和 OpenAPI |
| LangGraph | 采用 | 条件路由、checkpoint、interrupt/resume 与可观察节点 |
| Pydantic | 采用 | API、Tool、Agent state 和 provider 输出的统一 schema |
| SQLite | 采用 | 单机 demo 零运维，足够保存元数据、诊断、审批和工单 |
| Parquet/CSV | 采用 | 时序数据更适合列式文件；SQLite 只保存 dataset 引用 |
| FAISS | 采用 | 小型本地知识库无需额外服务；Qdrant 留作扩展 |
| Jinja2 + HTMX + Plotly.js | 采用 | 单仓库、少量前端构建成本，仍能完成可交互 Dashboard |
| MCP | MVP 核心路径不采用 | Phase 1-3 不实现；核心闭环完成后再决定是否增加可选 FastMCP gateway |
| Docker Compose | 采用 | 一键启动应用；MVP 不额外启动数据库/向量库容器 |
| PostgreSQL/Qdrant | 暂不采用 | 当前规模下只增加运维，不提高核心演示价值 |
| TSFM | 暂不采用 | 先建立可解释、可评测的确定性基线 |

## 9. 系统架构

系统分为六层，详细 Mermaid 图见 [ARCHITECTURE.md](./ARCHITECTURE.md)。

1. **Presentation**：资产选择、上传、运行状态、传感器图表、证据、诊断、审批与工单页面。
2. **API/Application**：FastAPI endpoint、用例服务、文件校验、身份占位与响应 DTO。
3. **Workflow**：LangGraph state、nodes、routing policy、checkpoint 和 resume。
4. **Domain/Tools**：Vision、Sensor、Failure Mode、Knowledge、Diagnosis、Risk、Approval、Work Order。
5. **Persistence/Providers**：SQLite、Parquet/CSV、文件 artifact、FAISS、LLM/Vision provider。
6. **Observability/Evaluation**：structured logs、tool trace、scenario runner、deterministic scorer、report。

架构上的核心约束：

- LangGraph node 只编排，不直接写 SQL 或实现算法。
- Tool adapter 只转换 schema，不复制业务逻辑。
- LLM 只接收必要的结构化摘要和 evidence，不接收无限长历史序列。
- 所有写操作都经过领域服务验证；不能只相信 prompt。

## 10. 计划中的项目结构

以下是后续 Phase 拟创建的结构，本阶段不会创建空壳代码：

```text
src/inspection_agent/
├─ api/                 # FastAPI routes 与 DTO
├─ agents/              # LangGraph state、nodes、graph、checkpoint
├─ domain/              # 领域实体、枚举、policy、异常类型
├─ services/
│  ├─ vision/           # provider interface、fixture/live adapter
│  ├─ sensors/          # 数据质量、detector、segment 聚合
│  ├─ knowledge/        # ingest、chunk、embedding、retrieval
│  ├─ diagnosis/        # 证据打包、schema-constrained synthesis
│  └─ workorders/       # draft、approval guard、create
├─ tools/               # typed tool adapters 与可选 FastMCP gateway
├─ repositories/        # SQLite、artifact、time-series、vector adapters
├─ evaluation/          # scenario runner、scorers、reports
├─ web/                 # templates 与 static assets
└─ config.py            # 环境变量与 secret-safe settings
tests/
├─ unit/
├─ integration/
└─ e2e/
data/
├─ demo/                # synthetic assets、sensor series、fixture manifest
└─ knowledge/           # 自编 demo manual/SOP
evals/scenarios/        # 小型端到端评测场景
```

## 11. Agent Workflow

### 11.1 Graph state

`InspectionState` 计划只保存 ID、状态和小型结构化结果，不把图片二进制或完整时序塞入 checkpoint：

```text
run_id, inspection_id, asset_id
image_artifact_id, sensor_dataset_id
vision_result, sensor_result
failure_mode_candidates, knowledge_evidence
diagnosis_report, risk_assessment
work_order_draft_id, approval_request_id, work_order_id
warnings, errors, tool_trace
```

### 11.2 节点与路由

1. `validate_request`：验证 asset、图片、时窗和幂等 key。
2. `load_asset_context`：读取资产、关键传感器和 operating limits。
3. 并行分支：
   - `analyze_image` 调 Vision service。
   - `load_sensor_window` + `detect_anomalies` 调确定性 sensor service。
4. `evidence_gate`：判断两个分支的可用性；全部失败则输出 evidence-insufficient。
5. `lookup_failure_modes`：按 asset type 与观察症状读取受控候选。
6. `build_retrieval_queries`：根据已观察异常生成有限、可记录的检索查询。
7. `retrieve_knowledge`：返回带来源的维修文档片段。
8. `synthesize_diagnosis`：LLM 只在提供的候选与 evidence 上排序、解释并输出 schema。
9. `apply_risk_policy`：确定性 risk matrix 结合 asset criticality、异常严重度与潜在后果映射风险。
10. `draft_work_order`：生成尚未写入正式工单表的 draft。
11. `approval_gate`：高风险/关键风险触发 LangGraph interrupt。
12. `resume_after_decision`：批准、拒绝或要求修改。
13. `create_work_order`：批准后调用受保护写 Tool；内部再次验证 approval 与 draft hash。
14. `finalize_report`：返回诊断、证据、审批/工单状态和警告。

### 11.3 失败处理

- provider 可重试错误只在节点内部有限重试，并记录次数。
- schema 解析失败不回退到未校验自由文本。
- 某分支失败可以 degraded mode 继续，但报告降低 evidence strength。
- 所有分支都失败时终止，禁止生成工单 draft。
- create tool 使用 idempotency key，重放 graph 不会重复写入。

## 12. 数据模型

### 12.1 持久化边界

- **SQLite**：资产元数据、inspection、分析摘要、diagnosis、approval、work order、workflow run、tool trace。
- **Parquet/CSV**：模拟传感器序列；SQLite 保存路径、哈希、时间范围和 schema version。
- **文件目录**：上传图片和生成 artifact，使用内容哈希和受控路径。
- **FAISS**：chunk embedding；SQLite/JSON 保存 chunk metadata，防止向量索引成为唯一事实来源。

### 12.2 主要实体

| 实体 | 关键字段 | 设计说明 |
| --- | --- | --- |
| `Asset` | `asset_id`, `name`, `asset_type`, `site`, `criticality`, `status` | demo 设备主数据 |
| `SensorDefinition` | `sensor_id`, `asset_type`, `unit`, `operating_min/max`, `sampling_interval` | 安装定义与规则阈值 |
| `SensorDataset` | `dataset_id`, `asset_id`, `uri`, `sha256`, `start/end`, `schema_version` | 指向时序文件，不在 state 复制序列 |
| `Inspection` | `inspection_id`, `asset_id`, `created_at`, `status`, `run_id` | 一次用户巡检事务 |
| `ImageArtifact` | `artifact_id`, `inspection_id`, `uri`, `mime_type`, `sha256`, `width/height` | 上传文件可追踪、可去重 |
| `VisionFinding` | `finding_id`, `label`, `observation`, `severity`, `confidence`, `region`, `provider` | 只描述可见证据，provider/fixture 明示 |
| `SensorAnalysis` | `analysis_id`, `method`, `parameters`, `quality`, `summary` | 保存实际算法和参数 |
| `AnomalySegment` | `sensor_id`, `start/end`, `peak_score`, `direction`, `severity` | 连续异常点合并，方便诊断引用 |
| `FailureMode` | `mode_id`, `asset_type`, `name`, `symptoms`, `related_sensors`, `source` | 受控候选，不声称完备 |
| `KnowledgeDocument` | `doc_id`, `title`, `version`, `source`, `sha256` | 文档版本与来源 |
| `KnowledgeChunk` | `chunk_id`, `doc_id`, `section/page`, `text`, `index_version` | RAG 引用最小单元 |
| `EvidenceRef` | `evidence_id`, `kind`, `source_id`, `summary`, `observed_at` | 跨模态统一引用 |
| `DiagnosisReport` | `diagnosis_id`, `candidates`, `causes`, `recommendations`, `uncertainty`, `evidence_ids` | LLM 输出经 schema 验证 |
| `RiskAssessment` | `severity`, `likelihood`, `asset_criticality`, `risk_level`, `policy_version` | 确定性映射，可审计 |
| `WorkOrderDraft` | `draft_id`, `diagnosis_id`, `content`, `content_hash`, `status` | 审批绑定不可变摘要 |
| `ApprovalRequest` | `approval_id`, `draft_id/hash`, `decision`, `reviewer`, `reason`, `decided_at` | 与正式工单状态分离 |
| `WorkOrder` | `work_order_id`, `draft_id`, `asset_id`, `priority`, `status`, `evidence_ids` | 最小工单模型，不复制 Maximo 全字段 |
| `WorkflowRun` | `run_id`, `graph_version`, `status`, `checkpoint_id`, `started/finished_at` | 可恢复与复评 |
| `ToolTrace` | `trace_id`, `run_id`, `tool`, `input_hash`, `status`, `duration_ms`, `error_code` | 不记录 secret 或图片二进制 |

### 12.3 关键约束

- `EvidenceRef.source_id` 必须指向已保存的 finding、segment 或 chunk。
- Diagnosis 引用不存在的 evidence ID 时 schema/domain validation 失败。
- Approval 必须绑定 `draft_id + content_hash`，draft 修改后旧批准失效。
- `WorkOrder.draft_id` 唯一，防止一次批准创建多张工单。
- 所有时间存 UTC，UI 按本地时区显示。

## 13. Tool 设计

### 13.1 通用约定

所有 Tool 使用 Pydantic schema，并返回统一元数据：

```text
schema_version, trace_id, status, data, warnings, error
```

`error` 至少包含 `code`、`message`、`retryable`；日志和输出都不得包含 API Key。大结果返回 artifact/result ID，不返回完整文件。

### 13.2 MVP Tool 清单

| Tool | 类型 | 核心输入 | 核心输出 | 为什么是 Tool |
| --- | --- | --- | --- | --- |
| `get_asset_profile` | read | `asset_id` | `AssetProfile`、sensor definitions | Agent 需要按设备路由 |
| `get_sensor_history` | read | `asset_id`, `sensor_ids`, `start`, `end` | `SensorWindowRef`、quality summary | 隔离时序存储与分页/时窗规则 |
| `analyze_inspection_image` | external read | `artifact_id`, `asset_context`, `provider` | `VisionAnalysisResult` | provider 可替换且输出需 schema 校验 |
| `detect_sensor_anomalies` | CPU read | `window_ref`, `detector_config` | `SensorAnalysisResult`, evidence IDs | 确定性算法，不由 LLM 猜 |
| `get_failure_modes` | read | `asset_type`, optional symptom/sensor filters | `FailureModeCandidateSet` | 受控候选与来源 |
| `search_maintenance_knowledge` | read | `queries`, filters, `top_k` | `KnowledgeEvidenceSet` | RAG 只是可引用证据工具 |
| `create_work_order_draft` | write-draft | `diagnosis_id`, `recommended_actions` | `WorkOrderDraft` | 将自然语言建议变成稳定待审对象 |
| `request_approval` | write | `draft_id`, `risk_level` | `ApprovalRequest` | 显式持久化 Human-in-the-loop |
| `create_work_order` | protected write | `draft_id`, `approval_id?`, `idempotency_key` | `WorkOrder` | 有副作用，内部执行授权和幂等校验 |
| `get_work_order` | read | `work_order_id` | `WorkOrder` | UI 和 Agent 查询状态 |

Diagnosis synthesis 和 risk policy 不作为公开 MCP Tool：前者是 graph 内受控 LLM node，后者是确定性 domain policy。这样可以避免工具面无限扩张。

### 13.3 Tool 与 MCP

LangGraph 默认直接调用 in-process Tool adapter，减少本地 demo 的进程和网络复杂度。可选 `inspection-mcp` 使用 FastMCP 暴露同一组 read tools 和受保护 write tools，用于展示协议兼容或外部客户端接入。MCP 层不得绕过 domain service 的审批校验。

## 14. RAG 设计

### 14.1 知识来源

MVP 使用自编 synthetic manual、故障手册和维修 SOP，或明确可再分发的公开资料。每个文档记录标题、版本、来源、许可说明和哈希。禁止把不明版权的厂商手册直接提交仓库。

### 14.2 Ingestion

1. 解析标题、章节和段落；保留 page/section 元数据。
2. 按语义段落切分，并设置有限 overlap。
3. 生成 embedding，写入 FAISS。
4. 将 chunk 文本、source、hash 和 index version 写入 metadata store。
5. 运行基本检查：空 chunk、重复、过长、无法定位来源。

### 14.3 Retrieval

- 查询由资产类型、视觉标签、异常传感器和 failure mode 候选组合生成。
- 先按 `asset_type`、`document_type` 过滤，再做向量 top-k。
- 可选简单 lexical rerank；MVP 不引入复杂 reranker 服务。
- 返回 `chunk_id`、title、section/page、score 和短 excerpt。
- 若低于配置阈值，返回 insufficient evidence，不用低相关文本凑数量。

### 14.4 Grounding 约束

- 维修建议中的文档性陈述必须引用 chunk ID。
- 模型不能引用未返回的文档。
- UI 可展开查看原 chunk 与来源。
- 诊断 prompt 区分“设备观测证据”和“通用文档知识”，防止把 SOP 条件写成已观察事实。

### 14.5 为什么 MVP 选 FAISS

知识量小、单机、只读为主，FAISS 更容易启动和演示。Qdrant 的 collection、服务容器和网络管理在此阶段没有足够收益。未来需要在线文档管理、过滤和多用户时再迁移。

## 15. Vision 模块设计

### 15.1 职责边界

Vision 模块只输出“图中可观察到什么”，不单独下最终故障结论。建议 schema：

```text
image_quality
findings[]: label, observation, severity, confidence, region, evidence_id
negative_findings[]
limitations[]
provider, model, prompt_version, latency_ms
```

`label` 使用有限词表，例如 leakage_trace、corrosion、crack_like_mark、loose_component、discoloration、foreign_object、no_visible_anomaly。自由文本 observation 可以补充，但最终 diagnosis 不应只依赖自由文本。

### 15.2 Provider 抽象

- `FixtureVisionProvider`：只服务仓库自带案例，返回带 `fixture=true` 的预置结果，保证无 Key 可跑。
- `MultimodalLLMProvider`：调用配置的真实视觉模型，要求 JSON schema 输出。
- 后续可添加本地 VLM，但不在 MVP 强制下载大模型。

### 15.3 输入与安全

- 限制 MIME、大小、分辨率和文件数量。
- 使用内容哈希和生成的文件名，不信任用户原始路径。
- 不把图片内容写入普通日志。
- provider 失败或 schema 不合法时保存明确 error，不把原始自由文本当正式 finding。

### 15.4 评测边界

小样本只能证明 pipeline 和特定 fixture 行为，不能宣称工业视觉泛化能力。若后续建立手工标注集，才报告 label precision/recall、严重度一致性和 schema pass rate，并同时披露样本规模。

## 16. Time-Series 模块设计

### 16.1 为什么先做确定性基线

MVP 数据量小、故障是可控注入，最重要的是解释“为什么这一段异常”。rolling median/MAD 对离群点更稳健，规则阈值能表达设备运行限制；二者比直接引入 TSFM 更容易测试和讲清楚。

### 16.2 Pipeline

1. 校验 timestamp、单位、重复点和排序。
2. 估算采样间隔并按需要 resample。
3. 计算缺失率；只插值短缺口，长缺口保留为不可评估区间。
4. 应用设备 operating limits，产生 rule breach evidence。
5. 计算 rolling median 与 MAD：
   `robust_z = 0.6745 * (x - rolling_median) / MAD`。
6. MAD 为零时使用明确 fallback 或标记无法评分，不能静默除零。
7. 按阈值标记点，并将相邻点合并为 `AnomalySegment`。
8. 输出方向、持续时间、peak score、原始/基线值、方法与参数。

### 16.3 多传感器融合

MVP 不训练黑盒多变量模型。Diagnosis Agent 接收多个单传感器 segment 及其时间重合关系；确定性代码额外计算 overlap 和 trend summary。LLM 可以解释“振动与温度在同一时段升高”，但不能重新计算曲线。

### 16.4 可插拔接口

统一 `AnomalyDetector` protocol，让后续的 Isolation Forest、change-point、conformal 或 TSFM 返回同一 `SensorAnalysisResult`。新增算法前必须先定义数据集、比较基线和验收指标。

## 17. Diagnosis 与 Risk 设计

### 17.1 Diagnosis Agent 输入

- 资产上下文。
- Vision findings 与限制。
- Sensor anomaly segments 与质量警告。
- 受控 failure mode candidates。
- RAG chunks。

### 17.2 输出

- `primary_fault_candidate` 与最多若干 alternatives。
- 每个候选的 supporting/contradicting evidence IDs。
- `possible_causes`，明确是推断而非观测。
- `recommended_actions`，区分立即检查、计划维修和补充数据。
- `uncertainties` 与 `missing_evidence`。
- `evidence_strength` 使用 weak/moderate/strong 等级，避免伪精确概率。

### 17.3 Risk policy

LLM 可以提出 potential consequence，但最终 risk level 由版本化矩阵计算：

```text
risk_level = matrix(severity, likelihood, asset_criticality)
```

severity 来自可验证规则和受控 failure mode impact；likelihood 由证据强度、异常持续时间和跨模态一致性映射为离散等级。矩阵、输入和 policy version 全部保存。

## 18. Human-in-the-loop 与 Work Order

### 18.1 审批策略

- HIGH/CRITICAL：graph 创建 draft 和 approval request 后 interrupt；没有 APPROVED 决策不得创建工单。
- LOW/MEDIUM：默认只展示 draft，由用户主动点击创建；不自动产生副作用。
- reviewer 可以 approve、reject 或 request_changes，并填写原因。

### 18.2 双重守卫

仅在 graph 里画审批分支不够安全。`create_work_order` domain service 必须重新检查：

1. draft 存在且未失效。
2. draft hash 与 approval 绑定值一致。
3. 高风险 draft 的 decision 为 APPROVED。
4. approval 未被撤回或过期。
5. idempotency key 未创建过工单。

任何 Agent、MCP client 或 API 直接调用写 Tool，都必须经过相同校验。

### 18.3 最小工单字段

`work_order_id`、asset、title、description、priority、status、recommended actions、diagnosis ID、evidence IDs、approval ID、created_at。MVP 不复制 Maximo 成本、人员、计划和所有状态字段。

## 19. API 与 Dashboard

### 19.1 计划中的主要 API

- `GET /api/assets`
- `GET /api/assets/{asset_id}`
- `POST /api/inspections`：创建 inspection 并上传图片。
- `POST /api/inspections/{id}/run`：启动/恢复 workflow。
- `GET /api/runs/{run_id}`：读取节点状态与结果。
- `POST /api/approvals/{approval_id}/decision`
- `GET /api/work-orders/{work_order_id}`
- `GET /health` 与 `GET /ready`

实际实现前会先确定异步执行策略；MVP 可使用应用内 background task/队列抽象，不为单用户 demo 引入 Celery/Redis。

### 19.2 Dashboard 页面

- 资产列表与详情。
- 新建巡检：图片预览与时窗选择。
- 运行详情：节点进度、传感器曲线、异常区间、视觉 finding。
- 诊断报告：候选、支持/反证、文档引用、风险和不确定性。
- 审批卡片：draft diff、批准/拒绝/修改。
- 工单详情与证据链。

## 20. Evaluation 设计

### 20.1 评测分层

| 层 | 主要对象 | 优先指标/断言 |
| --- | --- | --- |
| Unit | detector、risk matrix、approval guard | 精确输入输出、边界、错误与幂等 |
| Vision | 手工标注 fixture | schema pass、label/attribute 对齐；披露样本数 |
| Sensor | 注入异常的 synthetic series | point/segment precision、recall、F1、detection delay |
| Retrieval | 人工 QA/chunk relevance | Recall@k、MRR、引用存在性 |
| Diagnosis | 结构化 scenario ground truth | allowed candidate、required/forbidden evidence、unsupported claim count |
| Workflow | tool trace 与数据库副作用 | 必需节点、禁止调用、审批 interrupt、重放幂等 |
| End-to-end | 完整用户案例 | completion、正确状态、可见引用、工单是否按策略产生 |
| Operational | trace | latency、tool calls、tokens、provider cost；只报告实测 |

### 20.2 Scenario schema

每个评测场景计划包含：

```text
scenario_id, description, asset_fixture, image_fixture, sensor_fixture
expected_visual_labels, expected_anomaly_windows
allowed_fault_candidates, required_evidence_kinds
expected_risk_levels, expected_approval_state
expected_work_order_side_effect
scoring_method, tags
```

允许答案有多个合理候选时使用集合或约束评分，不要求 LLM 与参考解释逐字一致。

### 20.3 Scorer 优先级

1. 数据库状态、JSON 字段、数值容差、引用有效性和工具序列：确定性 scorer。
2. 解释是否清晰、是否充分表达不确定性：独立模型的 LLM judge，可选。
3. 禁止用同一个模型自评；judge 结果不覆盖确定性失败。

### 20.4 消融计划

在场景足够后比较：sensor-only、vision-only、无 RAG、完整系统。比较目的是理解模块贡献与失败，不预设完整系统一定更优。

### 20.5 结果披露

README 只展示通过可复现命令生成的报告，并同时注明 commit、provider、模型、数据集版本、场景数和运行时间。不引用 AssetOpsBench 的成绩，也不写“生产级”“准确率领先”等无依据表述。

## 21. 测试策略

- pytest unit tests：schema、算法、risk、approval、idempotency、path validation。
- repository tests：临时 SQLite 和临时 artifact 目录。
- provider contract tests：fixture adapter 与模拟失败。
- integration tests：Tool -> service -> repository，不调用真实付费 API。
- graph tests：分支、interrupt/resume、失败降级与 checkpoint 重放。
- API tests：上传限制、状态码、审批接口和工单查询。
- e2e scenario tests：使用离线 fixture 完成业务闭环。
- secret checks：`.env` 被忽略，fixture 中无 token；日志不含配置 secret。

真实 provider 的测试默认标记为 opt-in，避免 CI 消耗密钥和费用。

## 22. 开发阶段与验收条件

项目划分为六个 Phase（Phase 0 到 Phase 5）。只有 Phase 5 结束才称为 Portfolio MVP 完成。

### Phase 0：研究与设计（已完成）

交付：

- `docs/ASSETOPSBENCH_ANALYSIS.md`
- `docs/PROJECT_PLAN.md`
- `docs/ARCHITECTURE.md`

验收：

- 覆盖指定 AssetOpsBench 模块并固定参考提交。
- 区分 IBM 已实现能力、本项目计划和原创范围。
- Mermaid 图可解析，计划包含 MVP、数据模型、Tool、Evaluation 与逐阶段条件。
- 不存在 FastAPI、LangGraph、RAG、数据库、前端或 Vision 业务实现。

### Phase 1：工程骨架与可复现数据（已完成）

交付：

- Python package、settings、logging、统一 schema/error。
- SQLite migration/repository 基础。
- 两类资产、三套 scenario 的 synthetic sensor/image/knowledge manifest。
- 数据生成脚本、seed 命令和基础 pytest。

验收：

- 从空数据目录可重复生成同 hash/version 的 demo 数据或明确记录随机 seed。
- 资产和 dataset metadata 可查询。
- `.env.example` 只有占位符，仓库无 secret。
- 单元测试覆盖核心 schema、路径与 repository 边界。

### Phase 2：独立分析模块（已完成）

交付：

- Vision provider protocol 与 `FixtureVisionProvider`；基础闭环阶段不接真实多模态 API。
- 数据质量、operating-limit、rolling MAD、segment 聚合。
- Failure mode repository。
- RAG ingestion/retrieval 与引用 metadata。

验收：

- 每个模块可不经过 Agent 独立调用和测试。
- 无 API Key 时 fixture scenario 完整运行。
- detector 对预置注入输出可验证 segment，算法参数被持久化。
- RAG 返回的每个 chunk 可定位到文档和章节。

### Phase 3：LangGraph、诊断、HITL 与工单（已完成）

交付：

- 一个 LangGraph workflow，以及它的 typed tools、state、nodes、routing 和 checkpoint。
- schema-constrained diagnosis 与确定性 risk policy。
- work order draft、approval interrupt/resume、protected create tool。
- tool trace 与 idempotency。

验收：

- 正常、单分支失败、证据全失三条 graph 路径都有测试。
- HIGH/CRITICAL 在未批准时无法创建工单，包括直接调用 Tool。
- 批准后恢复同一 run 并只创建一张工单。
- diagnosis 的 evidence IDs 全部可解析，非法引用被拒绝。

### Phase 4：FastAPI、Dashboard 与 Docker Demo（已完成）

交付：

- FastAPI API、模板/HTMX Dashboard、Plotly.js 曲线。
- 上传、运行进度、诊断、引用、审批和工单页面。
- Dockerfile/Compose、health/readiness 和 demo seed 启动流程。
- 基础闭环稳定后，再增加一个可配置的真实多模态 Vision provider adapter。

验收：

- 新环境按文档一条主要命令启动。
- 浏览器完成至少一个高风险审批案例和一个无工单案例。
- UI 明确显示 synthetic/fixture/provider 状态。
- 重启后 workflow/approval/work order 状态可恢复。

### Phase 5：Evaluation、质量与作品集发布（已完成，外部门禁待验证）

交付：

- scenario runner、deterministic scorers、聚合报告。
- pytest 全套、CI、lint/type check、secret scan。
- README、架构说明、Demo GIF/截图、设计取舍和已知限制。
- 实测结果文件；没有运行的指标不写入 README。

验收：

- 一条命令可运行离线 evaluation 并生成带版本信息的报告。
- 测试/CI 在干净环境通过。
- README 功能清单与实际实现逐项一致。
- 所有已知限制、fixture 含义和非生产定位清楚可见。

## 23. 后续 Roadmap

Portfolio MVP 之后再按证据和需求选择：

- PostgreSQL + Qdrant，支持在线文档管理与更大数据量。
- Isolation Forest、change-point、conformal 或 TSFM，与 MVP baseline 做同数据对比。
- 高频振动 FFT/包络谱和轴承特征频率。
- 多图片/视频巡检和目标区域标注。
- 实时 MQTT/streaming ingestion，但继续保留 simulator。
- CMMS/Maximo adapter，只在有合法测试环境时实现。
- 用户、角色和审计日志。
- 多模型 cost/latency/quality evaluation。
- 核心闭环和 evaluation 稳定后，再评估是否需要可选 FastMCP gateway。

这些不是 README 的当前功能，也不是 MVP 验收前置条件。

## 24. 主要风险与控制

| 风险 | 控制方式 |
| --- | --- |
| 项目范围过大 | 两类资产、三套 demo、一个 graph、一个向量后端 |
| 多模态模型输出不稳定 | JSON schema、有限标签、fixture、失败不接收自由文本 |
| RAG 引用幻觉 | chunk ID 强校验、来源可展开、低相关返回不足 |
| 时序算法被过度宣传 | synthetic 注入、公开算法参数、不推断真实泛化 |
| Agent 绕过审批 | domain service 二次校验、draft hash、幂等唯一约束 |
| Tool 数量过多 | 只暴露业务边界；内部 policy 不包装成 Tool |
| 本地 Demo 难启动 | SQLite/FAISS/文件存储，默认不依赖外部服务 |
| README 超前 | 每个 Phase 更新 implemented checklist，计划与实现分栏 |
| API Key 泄漏 | `.env`、secret scan、日志过滤、真实 provider 测试 opt-in |

## Differences from IBM AssetOpsBench

AssetOpsBench 更偏向**工业 Agent benchmark / evaluation framework**：它提供大量工业场景、领域 MCP 工具、可替换编排器、模拟数据环境、轨迹和排行榜式评测，用于研究模型及 Agent 架构在工业任务上的表现。

本项目更偏向**最终用户可使用的多模态智能巡检应用**：它只覆盖少量设备和场景，但要求用户从 Dashboard 发起巡检，看到图片与时序证据，获得带维修文档引用的诊断，并通过真实可恢复的人工审批创建工单。

最重要的区别不是换技术栈，而是优化目标不同：

| 维度 | IBM AssetOpsBench | 本项目 |
| --- | --- | --- |
| 首要目标 | 场景覆盖、Agent/模型比较、可复现评测 | 完整交互闭环、证据可见、用户操作与安全副作用 |
| 规模 | 140+ 场景、多 server、多 runner | 两类资产、少量高质量 demo/eval 场景、一个 LangGraph |
| 用户入口 | benchmark/CLI/agent runner 为主 | FastAPI Web Dashboard |
| 视觉 | 当前核心源码没有完整的图片上传巡检闭环 | 图片 artifact、Vision provider、视觉证据卡片 |
| 知识 | failure modes、catalog 和工业记录为主 | 说明书/故障手册/SOP 的可引用 RAG |
| 编排 | 比较多种范式和 Runner | 一条可解释、可暂停、可恢复的 LangGraph |
| 工单 | 丰富的工单读写与生命周期 | 简化工单 + 独立审批对象 + 写 Tool 双重守卫 |
| 时序 | TSFM/feature/model recipe 平台 | 可解释 deterministic baseline，后续再扩展 |
| 存储 | CouchDB 场景重置与多 collection | SQLite + Parquet/CSV + FAISS，优先本地易运行 |
| 评测 | benchmark trajectory 与模型对比 | 模块评测 + 闭环状态/引用/审批/副作用断言 |

本项目的原创开发部分包括：

1. 面向巡检图片的 artifact 与 Vision finding schema。
2. 视觉发现和时序异常的统一 Evidence 模型。
3. 受控 failure mode、维修 RAG 与诊断候选之间的证据链。
4. 结合反证、缺失证据和不确定性的 diagnosis schema。
5. 版本化确定性风险矩阵。
6. LangGraph Human-in-the-loop interrupt/resume。
7. draft hash、approval 与 idempotent work-order write 的双重安全守卫。
8. 面向最终用户的设备、图表、证据、审批和工单 Dashboard。
9. 针对多模态闭环而不是通用 Agent leaderboard 的 scenario/evaluation。

本项目会在致谢和参考资料中明确说明 AssetOpsBench 的启发，但不会复制其源码、目录、benchmark 数量、实验结论或业务成绩。
