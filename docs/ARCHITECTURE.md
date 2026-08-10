# 系统架构设计

> 当前状态：Phase 2 已实现 SQLite/CSV demo 数据、Fixture Vision、确定性时序分析、JSON Failure Mode、FAISS 本地检索和模块级 evaluation。  
> 本文中的 LangGraph、Diagnosis、Risk、FastAPI、Dashboard、审批和工单仍是后续开发边界，不代表仓库当前已有对应实现。

## 1. 架构目标

- 支持“图片 + 时序 + 故障模式 + 维修知识 + 人工审批 + 工单”的端到端闭环。
- 确定性算法、LLM 推理和数据库副作用边界清楚。
- 无 API Key 时仍可用 synthetic data 与 fixture provider 演示。
- LangGraph 工作流可观测、可暂停、可恢复、可幂等重放。
- 核心领域服务不依赖 LangGraph、FastAPI 或 MCP，方便单测和替换适配器。
- MVP 使用 SQLite/FAISS/本地文件，避免为个人项目引入不必要的分布式基础设施。

## 2. Overall System Architecture

```mermaid
flowchart TB
    User["巡检人员 / 维修工程师 / 审批人"]

    subgraph Presentation["Presentation"]
        Dashboard["Web Dashboard<br/>Jinja2 + HTMX + Plotly.js"]
    end

    subgraph Application["API and Application"]
        API["FastAPI<br/>upload / run / approve / query"]
        UseCases["Application Services<br/>validation and use cases"]
    end

    subgraph Workflow["Agent Workflow"]
        Graph["LangGraph<br/>state + routing + checkpoint"]
        Diagnosis["Diagnosis Node<br/>evidence-grounded LLM synthesis"]
        Risk["Risk Policy<br/>deterministic matrix"]
        HITL["Human-in-the-loop<br/>interrupt and resume"]
    end

    subgraph Domain["Domain Services and Typed Tools"]
        AssetSvc["Asset Service"]
        VisionSvc["Vision Service"]
        SensorSvc["Sensor Analysis Service"]
        FailureSvc["Failure Mode Service"]
        KnowledgeSvc["Knowledge / RAG Service"]
        WorkOrderSvc["Approval and Work Order Service"]
    end

    subgraph Storage["Persistence"]
        SQLite[("SQLite<br/>metadata / diagnosis / approval / work order")]
        Series[("Parquet or CSV<br/>sensor series")]
        Files[("Artifact Store<br/>inspection images")]
        FAISS[("FAISS<br/>knowledge vectors")]
        KnowledgeMeta[("JSON<br/>chunk text and citation metadata")]
        FailureCatalog[("JSON<br/>controlled failure modes")]
    end

    subgraph Providers["External or Local Providers"]
        VisionProvider["Fixture or Multimodal Vision Provider"]
        LLMProvider["LLM Provider"]
        EmbedProvider["Embedding Provider"]
    end

    subgraph Quality["Observability and Evaluation"]
        Trace["Workflow and Tool Trace"]
        Eval["Scenario Runner and Scorers"]
        Report["Evaluation Reports"]
    end

    User --> Dashboard --> API --> UseCases --> Graph
    Graph --> AssetSvc
    Graph --> VisionSvc
    Graph --> SensorSvc
    Graph --> FailureSvc
    Graph --> KnowledgeSvc
    Graph --> Diagnosis --> Risk --> HITL
    HITL --> WorkOrderSvc

    AssetSvc --> SQLite
    VisionSvc --> Files
    VisionSvc --> VisionProvider
    SensorSvc --> Series
    FailureSvc --> FailureCatalog
    KnowledgeSvc --> FAISS
    KnowledgeSvc --> KnowledgeMeta
    KnowledgeSvc --> EmbedProvider
    Diagnosis --> LLMProvider
    WorkOrderSvc --> SQLite

    Graph -. "events" .-> Trace
    Trace --> Eval --> Report
```

### 2.1 分层说明

- Dashboard 不直接访问 storage 或 provider。
- FastAPI 负责传输层与用例入口，不承载诊断算法。
- LangGraph 只保存 ID 和结构化小结果；图片、时序和大文档通过引用访问。
- Domain Service 是唯一业务事实来源，Tool、API 和 MCP 都是适配器。
- 诊断 LLM 只接收已筛选的 Evidence，不直接执行 SQL、读取任意路径或写工单。
- Risk Policy 与 WorkOrder Guard 是确定性代码，即使 Agent 路由错误也不能绕过。

## 3. Agent Workflow

