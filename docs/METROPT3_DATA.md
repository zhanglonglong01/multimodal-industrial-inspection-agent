# MetroPT-3 Real Sensor Data Profile

## Purpose and boundary

This profile adds real operational sensor measurements without relabeling them as a real multimodal factory scenario. MetroPT-3 was collected from the compressor Air Production Unit (APU) of a metro train in operation. The project retains its synthetic image/RAG/HITL scenarios and evaluates MetroPT-3 separately as sensor-only data.

It is accurate to say that the repository uses **real industrial-equipment operational sensor data**. It is not accurate to say that the repository contains real factory production-line data, synchronized real inspection images, or a factory-validated diagnosis workflow.

## Official source and license

- Dataset: MetroPT-3 Dataset
- Creators: Narjes Davari, Bruno Veloso, Rita P. Ribeiro, and Joao Gama
- UCI DOI: <https://doi.org/10.24432/C5VW3R>
- UCI record: <https://archive.ics.uci.edu/dataset/791/metropt%2B3%2B>
- Dataset paper: <https://doi.org/10.1038/s41597-022-01877-3>
- License: CC BY 4.0
- Official archive SHA-256: `aab991a970e58210de853bb8078ce0e63abb4d9412fdc5c79792dae3d8e1721a`
- Source CSV SHA-256: `db30ccb4ea402e3c8bf2c99db06e288d4f2a772f6928f9dbe26a920d69793e24`

The downloaded archive is stored under ignored `data/runtime/metropt3/`. Only two small attributed derived windows are versioned.

## Source-file verification

The actual UCI CSV contains 1,516,948 rows from `2020-02-01 00:00:00` through `2020-09-01 03:59:50`. Its dominant observed timestamp interval is 10 seconds, with 9-, 11-, 12-, and 13-second intervals also present.

The PDF bundled in the archive says 15,169,480 points at 1 Hz, while the current UCI page and actual CSV indicate approximately 1.5 million points at 0.1 Hz. This project treats the downloaded CSV as the file-level source of truth and records both source hashes so the discrepancy is visible rather than silently normalized.

The source timestamps do not declare a timezone. The project preserves their clock values and appends `+00:00` only to satisfy the application's timezone-aware timestamp contract. This is a storage normalization, not a claim that the original clock used UTC.

## Selected columns

| MetroPT-3 column | Project column | Unit | Meaning |
| --- | --- | --- | --- |
| `TP2` | `compressor_pressure` | bar | Pressure on the compressor |
| `TP3` | `pneumatic_panel_pressure` | bar | Pressure at the pneumatic panel |
| `Oil_temperature` | `oil_temperature` | degC | Compressor oil temperature |
| `Motor_current` | `motor_current` | A | Current on one phase of the compressor motor |

The values are grouped into one-minute buckets using arithmetic means. No interpolation, label synthesis, or missing-minute imputation is performed. Preparation fails if a selected minute is absent.

## Versioned windows

| Window | Source clock range | Raw rows | Derived rows | Relation to published reports |
| --- | --- | ---: | ---: | --- |
| `METROPT3-REFERENCE-20200410` | 2020-04-10 01:00-07:00 | 2,179 | 360 | Outside all published failure reports; not guaranteed healthy |
| `METROPT3-AIR-LEAK-20200418` | 2020-04-18 01:00-07:00 | 2,180 | 360 | Inside the published 2020-04-18 Air leak / High stress report |

The company reports four Air leak event windows. They are transcribed into `data/real/metropt3/manifest.json`. MetroPT-3 does not provide point-level labels identifying a specific sensor or anomaly direction, so this project does not invent them.

## Reproduction commands

```bash
inspection-agent download-metropt3
inspection-agent prepare-metropt3
inspection-agent validate-metropt3
inspection-agent evaluate-metropt3
```

`download-metropt3` pins and validates the official ZIP. `prepare-metropt3` validates the inner CSV hash before rebuilding the two windows. CI runs validation and evaluation entirely offline against the committed derived files.

## Measured baseline and interpretation

With the unchanged synthetic-demo rolling median/MAD detector:

- Outside-report reference window: 131/360 timestamps alerted (36.39%).
- Reported Air-leak window: 3/360 timestamps alerted (0.83%).

This is intentionally not reported as accuracy. The reference window is not verified healthy, and compressor start/stop state transitions can trigger the detector. The result demonstrates that a detector tuned for synthetic pump/motor fixtures is not calibrated for MetroPT-3. No threshold or ground truth was modified to improve the result.

## What remains synthetic

- All three end-to-end LangGraph scenarios
- Inspection images and Fixture Vision outputs
- Maintenance manuals, SOP, failure-mode catalog, and RAG queries
- Diagnosis, risk, approval, and WorkOrder outcomes

MetroPT-3 currently strengthens only the real sensor-data and provenance portions of the portfolio.
