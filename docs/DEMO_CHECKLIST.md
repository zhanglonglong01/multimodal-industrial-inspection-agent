# Portfolio MVP Demo Checklist

本清单用于每次公开演示或发布前复核。所有结果必须来自 synthetic/demo 数据；不要在录屏、终端或浏览器中显示 `.env`、API Key、真实用户信息或本地隐私路径。

## 1. Fresh install and startup

- [x] 从当前 `pyproject.toml` 执行 `python -m pip install -e ".[dev]"`。
- [x] 执行 `inspection-agent init-web-demo`，确认 SQLite、2 个资产、FAISS index、runtime storage 均 ready。
- [x] 启动 `uvicorn inspection_agent.web:app --host 127.0.0.1 --port 8000`。
- [x] 打开 Dashboard，确认顶部显示 `Synthetic industrial data / Fixture Vision / Fixture Diagnosis`。
- [x] 查看 `PUMP-001`、`MOTOR-001` 资产卡和传感器定义。

## 2. Three scenario demo

### SCENARIO-001 — Pump seal leakage

- [x] 点击 `Load SCENARIO-001` 并启动 workflow。
- [x] 查看 Vision finding、三个传感器图、异常窗口和 operating limits。
- [x] 展开 RAG evidence，检查 title、section、score、chunk ID。
- [x] 查看 structured diagnosis、Evidence ID 和 deterministic `CRITICAL` risk。
- [x] 确认 WorkOrder Draft ID 存在，然后输入 reason 并 Approve。
- [x] 确认真实 LangGraph resume 后只创建一张 WorkOrder。

### SCENARIO-002 — Motor bearing fault

- [x] 点击 `Load SCENARIO-002` 并启动 workflow。
- [x] 确认 diagnosis 为受控 motor bearing candidate，risk 为 `HIGH`。
- [x] Reject 并记录 reason。
- [x] 确认 workflow 完成但没有 WorkOrder。

### SCENARIO-003 — Normal equipment

- [x] 点击 `Load SCENARIO-003` 并启动 workflow。
- [x] 确认 `No actionable fault detected`。
- [x] 确认无 Draft、无 Approval、无 WorkOrder。

## 3. Persistence and safety

- [x] 自动测试覆盖：interrupt 后关闭 runtime、重建 runtime、resume 成功。
- [x] 自动测试覆盖：无审批直接创建 HIGH/CRITICAL WorkOrder 被拒绝。
- [x] 自动测试覆盖：同一 Draft 重复 create 返回同一 WorkOrder，数据库只有一条记录。
- [x] 自动测试覆盖：Draft hash 被修改后创建被阻止。
- [x] 自动测试覆盖：Vision degraded、Sensor degraded、双证据失败。
- [x] 双证据失败时确认 `INSUFFICIENT_EVIDENCE`，且无 Draft/Approval/WorkOrder。

## 4. Operations and quality

- [x] `GET /health` 返回 HTTP 200 和 `alive`。
- [x] `GET /ready` 返回 HTTP 200，四项 readiness 全部通过。
- [x] `ruff check .`。
- [x] `mypy src`。
- [x] `python -m pytest`。
- [x] `inspection-agent evaluate` 生成 JSON 和 Markdown 报告。
- [x] `inspection-agent check-hygiene`。
- [x] `python -m build`。
- [x] `git diff --check`。

## 5. Release gates requiring external state

- [ ] 用户审核 Phase 5 后提交代码与 evaluation report，并确认 `git status` clean。
- [ ] GitHub Actions CI 实际 green。当前只完成 workflow 文件与本地可执行步骤验证；推送后检查。
- [ ] GitHub Actions Docker build + `/health` + `/ready` smoke 实际 green。只有 green 后 README 才能写 “verified by CI”。
- [ ] OpenAI Vision 三图 live smoke。当前无可用 Key；保持 `REAL VISION PROVIDER IMPLEMENTED BUT NOT LIVE VERIFIED`。
- [ ] 用户确认后创建 `v1.0.0` tag；本阶段禁止自动 tag/push/release。

## 6. Demo artifacts

- [x] `docs/images/01-dashboard.png`
- [x] `docs/images/02-pump-critical.png`
- [x] `docs/images/03-evidence.png`
- [x] `docs/images/04-approval.png`
- [x] `docs/images/05-work-order.png`
- [x] `docs/images/06-normal.png`
- [x] `docs/images/demo.gif`（7 帧，31.5 秒）

最后本地人工演示日期：2026-08-11。外部 CI、Docker CI 和真实 Vision 状态以发布门禁区为准。
