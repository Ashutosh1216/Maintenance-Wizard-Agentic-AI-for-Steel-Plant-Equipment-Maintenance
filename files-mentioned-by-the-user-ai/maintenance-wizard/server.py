from __future__ import annotations

import csv
import json
import math
import os
import re
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
DATA_DIR = ROOT / "data"
REPORTS_DIR = ROOT / "reports"
FEEDBACK_FILE = DATA_DIR / "feedback.jsonl"


RISK_WEIGHTS = {
    "low": 1,
    "medium": 2,
    "high": 3,
    "critical": 4,
}


EQUIPMENT_KB = {
    "BF-01": {
        "name": "Blast Furnace Top Gas Blower",
        "criticality": 5,
        "manual": "Check vibration spectrum, coupling alignment, bearing temperature, lube oil pressure, and seal gas flow before restart.",
        "sop": [
            "Switch blower to safe maintenance state and notify shift in-charge.",
            "Inspect bearing housing and lube oil return for contamination.",
            "Verify coupling alignment and foundation bolt torque.",
            "Run no-load trial for 20 minutes and confirm vibration is below 7 mm/s.",
        ],
        "spares": {"bearing_set": {"stock": 1, "lead_days": 18}, "coupling": {"stock": 0, "lead_days": 35}},
    },
    "RM-02": {
        "name": "Rolling Mill Stand 2 Gearbox",
        "criticality": 4,
        "manual": "Gearbox defects are often visible in oil debris, tooth mesh frequency, abnormal heat, and load-side vibration.",
        "sop": [
            "Reduce mill speed and check oil temperature trend.",
            "Collect oil sample and inspect magnetic plug debris.",
            "Inspect gear tooth pattern during next planned stoppage.",
            "Rebalance load sharing across adjacent stands.",
        ],
        "spares": {"gear_pair": {"stock": 0, "lead_days": 45}, "oil_filter": {"stock": 4, "lead_days": 7}},
    },
    "CC-03": {
        "name": "Continuous Caster Segment Drive",
        "criticality": 5,
        "manual": "Segment drive alarms should be correlated with motor current, roll gap deviation, cooling water flow, and strand speed variation.",
        "sop": [
            "Confirm casting speed stability and strand cooling flow.",
            "Inspect encoder feedback and motor current imbalance.",
            "Check roll gap actuator and segment lubrication points.",
            "Prepare bypass or controlled slowdown if risk remains high.",
        ],
        "spares": {"drive_motor": {"stock": 1, "lead_days": 28}, "encoder": {"stock": 3, "lead_days": 10}},
    },
    "AG-04": {
        "name": "Acid Gas Scrubber Pump",
        "criticality": 3,
        "manual": "Pump health depends on suction pressure, seal flush condition, impeller fouling, cavitation noise, and motor current.",
        "sop": [
            "Verify suction strainer differential pressure.",
            "Check seal flush and bearing temperature.",
            "Inspect impeller for fouling at next isolation window.",
            "Trend motor current after cleaning.",
        ],
        "spares": {"mechanical_seal": {"stock": 2, "lead_days": 14}, "impeller": {"stock": 1, "lead_days": 21}},
    },
}