```mermaid
flowchart TD
    Start(["START"])
    Validate["Validate request<br/>asset, image, window, idempotency"]
    Context["Load asset context<br/>sensor definitions and limits"]

    Vision["Analyze image<br/>Vision Service"]
    LoadSeries["Load sensor window"]
    Detect["Data quality and anomaly detection<br/>deterministic Python"]

    Join["Join branch results"]
    EvidenceGate{"Any usable observed evidence?"}
    Insufficient["Finalize as evidence-insufficient<br/>no diagnosis claim and no work order"]

    FailureModes["Lookup controlled failure modes"]
    Query["Build recorded retrieval queries"]
    Retrieve["Retrieve manual, fault guide and SOP chunks"]
    Synthesize["Synthesize diagnosis<br/>schema + evidence IDs + uncertainty"]
    RiskPolicy["Apply deterministic risk policy"]
    ActionGate{"Maintenance action recommended?"}
    ReportOnly["Finalize report without work order"]
    Draft["Create immutable work-order draft"]
    RiskGate{"Risk is HIGH or CRITICAL?"}
    OptionalDraft["Expose optional draft<br/>user may explicitly create later"]
    Interrupt["LangGraph interrupt<br/>await approval decision"]
    Decision{"Human decision"}
    Revise["Invalidate old approval<br/>revise draft and request again"]
    Rejected["Finalize with rejected status<br/>no work order"]
    Guard["Protected create guard<br/>approval + draft hash + idempotency"]
    Create["Create work order"]
    Final["Finalize report and trace"]
    End(["END"])

    Start --> Validate --> Context
    Context --> Vision
    Context --> LoadSeries --> Detect
    Vision --> Join
    Detect --> Join
    Join --> EvidenceGate
    EvidenceGate -->|"no"| Insufficient --> End
    EvidenceGate -->|"yes"| FailureModes --> Query --> Retrieve --> Synthesize --> RiskPolicy --> ActionGate
    ActionGate -->|"no"| ReportOnly --> Final
    ActionGate -->|"yes"| Draft --> RiskGate
    RiskGate -->|"LOW or MEDIUM"| OptionalDraft --> Final
    RiskGate -->|"HIGH or CRITICAL"| Interrupt --> Decision
    Decision -->|"approve"| Guard --> Create --> Final
    Decision -->|"reject"| Rejected --> Final
    Decision -->|"request changes"| Revise --> Draft
    Final --> End
```

### 3.1 Workflow 约束

- Vision 与 sensor analysis 可以并行，二者互不伪造对方结果。
- 允许单分支降级，但报告必须保留 warning 并降低 evidence strength。
- 两个观察分支都不可用时，不进入故障定性和工单路径。
- `synthesize_diagnosis` 只能引用 state 中已存在的 evidence IDs。
- risk level 由 policy 计算，LLM 不能在输出中覆盖。
- HIGH/CRITICAL 的 graph resume 只是第一层检查；write service 还会二次验证。
- 同一个 `draft_id + idempotency_key` 重放只返回已有工单。

## 4. Tool Architecture / Post-core Optional MCP

Phase 1-3 只使用进程内 typed service/tool 调用，不实现 MCP。下图中的 FastMCP gateway 是核心闭环完成后才重新评估的可选外部适配器，不属于 MVP 核心开发路径。

```mermaid
flowchart LR
    subgraph Callers["Callers"]
        GraphNode["LangGraph Nodes"]
        FastAPIUseCase["FastAPI Use Cases"]
        ExternalClient["Optional External MCP Client"]
    end

    subgraph Adapters["Thin Adapters"]
        LocalTools["In-process Typed Tools<br/>Pydantic input and output"]
        MCPGateway["inspection-mcp<br/>FastMCP gateway"]
    end

    subgraph Services["Single Business Implementation"]
        Asset["Asset Service"]
        Vision["Vision Service"]
        Sensor["Sensor Service"]
        Failure["Failure Mode Service"]
        Knowledge["Knowledge Service"]
        WorkOrder["Approval and Work Order Service"]
    end

    subgraph Guards["Cross-cutting Guards"]
        Schema["Schema Validation"]
        Authz["Approval and Write Policy"]
        Idempotency["Idempotency"]
        Audit["Trace and Audit"]
    end

    GraphNode --> LocalTools
    FastAPIUseCase --> LocalTools
    ExternalClient --> MCPGateway
    LocalTools --> Schema
    MCPGateway --> Schema
    Schema --> Asset
    Schema --> Vision
    Schema --> Sensor
    Schema --> Failure
    Schema --> Knowledge
    Schema --> WorkOrder
    WorkOrder --> Authz --> Idempotency
    Asset -.-> Audit
    Vision -.-> Audit
    Sensor -.-> Audit
    Failure -.-> Audit
    Knowledge -.-> Audit
    WorkOrder -.-> Audit
```

