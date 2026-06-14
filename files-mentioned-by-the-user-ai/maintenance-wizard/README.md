# Maintenance Wizard

Agentic AI challenge prototype for steel-plant maintenance decision support.

## What It Does

- Accepts equipment alarms, delay logs, failure context, sensor snapshots, and engineer queries.
- Produces probable diagnosis, root causes, risk classification, urgency, RUL estimate, early-warning flags, maintenance actions, spare strategy, and traceable evidence.
- Generates a structured Markdown maintenance report in `reports/`.
- Stores engineer feedback in `data/feedback.jsonl` for a future learning loop.
- Supports an optional OpenAI call when `OPENAI_API_KEY` is configured; otherwise it runs fully offline with the deterministic reasoning pipeline.

## Architecture

- `server.py` serves the web UI and JSON APIs using Python standard-library HTTP tools.
- `static/index.html`, `static/styles.css`, and `static/app.js` implement the browser dashboard.
- `data/sensor_snapshot.csv` simulates live condition-monitoring input.
- The reasoning pipeline acts like a small agent workflow:
  1. Knowledge retriever: loads equipment manuals, SOPs, spares, and sensor snapshot.
  2. Fault classifier: matches symptom patterns from fault text and logs.
  3. Risk scorer: combines condition anomalies, process criticality, delay impact, and procurement constraints.
  4. RUL estimator: estimates remaining useful life from a rule-based health index.
  5. Planner: creates immediate actions, long-term monitoring, and spare procurement recommendations.
  6. Reporter: writes a structured decision report.

## How To Run

From this folder:

```powershell
python server.py
```

Then open:

```text
http://127.0.0.1:8000
```

If `python` does not work on your machine, use the bundled Python from this Codex environment:

```powershell
& "C:\Users\Ashutosh Tiwary\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe" server.py
```

## Optional LLM Mode

Set an OpenAI API key before running the server:

```powershell
$env:OPENAI_API_KEY="your_api_key_here"
$env:OPENAI_MODEL="gpt-4.1-mini"
python server.py
```

The app still returns the rule-based analysis if the API key is missing or the LLM call fails.

## Demo Scenario

Click **Load Demo Scenario** in the UI. It loads a blast-furnace blower case with high vibration, hot bearing symptoms, production delay, and a spares question.

## API Examples

Analyze a case:

```powershell
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/analyze -ContentType "application/json" -Body '{
  "equipment_id": "BF-01",
  "fault_message": "High vibration and hot bearing alarm",
  "delay_log": "90 minute production delay",
  "query": "What should we do first?"
}'
```

Get simulated sensors:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/sensors
```

## Assumptions And Limits

- The included model is a transparent rule-based prototype suitable for hackathon demonstration.
- Production deployment should replace the sample CSV with plant historian or IoT integration.
- RUL estimates are directional, not safety certification outputs.
- Human maintenance approval is required before physical intervention.
