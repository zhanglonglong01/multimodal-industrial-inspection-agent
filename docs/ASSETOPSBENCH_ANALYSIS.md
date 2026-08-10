# IBM AssetOpsBench 研究与取舍

> 文档状态：Phase 0 研究记录，不代表本项目已经实现下述功能。  
> 研究日期：2026-08-11  
> 研究基线：IBM/AssetOpsBench `main`，提交 [`e11d1c1`](https://github.com/IBM/AssetOpsBench/tree/e11d1c1b2022db0396364a6d66e24168955a3bb7)  
> 研究原则：以当前提交的源码和文档为主，以论文解释早期设计；不复制源码，不把 IBM 的实现写成本项目成果。

## 1. 结论摘要

AssetOpsBench 的核心价值不是某个单独算法，而是把工业资产运维问题拆成可复现的场景、边界清晰的领域工具、可替换的 Agent 编排器和可离线重评分的轨迹评测。其当前 `main` 已经从论文中的“四类领域 Agent + 两种编排范式”扩展为六个领域 MCP Server、多种 Agent Runner、统一轨迹与离线评测模块。

最值得本项目学习的设计有五点：

1. 让确定性领域能力通过有类型的 Tool 暴露，LLM 只负责选择、路由和综合。
2. 将资产注册信息、传感器观测、故障模式、时序分析和工单写入分成独立边界。
3. 对大结果返回“摘要 + 文件指针/结果 ID”，避免把整段时序数据塞入 LLM 上下文。
4. 把场景运行与评分解耦，保存完整轨迹后可以更换 scorer 重评。
5. 场景围绕工业人员意图设计，而不是为了覆盖工具接口而拼接问题。

AssetOpsBench 不适合直接缩小复制。对个人项目而言，完整 TSFM 模型/特征目录、多个 Agent SDK Runner、CouchDB 场景重置体系、多个独立 MCP 进程和大规模 benchmark 运维成本过高。本项目只保留能支撑“现场图片 + 传感器 + 维修知识 + 审批工单”闭环的最小边界，并新增可交互 Web 应用、视觉巡检、RAG 和 Human-in-the-loop。

## 2. 研究范围与事实口径

本次实际阅读和核对了：

- [`README.md`](https://github.com/IBM/AssetOpsBench/blob/e11d1c1b2022db0396364a6d66e24168955a3bb7/README.md) 与 [`INSTRUCTIONS.md`](https://github.com/IBM/AssetOpsBench/blob/e11d1c1b2022db0396364a6d66e24168955a3bb7/INSTRUCTIONS.md)
- 项目目录、`pyproject.toml` 入口点和测试布局
- [`docs/mcp-servers.md`](https://github.com/IBM/AssetOpsBench/blob/e11d1c1b2022db0396364a6d66e24168955a3bb7/docs/mcp-servers.md)
- IoT、FMSR、TSFM、WO、Vibration、Utilities MCP Server 源码与输出模型
- Plan-Execute、OpenAI Agent、Claude Agent、Deep Agent 等 Runner
- CouchDB 通用数据装载、scenario manifest 和 scenario suite runner
- evaluation 的 loader、scorer、metrics、report 及其测试
- AssetOpsBench 论文 v3 的架构、scenario 与 evaluation 章节：[`arXiv:2506.03828`](https://arxiv.org/abs/2506.03828v3)

需要特别区分两个时间切面：

- **论文设计**：四个领域 Agent（IoT、FMSR、TSFM、WO），比较 Agent-As-Tool 与 Plan-Execute；141 个场景中包含单 Agent 和多 Agent 任务；采用 rubric judge 与参考轨迹对齐。
- **当前 `main` 实现**：六个 MCP Server，外加多种 SDK/CLI Runner；评测已经形成保存 trajectory 后离线评分的模块，并新增确定性的 `static_json` scorer。

因此，下面不会用论文中的早期能力替代当前源码事实。例如，当前 FMSR 明确没有暴露 failure-mode/sensor mapping；README 的 quick-start 命令也标注了 “to be enabled”。

## 3. 整体架构

当前 AssetOpsBench 可以概括为五层：

1. **场景与数据层**：scenario 目录中的 `manifest.json` 指定本场景需要加载的集合，`question.txt` 提供问题，外部完整场景还包含 ground truth；通用 loader 将 CSV/JSON 写入 CouchDB。
2. **领域服务层**：IoT、FMSR、TSFM、WO、Vibration、Utilities 分别作为 FastMCP stdio server，对外暴露工具。
3. **Agent 编排层**：Plan-Execute 或 SDK 驱动的 ReAct/Deep Agent 发现并调用 MCP 工具。
4. **可观测层**：统一记录回答、turn、tool call、token 和耗时，持久化为 trajectory。
5. **评测层**：按 `scenario_id` 连接 ground truth 与 trajectory，选择 scorer，输出单场景报告和聚合报告。

当前源码的主要目录职责如下：

```text
AssetOpsBench/
├─ benchmarks/scenario_suite/  # 场景选择 profile 与批量 runner
├─ docs/                       # MCP、数据、评测、设计指南
├─ notebook/                   # TSFM 与工具工作流示例
├─ src/
│  ├─ agent/                   # 多种 Agent Runner 与共享 trajectory 模型
│  ├─ benchmark/               # scenario suite 执行器
│  ├─ couchdb/                 # 通用 loader、manifest、样例数据、设计文档
│  ├─ evaluation/              # loader、scorer、指标与报告
│  ├─ llm/                     # 模型后端与路由
│  ├─ observability/           # tracing 与 trajectory 持久化
│  └─ servers/                 # iot/fmsr/tsfm/wo/vibration/utilities
└─ tests 分布在各模块内部
```

这种组织方式强调“领域模块拥有自己的测试”，而不是把所有行为堆在统一入口中。它适合 benchmark 框架，但本项目会进一步使用单一 Python 包和共享领域服务，减少重复进程及部署配置。

## 4. 核心模块

| 模块 | 当前职责 | 关键设计价值 |
| --- | --- | --- |
| `servers/iot` | 资产注册发现、传感器发现、历史数据、覆盖度与统计 | 区分资产元数据和真实观测字段；对时窗、分页和时间戳做约束 |
| `servers/fmsr` | 查询、生成、写入资产类别的 failure mode 列表 | 读取、LLM 生成和持久化是三个不同动作；结果带来源和 completeness 语义 |
| `servers/tsfm` | 任务、数据证据、模型/特征目录、recipe 执行、结果账本 | 让 Agent 选择方案，让确定性代码执行时序任务并保存证据 |
| `servers/wo` | 工单读取、创建、更新、审批、分派、关闭、取消与 KPI | 读写工具分组；写入有状态、有验证、有来源 |
| `servers/vibration` | FFT、包络谱、轴承特征频率、振动严重度和诊断 | 物理/信号处理逻辑不交给 LLM 计算 |
| `servers/utilities` | JSON、目录与时间等辅助能力 | 通用能力与领域工具分离 |
| `agent` | 计划执行或多轮工具调用 | Runner 可替换，领域工具不随 Agent 框架重写 |
| `evaluation` | 保存后评分与运营指标汇总 | 执行和评分解耦，可复评、可对比 |

## 5. MCP Server 设计

### 5.1 进程与协议边界

六个 server 都是独立 FastMCP stdio 进程，由 Agent client 按需启动。Agent 通过 MCP 的 `list_tools` 获取工具描述与 JSON Schema，再通过 `call_tool` 调用。server 背后可以访问 CouchDB、文件系统或 CPU 密集算法，但调用者只依赖工具契约。

这一设计的优点是：

- 领域能力与具体 Agent SDK 解耦。
- 可以独立测试、替换或限制某个 server。
- 类型注解/Pydantic 模型同时承担运行时校验和工具输出契约。
- 工具描述成为 Agent 路由的重要上下文。

代价是：

- 多个 stdio 进程会增加启动、发现、配置和排错成本。
- 如果每一步都重新连接 server，会带来明显延迟。
- server 数量和工具数量扩大后，LLM 的工具选择空间也更难控制。

### 5.2 工具契约模式

AssetOpsBench 的工具边界有几个值得保留的模式：

- 输出采用明确结果模型或 `ErrorResult`，避免任意自然语言。
- 工具描述写清可选参数、范围、时窗语义和前置发现工具。
- 读、写、LLM-use、CPU-centric 分类清楚。
- WO 支持只暴露 read tools 的只读模式。
- TSFM 对大型输出返回 headline 字段和 `results_file`，完整结果另存。
- run、result 和 lineage 记录带 `asset_id`、`scenario_id`、parent 等可追踪字段。

### 5.3 对本项目的启示

本项目不会为每个小模块启动一个 MCP 进程。MVP 计划将业务逻辑写成无框架依赖的领域服务，同时提供两种薄适配器：LangGraph 的 in-process typed tools，以及一个可选的 FastMCP gateway。两种入口复用同一服务，避免“Web 一套、MCP 一套”重复实现。

## 6. Agent orchestration

### 6.1 论文中的两种范式

论文比较：

- **Agent-As-Tool**：领域 Agent 被当作高层工具，由 supervisor 通过 ReAct 迭代选择。
- **Plan-Execute**：Planner/Reviewer 将问题分解成 DAG，Orchestrator 按依赖执行并通过共享记忆传递结果。

其研究目的主要是比较不同编排范式在同一 benchmark 上的质量、步骤数、延迟与失败模式。

### 6.2 当前 `main` 的实现

当前 Plan-Execute Runner 会：

1. 从所有 MCP Server 发现工具签名。
2. 让 LLM 生成包含 task、server、tool、dependency 和 expected output 的计划。
3. 对每步再次让 LLM 根据原问题、工具 schema 与前序结果解析参数。
4. 按拓扑顺序顺序执行 MCP tool。
5. 将所有 step result 交给 LLM 汇总最终回答。

它保留了完整 plan 与 step trajectory，利于诊断；但预先计划错误可能层层传递，而且当前执行器即使有 DAG 依赖也采用顺序执行。

OpenAI Agent 与 Claude Agent 直接把 MCP Server 交给各自 SDK 的多轮 agent loop；Deep Agent 通过 LangChain MCP adapter 将 MCP 工具转换后交给 deep-agents。它们共享统一 `AgentResult`/`Trajectory` 形状，从而可以进入同一观察与评测链路。

### 6.3 本项目的取舍

本项目不需要同时维护多种 Runner，也不以比较编排框架为目标。计划只使用一条显式 LangGraph 状态机：可并行的视觉与传感器分析节点、证据汇合、诊断、风险策略、审批中断和工单写入。这样可以在面试中解释每个状态、条件分支、失败降级和副作用边界。

## 7. IoT 数据处理方式

IoT Server 把数据分为：

- **资产注册库**：site、asset、asset type、位置、安装传感器等。
- **遥测库**：按 `asset_id` 和 `timestamp` 存放实际观测字段。

值得注意的是它区分：

- `installed_sensors`：注册表声明安装了什么。
- `measured_sensors`：历史记录里实际出现了什么字段。

遥测工具提供：

- 半开 ISO 8601 时窗 `[start, end)`。
- 最多 1000 条的 cursor 分页历史。
- stream extent、latest reading、coverage、numeric statistics。
- 对时区 aware/naive 混用、非法时间戳、非有限数值和保留字段做明确校验。
- 均值与总体标准差由 Python accumulator 计算，不让 LLM估算。

局限是底层为了 benchmark 可复现而依赖 CouchDB 和全流扫描；对于个人 MVP 的小规模模拟数据，SQLite 元数据 + Parquet/CSV 时序文件会更轻量。

## 8. Failure Mode 模块

当前 FMSR 实际暴露三个工具：

- `get_failure_modes(asset_class)`：从数据库读取已知模式。
- `generate_failure_modes(asset_class, max_modes)`：使用 LLM 生成或扩展候选，但不写库。
- `add_failure_modes(...)`：显式写入并合并去重。

几个关键细节：

- asset class 会统一大小写、空格、数字、下划线和连字符。
- 返回 `exhaustive` 和 `source`，不会默认声称列表完整。
- LLM 生成结果带模型来源，并与持久化分开。
- 当前源码明确禁用了 failure-mode/sensor mapping，因为大矩阵工具调用成本和超时风险较高。

本项目将采用“受控 failure mode 库 + 来源”思想，但 MVP 不开放 LLM 自动扩写并直接入库。故障模式、症状、关联传感器和推荐文档由受版本控制的 demo 数据提供；LLM 只在已有候选中做证据综合。

## 9. Time-Series / TSFM 模块

TSFM 是当前最复杂的 server，文档列出 41 个工具，大致分为四组：

1. **任务与证据**：列出标准任务，profile/characterize series，检查并清理数据质量。
2. **模型与特征目录**：用 model card/feature card 描述模型指针、能力、版本和 lineage；不把权重放入目录。
3. **组合与执行**：recipe 指定 estimator、transform、ensemble、conformal、finetune、impute 和 eval，由 Python/sktime 执行。
4. **运行与结果账本**：保存 run record、typed result 和本地大结果文件指针。

异常检测有两条主要路径：

- **Detector path**：解析 detector card，`fit` + `predict`，把不同 sktime 输出规范化成与原序列等长的 0/1 labels 和 anomaly indices。
- **Conformal path**：用 forecasting + conformal interval 预测最近窗口，实际值落在区间外即标异常。

这体现了正确的职责分配：Agent 决定任务、模型和 recipe；算法代码负责拟合、阈值、标签、回测和持久化。

对本项目而言，完整模型目录、228 个特征 extractor、fine-tune 生命周期、GIFT-Eval 和多任务 TSFM 太重。MVP 只实现可解释的阈值规则与 rolling median/MAD 异常检测，输出数据质量、参数、异常区间和证据。Isolation Forest、change-point 或 TSFM 在有基线和评测数据后再加入。

## 10. Work Order 模块

当前 WO Server 包含 9 个读工具和 6 个写工具：查询列表/详情/任务/成本/KPI/日历/人员分派/故障码，以及创建、更新、批准、分派、关闭和取消。

关键设计包括：

- 工单创建后状态为 `WAPPR`，之后可进入 `APPR`、`COMP` 或 `CAN` 等状态。
- 写入时校验 priority、work type、description 和部分来源字段。
- `aob_source` 可以记录 agent、trigger、scenario/evidence 等来源。
- 读模型对现实数据的缺失字段较宽容，写入约束放在数据库设计文档。
- server 本身不调用 LLM，工单是确定性的数据库操作。
- 可通过配置只暴露读工具。

当前模块的“approve”是工单生命周期状态变更，不等同于本项目需要的 Human-in-the-loop 安全审批。当前核心源码没有 LangGraph interrupt、审批令牌绑定或“未获人工批准不得创建高风险工单”的守卫。本项目会把审批作为独立领域对象，并在写工具内部再次校验，而不是只依赖 Agent 选择正确分支。

## 11. Scenario / benchmark 设计

### 11.1 场景数据组织

AssetOpsBench 的 scenario 目录以配置驱动：

```text
scenario_<id>/
├─ question.txt
├─ manifest.json
└─ groundtruth.txt   # 完整 benchmark 场景由 runner/evaluation 使用
```

`manifest.json` 将 collection key 绑定到本场景的 CSV/JSON。通用 loader 根据 `collections.json` 解析，并将每个 key 装入同名 CouchDB 数据库。共享数据放在 `shared/`，场景特有数据与 manifest 同目录。每次场景运行前可 reset 数据库，因此场景之间不会互相污染。

### 11.2 场景意图

论文中的 scenario 不是 API 示例，而是面向运维人员的自然语言意图，type 覆盖 IoT、FMSR、TSFM、WO 或端到端任务。ground truth 可以描述：

- 规划步骤。
- 具体工具动作和参数。
- 步骤间依赖边。
- 最终结构化输出或期望行为。

当前 scenario suite 通过 profile 选择 `car`、`fcc`、`fmsr`、`health`、`tsfm`、`wosr` 类别，逐场景重载数据、运行 Agent、保存 trajectory，再执行 evaluation。完整场景数据主要由外部 scenario root/Hugging Face 数据集提供；当前源码仓库内只有轻量样例。当前 `lite.yaml` 与其 README 摘要也存在数量不一致，说明配置和文档仍在快速演进，实际运行应以配置为准。

### 11.3 本项目如何缩小

本项目不做 100+ 场景 leaderboard。MVP 先维护少量可审阅、可一键复现的多模态场景，每个场景包含资产、图片、时序、知识文档、预期异常、允许的诊断候选、审批预期和工单副作用。重点评估完整业务闭环，而不是覆盖所有工具组合。

## 12. Evaluation 设计

### 12.1 当前代码

当前 evaluation 采用三阶段离线流程：

```text
agent run -> persisted trajectory -> scorer -> per-run / aggregate report
```

`scenario_id` 是 ground truth 与 trajectory 的主要 join key。trajectory 中保存 runner、model、question、answer 和详细 turns/steps。评测会同时汇总：

- 是否通过与 scorer score。
- 按 scenario type 的通过率。
- turn/tool call 数量与 unique tools。
- input/output tokens。
- p50/p95 duration。
- 可估算时的模型费用。

当前真正可用的 scorer 是：

- `static_json`：确定性解析和比较结构化结果，给出 exact/partial、precision、recall、F1、缺失/多余 key 等信息；只有完全匹配才 pass。
- `llm_judge`：六项 rubric，包括完成度、数据检索准确性、结果验证、步骤顺序、清晰度和 hallucination；并有同模型自评阻断。

`exact_string_match`、`numeric_match`、`semantic_similarity` 在该提交中仍是 skeleton，不能当成已实现能力。

### 12.2 论文与当前代码的差异

论文重点比较两类编排范式，使用三项 rubric（任务完成、数据检索、结果验证）和参考轨迹的 ROUGE 对齐，并报告 Pass 指标、步骤数与耗时。当前代码的 judge rubric 已扩展为六项，另加入 `static_json` 与通用报告/ops metrics。

论文也指出 ROUGE 更接近“轨迹文本/结构一致性”，不等于诊断正确性。本项目不会用 ROUGE 证明故障诊断有效。

### 12.3 本项目采用的评测原则

- 事实、数值、引用、工具顺序和副作用优先用确定性 scorer。
- 视觉、传感器、检索、诊断、工作流分别评估，再做端到端评估。
- LLM judge 只评价解释完整性和可读性，不作为数值/事实正确性的唯一依据。
- 保留完整 tool trace，支持更换模型后复评和消融实验。
- 没有真实测试结果前不写准确率、节省时间或业务收益。

## 13. 能力采用与修改矩阵

| AssetOpsBench 能力 | 本项目是否采用 | 如何实现 | 是否修改 |
| --- | --- | --- | --- |
| 工业资产运维闭环场景 | 采用 | 聚焦“图片巡检 + 传感器 + 知识 + 诊断 + 工单”单一闭环 | 是，benchmark 导向改为最终用户应用 |
| 模拟工业数据 | 采用 | 少量泵/电机资产、可解释故障注入、合成说明书/SOP | 是，规模缩小并加入图片与离线 fixture |
| 领域能力模块化 | 采用 | Vision、Sensor、Failure Mode、RAG、Diagnosis、Work Order 分层 | 是，不把每个模块都做成独立进程 |
| FastMCP 领域 Server | 部分采用 | 一个可选 MCP gateway 暴露共享领域服务 | 是，避免六个 server 的部署复杂度 |
| 类型化 Tool schema | 采用 | Pydantic input/output、统一 error、schema version 与 trace ID | 是，增加 evidence ID 与幂等字段 |
| IoT asset/telemetry 分离 | 采用 | SQLite 资产元数据 + Parquet/CSV 时序数据引用 | 是，不使用 CouchDB |
| installed/measured sensor 区分 | 采用 | 资产定义与数据质量报告分别记录 | 否，保留语义 |
| 时窗、分页和数据质量约束 | 采用 | 统一 UTC、半开时窗、小结果直接返回、大结果返回引用 | 是，适配本地文件数据 |
| Failure mode catalog | 采用 | 受控 YAML/SQLite 数据，带来源、症状和关联传感器 | 是，扩展为诊断候选证据 |
| LLM 自动生成并写入 failure mode | MVP 不采用 | 后续只能生成待审核候选，不能自动发布 | 是，增加人工审核与版本控制 |
| Failure mode/sensor 大矩阵生成 | 不采用 | 只维护 demo 资产的小型显式映射 | 是，避免成本和不可审计输出 |
| 完整 TSFM 模型/特征目录 | 不采用 | 后续需求明确后再评估 | 是，个人项目不维护模型生命周期平台 |
| 时序异常确定性执行 | 采用 | operating limit + rolling median/MAD，聚合异常区间 | 是，先做可解释基线，不依赖 foundation model |
| Conformal / TSFM anomaly | Roadmap | 有标注数据和基线后作为可插拔 detector | 是，不进入 MVP |
| Vibration FFT/包络谱 | Roadmap | 若加入轴承高频振动样例再实现 | 是，不为技术展示强行加入 |
| Work order lifecycle | 采用 | draft、approval、created/cancelled 等最小状态 | 是，使用简化字段而非 Maximo 全量模型 |
| 工单来源追踪 | 采用 | work order 保存 diagnosis/evidence/workflow run 引用 | 是，加入审批记录和 draft hash |
| 只读/写工具隔离 | 采用 | Tool 元数据区分 read/write；写工具内部执行权限检查 | 是，增加高风险审批守卫 |
| 多种 Agent Runner 对比 | 不采用 | MVP 只维护 LangGraph | 是，本项目不做编排框架 benchmark |
| Plan/trajectory 持久化 | 采用 | 保存节点状态、tool trace、模型和耗时 | 是，适配 LangGraph checkpoint |
| scenario manifest | 采用 | 每个 demo/eval case 自包含输入与期望结果 | 是，使用本地文件与 SQLite，不重置 CouchDB |
| 离线 trajectory evaluation | 采用 | 运行和评分分离，可对已有 trace 重评 | 否，保留核心思想 |
| LLM-as-a-judge | 部分采用 | 仅用于说明质量；不能覆盖确定性断言 | 是，降低其权威性并避免同模型自评 |
| Web Dashboard | AssetOpsBench 当前核心未提供 | 本项目提供资产选择、上传、曲线、证据、审批和工单 UI | 本项目原创重点 |
| 多模态视觉巡检 | AssetOpsBench 当前核心未提供完整流程 | provider adapter + schema-constrained findings + fixture mode | 本项目原创重点 |
| 维修知识 RAG | AssetOpsBench 当前核心未提供 | FAISS + 带页码/章节/chunk 的可引用证据 | 本项目原创重点 |
| Human-in-the-loop 工单审批 | AssetOpsBench 当前核心未提供 | LangGraph interrupt + 审批记录 + 写工具二次校验 | 本项目原创重点 |

## 14. 值得学习、过于复杂与明确不采用

### 值得学习

- 工业人员意图驱动的场景，而不是工具覆盖驱动的问题集合。
- Tool 输入输出 schema、错误边界、来源和结果索引。
- 大时序结果不直接进入 LLM，上下文只保留结构化证据摘要。
- 数值计算、DSP、异常检测和数据库写入由确定性代码完成。
- trajectory 先保存、evaluation 后执行。
- 写工具与读工具明确区分，工单保留触发来源。

### 对个人项目过于复杂

- 41-tool TSFM 平台、模型/特征 card 生命周期、fine-tune lineage。
- 多个 Agent SDK 和多个 orchestration 范式并行维护。
- CouchDB 多集合、场景 reset、外部大数据集和 leaderboard 基础设施。
- 大型 failure-mode/sensor 自动生成矩阵。
- 在没有对应业务场景时引入完整振动 DSP、预测、分类、聚类等任务面。

### 明确不采用

- 不复制 AssetOpsBench 目录后改名。
- 不复制其源码、prompt 或 Maximo 字段全集。
- 不把 IBM 的论文指标、场景数量、测试结果写成本项目成绩。
- 不为了“看起来像多 Agent”创建多个只做转发的 Agent。
- 不让 LLM 直接计算异常分数、直接编造故障模式或绕过审批写工单。
- 不在 MVP 建排行榜、比赛平台、模型目录或生产级流式 IoT 平台。

## 15. 对本项目设计的直接影响

本项目将 AssetOpsBench 的“可组合工业工具 + 轨迹评测”思想改造成一条面向最终用户的窄而完整的产品路径：

```text
选择设备 -> 上传图片 -> 读取模拟传感器 -> 确定性异常检测
-> 查询故障模式 -> 检索维修文档 -> 多模态证据诊断
-> 风险策略 -> 人工审批 -> 创建可追踪工单
```

真正原创和需要自行实现的部分是：视觉证据 schema、视觉与时序的证据融合、带引用的维修 RAG、LangGraph 可恢复审批、审批与写工具的双重守卫、用户 Dashboard，以及针对这个闭环的多模态 scenario/evaluation。

## 16. 主要参考资料

- [AssetOpsBench repository at reviewed commit](https://github.com/IBM/AssetOpsBench/tree/e11d1c1b2022db0396364a6d66e24168955a3bb7)
- [README](https://github.com/IBM/AssetOpsBench/blob/e11d1c1b2022db0396364a6d66e24168955a3bb7/README.md)
- [MCP servers](https://github.com/IBM/AssetOpsBench/blob/e11d1c1b2022db0396364a6d66e24168955a3bb7/docs/mcp-servers.md)
- [CouchDB data layer](https://github.com/IBM/AssetOpsBench/blob/e11d1c1b2022db0396364a6d66e24168955a3bb7/docs/data.md)
- [Evaluation](https://github.com/IBM/AssetOpsBench/blob/e11d1c1b2022db0396364a6d66e24168955a3bb7/docs/evaluation.md)
- [Scenario suite runner](https://github.com/IBM/AssetOpsBench/blob/e11d1c1b2022db0396364a6d66e24168955a3bb7/benchmarks/scenario_suite/README.md)
- [IoT MCP source](https://github.com/IBM/AssetOpsBench/blob/e11d1c1b2022db0396364a6d66e24168955a3bb7/src/servers/iot/main.py)
- [FMSR MCP source](https://github.com/IBM/AssetOpsBench/blob/e11d1c1b2022db0396364a6d66e24168955a3bb7/src/servers/fmsr/main.py)
- [TSFM MCP source](https://github.com/IBM/AssetOpsBench/blob/e11d1c1b2022db0396364a6d66e24168955a3bb7/src/servers/tsfm/main.py)
- [Work Order MCP source](https://github.com/IBM/AssetOpsBench/blob/e11d1c1b2022db0396364a6d66e24168955a3bb7/src/servers/wo/main.py)
- [Plan-Execute runner](https://github.com/IBM/AssetOpsBench/blob/e11d1c1b2022db0396364a6d66e24168955a3bb7/src/agent/plan_execute/runner.py)
- [AssetOpsBench paper v3](https://arxiv.org/abs/2506.03828v3)
