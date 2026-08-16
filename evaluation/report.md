# Portfolio MVP Evaluation Report

> Synthetic end-to-end task evaluation plus real sensor event-window analysis; not industrial accuracy or factory validation.

## Run Metadata

- Commit: `75acfcc` (dirty: `True`)
- Timestamp: `2026-08-16T15:29:45.439577+00:00`
- Mode/providers: `demo` / `fixture` / `fixture_diagnosis`
- Scope: 3 scenarios; scenario schema ['1.0']; dataset schema ['1.0']

## Sensor Evaluation

| Level | Precision | Recall | F1 | TP | FP | FN |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Point | 1.0000 | 0.8917 | 0.9427 | 107 | 0 | 13 |
| Segment | 1.0000 | 1.0000 | 1.0000 | 4 | 0 | 0 |

Normal scenario: **Normal-case pass** (no expected or predicted anomaly points/segments); positive-class F1 is not reported.

## Real Sensor Event-Window Analysis

MetroPT-3 is real operational railway APU data. It has company failure-event reports, not point-level anomaly labels, so precision/recall/F1 are not reported.

| Window | Relation | Alerted timestamps | Alert rate | Alerted sensors |
| --- | --- | ---: | ---: | --- |
| METROPT3-REFERENCE-20200410 | outside_reported_failure | 131 | 36.39% | compressor_pressure, motor_current |
| METROPT3-AIR-LEAK-20200418 | within_reported_failure | 3 | 0.83% | pneumatic_panel_pressure |

The outside-report reference window is not verified healthy. Its higher alert rate demonstrates that the synthetic-demo detector is not calibrated for MetroPT-3 operating-state transitions.

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

Collected 105; passed 104; skipped 1; failed 0; errors 0.

## Measured Runtime

- `sensor_evaluation_ms`: 48.15 ms
- `retrieval_evaluation_ms`: 9.47 ms
- `workflow_evaluation_ms`: 566.07 ms
- `graph_path_evaluation_ms`: 1281.29 ms
- `safety_evaluation_ms`: 251.26 ms
- `real_sensor_evaluation_ms`: 82.86 ms

## Limitations

- The end-to-end workflow evaluation still contains three synthetic scenarios only.
- MetroPT-3 contributes two real operational sensor windows, not a real multimodal workflow.
- Four manually defined retrieval queries only.
- Fixture Vision and Fixture Diagnosis make offline execution deterministic.
- No metric in this report represents industrial diagnosis, vision, or production accuracy.