### 4.1 为什么 Phase 1-3 不实现 MCP Server

MVP 是单机应用，MCP server 会增加进程发现、连接、环境变量和启动延迟，却不会增加领域隔离的实质价值。Phase 1-3 的领域隔离由 Python module、interface、schema 和测试完成。核心闭环完成后，只有在确有外部客户端接入或协议展示价值时，才考虑一个复用既有业务服务的可选 gateway。

### 4.2 Tool contract

每个 Tool 返回稳定 envelope：

```text
ToolResult<T>
├─ schema_version
├─ trace_id
├─ status: success | partial | error
├─ data: T | null
├─ warnings[]
└─ error: code, message, retryable | null
```

Tool 不返回 secret、图片二进制或无限长时序。大型结果返回 `artifact_id`、`window_ref` 或 `result_id`。写 Tool 必须声明副作用、支持 idempotency，并由 service 而不是 prompt 执行权限判断。

## 5. Data Flow

```mermaid
flowchart TB
    AssetInput["Selected asset_id"]
    ImageInput["Uploaded inspection image"]
    SensorInput["Synthetic sensor dataset"]
    KnowledgeInput["Synthetic manual / fault guide / SOP"]

    ImageStore[("Artifact Store")]
    SeriesStore[("Parquet or CSV")]
    Metadata[("SQLite")]
    VectorStore[("FAISS + chunk metadata")]
    FailureCatalog[("Failure mode JSON")]

    VisionAnalysis["Vision analysis"]
    SensorAnalysis["Data quality and anomaly analysis"]
    FailureLookup["Failure mode lookup"]
    Retrieval["Knowledge retrieval"]
    EvidenceBundle["Evidence Bundle<br/>IDs + summaries + provenance"]
    Diagnosis["Diagnosis synthesis"]
    Risk["Risk matrix"]
    Draft["Work-order draft"]
    Approval["Human approval record"]
    WorkOrder["Work order"]
    Trace[("Workflow and Tool Trace")]

    AssetInput --> Metadata
    ImageInput -->|"validate, hash, persist"| ImageStore
    ImageStore -->|"artifact_id"| VisionAnalysis
    SensorInput --> SeriesStore
    SeriesStore -->|"window_ref"| SensorAnalysis
    KnowledgeInput -->|"ingest, chunk, embed"| VectorStore
    FailureCatalog --> FailureLookup
    VectorStore --> Retrieval

    VisionAnalysis -->|"VisionFinding evidence"| EvidenceBundle
    SensorAnalysis -->|"AnomalySegment evidence"| EvidenceBundle
    FailureLookup -->|"controlled candidates"| EvidenceBundle
    Retrieval -->|"KnowledgeChunk evidence"| EvidenceBundle

    EvidenceBundle -->|"structured evidence only"| Diagnosis
    Diagnosis --> Metadata
    Diagnosis --> Risk
    Risk --> Draft
    Draft --> Metadata
    Draft --> Approval
    Approval --> Metadata
    Approval -->|"approved draft hash"| WorkOrder
    WorkOrder --> Metadata

    VisionAnalysis -.-> Trace
    SensorAnalysis -.-> Trace
    FailureLookup -.-> Trace
    Retrieval -.-> Trace
    Diagnosis -.-> Trace
    Risk -.-> Trace
    Approval -.-> Trace
    WorkOrder -.-> Trace
```

### 5.1 数据分类

| 数据 | 是否进入 LLM | 保存位置 | 说明 |
| --- | --- | --- | --- |
| 原始图片 | 仅发送给配置的 Vision provider | artifact store | 不写普通日志 |
| 完整时序 | 否 | Parquet/CSV | Python 算法处理 |
| 时序摘要/异常区间 | 是 | SQLite + state | 数值已由算法计算 |
| 完整知识文档 | 否 | source files | 只将 top-k chunks 送入 LLM |
| RAG chunk | 是 | metadata + FAISS | 带 chunk/source/section ID |
| failure mode 候选 | 是 | SQLite/YAML seed | 受控、带来源 |
| diagnosis | LLM 生成后校验 | SQLite | 必须引用 evidence IDs |
| risk level | 否，由 policy 生成 | SQLite | 保存 policy version |
| approval | 人工输入 | SQLite | 绑定 draft hash |
| work order | 否，由 service 写入 | SQLite | 受保护且幂等 |