FAULT_PATTERNS = [
    {
        "keywords": ["vibration", "bearing", "hot", "temperature", "noise"],
        "diagnosis": "Likely bearing wear or misalignment",
        "root_causes": ["Bearing race damage", "Coupling misalignment", "Lubrication contamination", "Foundation looseness"],
        "actions": ["Inspect bearing and coupling alignment", "Check lube oil quality", "Tighten base bolts", "Schedule vibration spectrum analysis"],
    },
    {
        "keywords": ["oil", "debris", "gear", "gearbox", "mesh"],
        "diagnosis": "Likely gearbox tooth wear or lubrication breakdown",
        "root_causes": ["Gear pitting", "Oil contamination", "Overload cycles", "Filter bypass condition"],
        "actions": ["Collect oil sample", "Inspect magnetic plug", "Replace filter", "Plan borescope inspection"],
    },
    {
        "keywords": ["current", "overload", "motor", "encoder", "drive"],
        "diagnosis": "Likely electrical drive overload or feedback instability",
        "root_causes": ["Encoder drift", "Motor winding heating", "Load imbalance", "Drive tuning mismatch"],
        "actions": ["Check motor current phase balance", "Validate encoder signal", "Review VFD faults", "Reduce load until stable"],
    },
    {
        "keywords": ["cavitation", "suction", "pump", "seal", "flow"],
        "diagnosis": "Likely pump cavitation or seal degradation",
        "root_causes": ["Blocked suction strainer", "Low tank level", "Worn mechanical seal", "Impeller fouling"],
        "actions": ["Clean suction strainer", "Verify tank level", "Inspect seal flush", "Trend pump current and flow"],
    },
]


@dataclass
class SensorRow:
    equipment_id: str
    vibration_mm_s: float
    temperature_c: float
    current_a: float
    oil_particles_ppm: float
    pressure_bar: float
    delay_minutes: float


def load_sensor_rows() -> list[SensorRow]:
    rows: list[SensorRow] = []
    with (DATA_DIR / "sensor_snapshot.csv").open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            rows.append(
                SensorRow(
                    equipment_id=row["equipment_id"],
                    vibration_mm_s=float(row["vibration_mm_s"]),
                    temperature_c=float(row["temperature_c"]),
                    current_a=float(row["current_a"]),
                    oil_particles_ppm=float(row["oil_particles_ppm"]),
                    pressure_bar=float(row["pressure_bar"]),
                    delay_minutes=float(row["delay_minutes"]),
                )
            )
    return rows


def latest_sensor(equipment_id: str) -> SensorRow | None:
    rows = [row for row in load_sensor_rows() if row.equipment_id == equipment_id]
    return rows[-1] if rows else None


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


def match_faults(text: str) -> list[dict[str, Any]]:
    normalized = normalize_text(text)
    matches = []
    for pattern in FAULT_PATTERNS:
        score = sum(1 for word in pattern["keywords"] if word in normalized)
        if score:
            matches.append({**pattern, "match_score": score})
    return sorted(matches, key=lambda x: x["match_score"], reverse=True)


def score_sensor_risk(sensor: SensorRow | None) -> tuple[int, list[str]]:
    if not sensor:
        return 0, ["No live sensor snapshot available"]
    score = 0
    evidence = []
    checks = [
        ("vibration", sensor.vibration_mm_s, 7.0, 11.0, "mm/s"),
        ("temperature", sensor.temperature_c, 80.0, 95.0, "C"),
        ("motor current", sensor.current_a, 420.0, 500.0, "A"),
        ("oil particles", sensor.oil_particles_ppm, 90.0, 140.0, "ppm"),
    ]
    for label, value, warn, critical, unit in checks:
        if value >= critical:
            score += 3
            evidence.append(f"{label} is critical at {value:g} {unit}")
        elif value >= warn:
            score += 2
            evidence.append(f"{label} is elevated at {value:g} {unit}")
    if sensor.pressure_bar < 1.8:
        score += 2
        evidence.append(f"pressure is low at {sensor.pressure_bar:g} bar")
    if sensor.delay_minutes >= 60:
        score += 2
        evidence.append(f"delay impact is high at {sensor.delay_minutes:g} minutes")
    elif sensor.delay_minutes > 0:
        score += 1
        evidence.append(f"delay impact recorded at {sensor.delay_minutes:g} minutes")
    return score, evidence or ["Sensor values are within expected operating range"]


def risk_label(score: int) -> str:
    if score >= 12:
        return "critical"
    if score >= 8:
        return "high"
    if score >= 4:
        return "medium"
    return "low"


