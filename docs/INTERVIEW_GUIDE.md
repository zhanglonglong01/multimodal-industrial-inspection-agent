# Interview Guide

这是一份基于真实实现的技术复盘，不是需要背诵的营销稿。面试时应主动说明数据规模、fixture 边界和未验证项。

## 项目背景

工业巡检判断通常依赖现场图片、传感器历史、故障模式和维修文档。项目用两个 synthetic asset 和三个固定 scenario，演示从证据采集到高风险审批和 WorkOrder 的完整软件闭环；同时用 MetroPT-3 的两个真实运行 APU 传感器窗口检验 synthetic detector 的外部适配边界。它仍不代表真实工厂部署或工业准确率。

## 核心设计问答

### 为什么做这个项目？

它能同时展示后端工程、时序算法、RAG、受控 LLM、持久化 workflow、HITL、安全副作用、评测和 Web 产品化，而不是只做一次模型问答。

### 为什么使用 Agent / LangGraph？

任务具有状态、条件分支、降级、interrupt 和跨进程 resume。LangGraph 保存小型结构化 state，并根据 evidence/risk/decision 路由。固定数值算法仍是普通 service。

### 为什么不是 Multi-Agent？

Vision、Sensor、RAG、Risk、WorkOrder 的职责和输入输出明确，不需要各自扮演会对话的 Agent。一个 workflow 更容易调试、评测和解释，也避免冗余 LLM 调用。

### 为什么 Sensor 不交给 LLM？

阈值、rolling median/MAD、数据质量和异常区间是确定性数值任务。Python 输出可复现指标和 Evidence ID；LLM 只消费摘要，不能猜测原始序列。

### 为什么使用 RAG？

维修建议需要文档依据。RAG 返回可展开的 chunk、section、score 和 source，使 Diagnosis 只能引用已检索证据，而不是依赖模型记忆。

### 为什么选择 FAISS？

知识库只有三份 synthetic 文档，单机 FAISS 无外部服务，能够从源文件重建。Qdrant/PostgreSQL 在当前规模只增加运维成本。

### 为什么需要 HITL？

创建工单是有副作用的动作。HIGH/CRITICAL 路径先持久化 immutable Draft，然后 interrupt；审批后 resume，WorkOrder service 仍重新检查 hash、decision、risk 和 status。

### 为什么使用 SQLite？

Portfolio demo 的单机写入量很小。SQLite 同时满足 metadata、approval、WorkOrder 和 LangGraph checkpoint 的持久化需求，便于一键运行与测试。

### 为什么使用 Provider abstraction？

Fixture Provider 保证无 Key、无网络、无费用、可重复测试；OpenAI Provider 使用同一 schema，因此替换 provider 不需要重写 workflow。真实结果不会反向修改 fixture ground truth。

### AssetOpsBench 带来了什么启发？

参考了工业资产运维、IoT/time-series、failure-mode、work-order、tool 与 evaluation 的场景组织思想。项目不是 fork，没有复制 benchmark 源码或成绩；这里优化的是小型最终用户应用和多模态展示。

## 最大技术难点

1. 让 Vision/Sensor/RAG evidence 共享可校验 ID，同时防止 Diagnosis 引用不存在的证据。
2. 将 LangGraph interrupt/checkpoint 与数据库 Draft/Approval/WorkOrder 状态保持一致。
3. 在 retry、restart 和 duplicate create 下保存独立 ToolTrace，同时保持业务幂等。
4. 保证正常和双证据失败路径不会产生维修 Draft 或副作用。
5. 将 fixture 与真实 Provider 解耦，且不把 scenario ground truth 泄漏给模型。

## 实际遇到的 Bug

- Normal scenario 最初仍生成 WorkOrderDraft；通过 actionable fault guard 修正并增加回归测试。
- ToolTrace ID 最初与业务幂等混在一起，重复失败可能覆盖；改为独立 trace/attempt。
- Phase 4 自定义上传表单把 `DOMStringMap` 写入 dataset 字段；浏览器 E2E 捕获并修正属性访问。
- Timeline 曾把已有 Draft 误显示为 WorkOrder complete；改为只在正式 WorkOrder 存在时完成。
- Phase 4 Web 外键使 `seed-demo` 的 DELETE 重置失败；Phase 5 改为 upsert，保留 Inspection 引用。
- Starlette TestClient 回退旧 `httpx` 产生迁移 warning；安装当前支持的 `httpx2`，未用 warning ignore。
- MetroPT-3 附带 PDF 写 15,169,480 点/1 Hz，而实际 CSV 和 UCI 当前记录约 1,516,948 点/0.1 Hz；实现固定 ZIP/CSV 双哈希并以实际文件统计为准，文档公开差异。

## 至少 20 个可能的技术追问