## 6. 关键时序

### 6.1 高风险批准

```mermaid
sequenceDiagram
    actor User as 巡检人员
    participant UI as Dashboard
    participant API as FastAPI
    participant Graph as LangGraph
    participant Tools as Domain Tools
    participant DB as SQLite
    actor Reviewer as 审批人

    User->>UI: 选择设备并上传图片
    UI->>API: 创建 inspection 并启动 run
    API->>Graph: invoke inspection state
    par 图片分支
        Graph->>Tools: analyze_inspection_image
        Tools-->>Graph: vision evidence IDs
    and 时序分支
        Graph->>Tools: get_sensor_history + detect_sensor_anomalies
        Tools-->>Graph: anomaly evidence IDs
    end
    Graph->>Tools: get_failure_modes + search_maintenance_knowledge
    Tools-->>Graph: controlled candidates + cited chunks
    Graph->>Graph: synthesize diagnosis + apply risk policy
    Graph->>Tools: create_work_order_draft
    Tools->>DB: persist draft and approval request
    Graph-->>API: interrupt with approval_request_id
    API-->>UI: awaiting approval
    Reviewer->>UI: approve with reason
    UI->>API: submit decision
    API->>DB: persist approval bound to draft hash
    API->>Graph: resume checkpoint
    Graph->>Tools: create_work_order
    Tools->>DB: verify approval, hash and idempotency, then create once
    Tools-->>Graph: work_order_id
    Graph-->>UI: final report and work order
```

## 7. 安全与可靠性边界

- 上传路径由服务端生成；拒绝路径穿越、非法 MIME 和超限文件。
- 所有 API Key 只从环境变量读取；`.env` 不提交，日志过滤 secret。
- fixture 与真实 provider 结果在 schema 和 UI 中明确区分。
- 不把自由文本模型输出直接当成正式实体，必须通过 Pydantic 和领域校验。
- Diagnosis 不得产生不存在的 evidence 引用。
- WorkOrder 写入必须验证 approval、draft hash、risk policy 和 idempotency。
- checkpoint 不保存二进制和超大 payload。
- evaluation 读取固定版本 fixture，不能依赖会变化的在线结果作为唯一 ground truth。

## 8. 部署拓扑

MVP 默认单容器/单进程应用，挂载一个数据卷：

```text
Browser -> FastAPI application
             ├─ LangGraph
             ├─ SQLite
             ├─ FAISS index
             ├─ image artifacts
             └─ Parquet/CSV sensor data
```

真实 Vision/LLM/Embedding provider 是可选外部依赖。未来迁移到 PostgreSQL、Qdrant 或 worker queue 时，domain interface 和 Tool schema 保持不变。

## 9. 架构决策记录摘要

| 决策 | 选择 | 被放弃的 MVP 方案 | 原因 |
| --- | --- | --- | --- |
| Agent 编排 | 单一 LangGraph | 同时维护多个 Runner | 项目目标不是框架 benchmark |
| Tool 调用 | Phase 1-3 仅 in-process | MCP 或每域独立进程 | 更易启动和排错；可选 gateway 留到核心闭环后决策 |
| 数据库 | SQLite | CouchDB/PostgreSQL | 单用户 demo 足够，事务和约束清楚 |
| 时序存储 | Parquet/CSV 引用 | 将所有观测写 SQLite | 更适合列式分析和 fixture 版本化 |
| 向量库 | FAISS | Qdrant | 小型本地语料无需服务化 |
| 时序算法 | limit + rolling MAD | 直接上 TSFM | 可解释、可测试、有基线 |
| 风险 | 确定性矩阵 | LLM 直接输出最终等级 | 可审计、可复现 |
| 前端 | Jinja2/HTMX/Plotly.js | React SPA | 降低个人项目非核心工作量 |
| 离线演示 | fixture provider | 必须有 API Key | 招聘演示和 CI 可复现 |

## 10. 与 AssetOpsBench 的架构关系

保留的思想是领域工具边界、typed schema、数值任务确定性执行、大结果引用、轨迹持久化和离线 evaluation。修改之处是从“多个领域 server + 多编排器 + benchmark data reset”收敛为“一个最终用户应用 + 一个 LangGraph + 共享领域服务”。视觉巡检、维修 RAG、审批对象、write guard 和 Dashboard 是本项目为业务闭环新增的核心架构。
