# Portfolio MVP Evaluation Report

> Synthetic scenario task success report; not industrial accuracy or factory validation.

## Run Metadata

- Commit: `a1d7270` (dirty: `True`)
- Timestamp: `2026-08-11T01:40:42.292720+00:00`
- Mode/providers: `demo` / `fixture` / `fixture_diagnosis`
- Scope: 3 scenarios; scenario schema ['1.0']; dataset schema ['1.0']

## Sensor Evaluation

| Level | Precision | Recall | F1 | TP | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Point | 1.0000 | 0.8917 | 0.9427 | 107 | 0 | 13 |
| Segment | 1.0000 | 1.0000 | 1.0000 | 4 | 0 | 0 |

Normal scenario: **Normal-case pass** (no expected or predicted anomaly points/segments); positive-class F1 is not reported.

## Retrieval Evaluation

Only **4 manually defined retrieval queries**.

| Recall@1 | Recall@3 | MRR |
| ---: | ---: | ---: |
| 1.0000 | 1.0000 | 1.0000 |

## Workflow Evaluation

Synthetic scenario task success: **3/3 (100.0%)**.

| Scenario | Candidate | Evidence | Interrupt | WorkOrder side effect | Pass |
| --- | --- | --- | --- | --- | --- |
| SCENARIO-001 | True | True | True | True | True |
| SCENARIO-002 | True | True | True | True | True |
| SCENARIO-003 | True | True | True | True | True |

## Safety and Idempotency

Passed **5/5** deterministic checks.

- `high_risk_approval_enforcement`: **PASS**
- `direct_bypass_rejection`: **PASS**
- `work_order_idempotency`: **PASS**
- `restart_resume`: **PASS**
- `dual_evidence_failure`: **PASS**

## Tests

Collected 102; passed 101; skipped 1; failed 0; errors 0.

## Measured Runtime

- `sensor_evaluation_ms`: 45.71 ms
- `retrieval_evaluation_ms`: 7.04 ms
- `workflow_evaluation_ms`: 449.41 ms
- `graph_path_evaluation_ms`: 1066.27 ms
- `safety_evaluation_ms`: 210.67 ms

## Limitations

- Three synthetic scenarios only; results are Portfolio evaluation scenario outcomes.
- Four manually defined retrieval queries only.
- Fixture Vision and Fixture Diagnosis make offline execution deterministic.
- No metric in this report represents industrial diagnosis, vision, or production accuracy.