def estimate_rul(sensor: SensorRow | None, risk: str) -> dict[str, Any]:
    if not sensor:
        return {"hours": None, "confidence": "low", "method": "No sensor data available"}
    stress = (
        max(sensor.vibration_mm_s / 12, 0)
        + max(sensor.temperature_c / 105, 0)
        + max(sensor.current_a / 540, 0)
        + max(sensor.oil_particles_ppm / 160, 0)
    ) / 4
    risk_multiplier = {"low": 1.6, "medium": 1.0, "high": 0.55, "critical": 0.25}[risk]
    hours = max(8, min(720, round((360 / max(stress, 0.15)) * risk_multiplier)))
    return {
        "hours": hours,
        "confidence": "medium" if risk in {"medium", "high"} else "low",
        "method": "Rule-based health index using vibration, temperature, current, and oil particle stress.",
    }


def choose_spare_strategy(equipment: dict[str, Any], risk: str) -> list[str]:
    strategies = []
    for spare, info in equipment["spares"].items():
        readable = spare.replace("_", " ")
        if info["stock"] <= 0 and risk in {"high", "critical"}:
            strategies.append(f"Raise urgent purchase request for {readable}; lead time is {info['lead_days']} days.")
        elif info["stock"] > 0:
            strategies.append(f"Reserve {readable} from stores; current stock is {info['stock']}.")
    return strategies


def analyze(payload: dict[str, Any]) -> dict[str, Any]:
    equipment_id = payload.get("equipment_id") or "BF-01"
    equipment = EQUIPMENT_KB.get(equipment_id, EQUIPMENT_KB["BF-01"])
    fault_text = " ".join(
        str(payload.get(key, ""))
        for key in ["fault_message", "delay_log", "failure_report", "query", "extra_context"]
    )
    sensor = latest_sensor(equipment_id)
    faults = match_faults(fault_text)
    sensor_score, evidence = score_sensor_risk(sensor)
    text_score = min(4, sum(item["match_score"] for item in faults))
    criticality_score = equipment["criticality"]
    procurement_penalty = max((info["lead_days"] for info in equipment["spares"].values() if info["stock"] <= 0), default=0) // 15
    total_score = sensor_score + text_score + criticality_score + procurement_penalty
    risk = risk_label(total_score)
    top_fault = faults[0] if faults else {
        "diagnosis": "No dominant fault pattern detected",
        "root_causes": ["Insufficient symptom detail", "Transient process deviation", "Unknown equipment condition"],
        "actions": ["Collect additional symptom details", "Review recent alarms", "Compare against normal operating envelope"],
    }
    rul = estimate_rul(sensor, risk)
    immediate = list(dict.fromkeys(top_fault["actions"] + equipment["sop"][:2]))
    long_term = [
        "Add this case to the digital maintenance log with final outcome.",
        "Trend the leading indicators every shift until health returns to normal.",
        "Review repeated alarms against production schedule and process conditions.",
    ]
    spare_strategy = choose_spare_strategy(equipment, risk)
    priority = round(total_score * RISK_WEIGHTS[risk] / 4, 1)
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "equipment_id": equipment_id,
        "equipment_name": equipment["name"],
        "diagnosis": top_fault["diagnosis"],
        "root_causes": top_fault["root_causes"],
        "risk": risk,
        "priority_score": priority,
        "urgency": {
            "low": "Plan in next weekly maintenance window.",
            "medium": "Inspect within 24-48 hours.",
            "high": "Intervene in the current shift and prepare spares.",
            "critical": "Stop or derate equipment safely; escalate immediately.",
        }[risk],
        "rul": rul,
        "early_warning": risk in {"high", "critical"},
        "catastrophic_failure_risk": risk == "critical",
        "evidence": evidence + [f"Equipment criticality score is {criticality_score}/5", equipment["manual"]],
        "immediate_actions": immediate,
        "long_term_plan": long_term,
        "spare_strategy": spare_strategy,
        "traceability": {
            "knowledge_base": f"{equipment_id} manual and SOP",
            "sensor_snapshot": sensor.__dict__ if sensor else None,
            "matched_fault_patterns": [f["diagnosis"] for f in faults],
        },
        "digital_log_entry": f"{equipment_id}: {top_fault['diagnosis']} assessed as {risk.upper()} risk. {payload.get('query', '').strip()}",
    }


