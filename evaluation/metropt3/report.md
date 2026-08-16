# MetroPT-3 Real Sensor Evaluation

> Event-window analysis of real operational railway APU sensor data; not factory or multimodal validation.

- Source DOI: `10.24432/C5VW3R`
- License: `CC BY 4.0`
- Profile: `metropt3-real-sensor-v1`
- Detector: `rule_based_and_rolling_mad`

| Window | Relation to company report | Points | Segments | Alerted timestamps | Alert rate |
| --- | --- | ---: | ---: | ---: | ---: |
| METROPT3-REFERENCE-20200410 | outside_reported_failure | 170 | 25 | 131 | 36.39% |
| METROPT3-AIR-LEAK-20200418 | within_reported_failure | 3 | 1 | 3 | 0.83% |

## Limitations

- No point-level precision, recall, or F1 is reported because MetroPT-3 provides event reports rather than point labels.
- The outside-report window is a reference window, not verified healthy ground truth.
- Alerts can reflect normal compressor operating-state transitions.
- Configured operating ranges are analysis guardrails, not validated manufacturer limits.
- The selected profile contains real sensors only; Vision, RAG, diagnosis, and WorkOrder remain synthetic-demo capabilities.