1. **Ground truth 如何隔离？** Workflow 只接收运行输入；scenario manifest 仅在所有 run 完成后的 scorer 中打开。
2. **OpenAI Vision 看到了什么输入？** 图片 data URL、asset type/name/description、有限 label vocabulary；没有 scenario ID、expected fault 或 ground truth。
3. **如何限制 Vision 不直接诊断？** 枚举只包含 visual observations，system instruction 禁止故障确认，最终输出再经 Pydantic 校验。
4. **上传为什么安全？** 限制大小、扩展名、MIME、Pillow 实际格式、像素数；使用 hash filename 和 data-root path confinement。
5. **Evidence ID 如何验证？** Diagnosis 返回后与传入 EvidenceRef 集合求差，出现未知 ID 直接失败。
6. **MAD 为零怎么办？** detector 有显式 fallback score，并把参数写入评测报告。
7. **异常 segment 如何形成？** 同 sensor、方向一致且间隔不超过配置 gap 的异常点聚合，过滤最小点数。
8. **正常场景 F1 为什么不写 100%？** 无正样本时正类 F1 没有信息量，报告为 Normal-case pass。
9. **Risk 为什么不让 LLM 决定？** 风险影响副作用，应由 versioned deterministic matrix 产生并可审计。
10. **Approval 为什么不能只靠前端？** 前端只提交 decision；服务端 resume 且 create boundary 再检查批准和 Draft hash。
11. **如何阻止重复 WorkOrder？** `draft_id` 唯一、稳定 idempotency key、repository 唯一约束，重复调用返回同一记录。
12. **进程重启后如何恢复？** LangGraph SQLite checkpoint 使用 `run_id/thread_id`；新的 runtime 调 `Command(resume=...)`。
13. **ToolTrace 与幂等是什么关系？** Trace 记录每次 attempt；业务幂等由 Draft/Approval/WorkOrder 唯一性独立保证。
14. **为什么 checkpoint 不存图片和完整 CSV？** state 只存 artifact/dataset ID 和小型结果，避免膨胀和不可序列化对象。
15. **RAG 如何避免跨设备误检索？** search 使用 asset type metadata filter，返回 chunk metadata 和 score。
16. **真实 Vision 如何测试而不影响 CI？** `RUN_LIVE_TESTS=1` 显式 opt-in；默认测试只用 fake client 或 Fixture Provider。
17. **Degraded mode 如何路由？** 单一 modality 失败时保留 warning 并降低 evidence strength；两者都失败时直接 insufficient evidence。
18. **API 如何避免泄漏内部状态？** Run DTO 只返回 summary、diagnosis/risk/approval/WorkOrder status，不返回 Key、prompt 或 checkpoint binary。
19. **为什么不是实时系统？** 数据是历史 CSV window，同步执行；项目没有 broker、stream processor 或在线 SLA。
20. **评测 3/3 能说明什么？** 只说明三个 versioned synthetic tasks 达到预设行为，不说明工业准确率或泛化。
21. **如果 FAISS index 损坏怎么办？** `/ready` 会失败；index 可由 tracked synthetic documents 和 metadata 重建。
22. **如果 Provider 返回 schema 外 label？** Structured parse/Pydantic enum 拒绝，不能回退成未校验自由文本。
23. **为什么 API/Template 不直接查数据库？** route 只调用 application service，避免在展示层复制风险、审批和查询规则。
24. **CSRF 的边界？** 同站表单使用 double-submit token；这是 demo 基础措施，不等于生产身份认证。
25. **CI 如何保证不付费？** 固定 `APP_MODE=demo`、`VISION_PROVIDER=fixture`、`RUN_LIVE_TESTS=0`，不注入 Key。
26. **真实数据来自哪里？** MetroPT-3，来自实际运行地铁列车的压缩机 APU；UCI DOI `10.24432/C5VW3R`，CC BY 4.0。
27. **为什么不把 MetroPT-3 接进多模态 Workflow？** 它没有同步巡检图片、维修手册或点级异常标签；强行拼接 synthetic 图片会造成来源误导。
28. **为什么真实数据不报告 precision/recall/F1？** 企业报告只给 Air-leak 事件时间窗，没有每个传感器点的异常标签或方向。
29. **真实数据结果为什么反而较差？** 参考窗口告警率高于故障窗口，说明 rolling MAD 把压缩机启停状态切换当成异常，当前 detector 未针对该设备校准。

## 项目限制

面试时必须主动说明：完整 Workflow 只有两台 synthetic asset、三个 synthetic scenario、四个手工 retrieval query、三张 schematic image；MetroPT-3 只增加两个真实铁路 APU 传感器窗口，不是工厂或真实多模态验证；真实 OpenAI Vision 在无 Key 环境未 live verified；无 PLC/SCADA、生产认证、实时处理和安全验证。

## 如果进入生产如何扩展

先从需求和安全评审开始，而不是直接换基础设施。需要真实标注数据、provider calibration、模型/数据漂移监控、RBAC/审计、对象存储、作业队列、PostgreSQL、设备数据接入、故障隔离、人工 SOP、灰度部署和事故回滚。任何自动维修动作都必须经过企业安全策略与专业工程师验证。