def optional_llm_answer(query: str, analysis_result: dict[str, Any]) -> str | None:
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not query:
        return None
    body = json.dumps(
        {
            "model": os.environ.get("OPENAI_MODEL", "gpt-4.1-mini"),
            "messages": [
                {
                    "role": "system",
                    "content": "You are a maintenance decision-support assistant for steel plant equipment. Be concise, explainable, and safety-focused.",
                },
                {
                    "role": "user",
                    "content": f"Engineer query: {query}\n\nStructured analysis JSON:\n{json.dumps(analysis_result, indent=2)}",
                },
            ],
            "temperature": 0.2,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=25) as response:
            data = json.loads(response.read().decode("utf-8"))
            return data["choices"][0]["message"]["content"]
    except Exception as exc:
        return f"LLM call failed, fallback analysis shown. Reason: {exc}"


def make_report(result: dict[str, Any]) -> str:
    REPORTS_DIR.mkdir(exist_ok=True)
    slug = f"{result['equipment_id']}-{int(time.time())}.md"
    path = REPORTS_DIR / slug
    lines = [
        f"# Maintenance Wizard Report - {result['equipment_id']}",
        "",
        f"Generated: {result['timestamp']}",
        f"Equipment: {result['equipment_name']}",
        f"Risk: {result['risk'].upper()}",
        f"Priority score: {result['priority_score']}",
        f"Diagnosis: {result['diagnosis']}",
        f"Urgency: {result['urgency']}",
        "",
        "## Probable Root Causes",
        *[f"- {item}" for item in result["root_causes"]],
        "",
        "## Evidence",
        *[f"- {item}" for item in result["evidence"]],
        "",
        "## Immediate Actions",
        *[f"- {item}" for item in result["immediate_actions"]],
        "",
        "## Long-Term Plan",
        *[f"- {item}" for item in result["long_term_plan"]],
        "",
        "## Spare Strategy",
        *[f"- {item}" for item in result["spare_strategy"]],
        "",
        "## Digital Log Entry",
        result["digital_log_entry"],
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return f"/reports/{slug}"


class AppHandler(SimpleHTTPRequestHandler):
    def translate_path(self, path: str) -> str:
        parsed = urlparse(path).path
        if parsed.startswith("/reports/"):
            return str(ROOT / parsed.lstrip("/"))
        if parsed in {"/", "/index.html"}:
            return str(STATIC_DIR / "index.html")
        return str(STATIC_DIR / parsed.lstrip("/"))

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/equipment":
            self.send_json(
                [
                    {"id": key, "name": value["name"], "criticality": value["criticality"]}
                    for key, value in EQUIPMENT_KB.items()
                ]
            )
        elif parsed.path == "/api/sensors":
            self.send_json([row.__dict__ for row in load_sensor_rows()])
        else:
            super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/analyze":
                payload = self.read_json()
                result = analyze(payload)
                result["llm_answer"] = optional_llm_answer(payload.get("query", ""), result)
                result["report_url"] = make_report(result)
                self.send_json(result)
            elif parsed.path == "/api/feedback":
                payload = self.read_json()
                payload["timestamp"] = datetime.now(timezone.utc).isoformat()
                with FEEDBACK_FILE.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(payload) + "\n")
                self.send_json({"ok": True, "message": "Feedback stored for future recommendation tuning."})
            else:
                self.send_json({"error": "Not found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)


def main() -> None:
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer(("127.0.0.1", port), AppHandler)
    print(f"Maintenance Wizard running at http://127.0.0.1:{port}")
    print("Press Ctrl+C to stop.")
    server.serve_forever()


if __name__ == "__main__":
    main()
