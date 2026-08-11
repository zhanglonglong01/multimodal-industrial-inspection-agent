# Multimodal Industrial Inspection & Fault Diagnosis Agent

A portfolio-scale application that demonstrates an evidence-driven industrial inspection workflow using synthetic equipment data. It is an engineering practice project—not a real factory deployment or a production maintenance system.

## Implemented Features

- FastAPI API and Jinja2/HTMX dashboard for two demo assets and three scenarios.
- A single persistent LangGraph workflow with Fixture Vision, deterministic sensor anomaly detection, controlled failure modes, FAISS maintenance retrieval, structured diagnosis and deterministic risk policy.
- Real LangGraph `interrupt` / `Command(resume=...)` approval flow with protected, idempotent work-order creation.
- Plotly.js sensor curves with operating limits and highlighted anomaly windows.
- Evidence-linked Vision, sensor, RAG, diagnosis, risk, approval and WorkOrder views.
- Validated PNG/JPEG/WebP upload storage using content hashes and generated filenames.
- SQLite, CSV, FAISS and local artifact storage; no external infrastructure is required.

All included industrial assets, sensor readings, images, manuals, findings and evaluation labels are synthetic.

## Quick Start

Requires Python 3.11+.

```bash
python -m pip install -e ".[dev]"
inspection-agent init-web-demo
uvicorn inspection_agent.web:app --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The API documentation is available at <http://127.0.0.1:8000/docs>.

Docker demo:

```bash
docker compose up --build
```

The container initializes the demo database, FAISS index and writable artifact directory before starting the app. SQLite/checkpoints/uploads are retained in the `inspection-runtime` volume.

## Demo Mode

`APP_MODE=demo` is the default and requires no API key. It always uses:

- synthetic assets and sensor CSVs;
- `FixtureVisionProvider` (preset fixture findings, not pixel inference);
- `FixtureDiagnosisProvider`;
- synthetic maintenance documents and deterministic local embeddings.

The three quick-start scenarios are:

- `SCENARIO-001`: pump seal leakage → CRITICAL → approval can create a WorkOrder;
- `SCENARIO-002`: motor bearing anomaly → HIGH → reject creates no WorkOrder;
- `SCENARIO-003`: normal equipment → no actionable fault, draft, approval or WorkOrder.

`APP_MODE=live` is reserved for implemented provider adapters. The current OpenAI diagnosis adapter requires `INSPECTION_OPENAI_API_KEY`; no real multimodal Vision adapter or end-to-end live configuration has been verified.

## Screenshots

Screenshots and a final demo recording will be added after UI review. Current pages include the asset fleet, inspection setup, evidence-rich run detail, approval gate and WorkOrder detail.

## Known Limitations

- Portfolio demo; **not production-ready** and not connected to a factory, PLC, SCADA or CMMS.
- Fixture Vision does not inspect uploaded pixels. Uploaded images are validated and displayed, while demo findings come from the selected versioned fixture.
- Synthetic scenario results do not establish accuracy on real industrial data.
- SQLite and the synchronous workflow runtime target a single-container demonstration, not concurrent distributed execution.
- CSRF protection uses a basic same-site double-submit strategy; there are no user accounts, OAuth, RBAC or enterprise security controls.
- Plotly.js and HTMX are loaded from public CDNs in the current demo UI.
