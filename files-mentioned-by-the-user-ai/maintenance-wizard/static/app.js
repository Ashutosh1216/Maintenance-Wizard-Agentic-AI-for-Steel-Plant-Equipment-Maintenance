const equipmentSelect = document.querySelector("#equipment");
const form = document.querySelector("#analysisForm");
const demoBtn = document.querySelector("#demoBtn");
const feedbackBtn = document.querySelector("#feedbackBtn");
let lastAnalysis = null;

const demo = {
  equipment_id: "BF-01",
  fault_message: "High vibration, hot bearing alarm, abnormal noise from drive end.",
  delay_log: "90 minute production delay after repeated blower trip during charging cycle.",
  query: "What should the maintenance engineer do first, and do we need to reserve spares?",
};

function itemList(target, items, ordered = false) {
  target.innerHTML = "";
  for (const item of items || []) {
    const li = document.createElement("li");
    li.textContent = item;
    target.appendChild(li);
  }
}

function setRiskClass(risk) {
  const warning = document.querySelector("#warning");
  warning.className = "alert";
  if (risk === "critical") warning.classList.add("critical");
  if (risk === "high" || risk === "medium") warning.classList.add("warning");
}

async function loadEquipment() {
  const response = await fetch("/api/equipment");
  const equipment = await response.json();
  equipmentSelect.innerHTML = equipment
    .map((item) => `<option value="${item.id}">${item.id} - ${item.name}</option>`)
    .join("");
}

function payloadFromForm() {
  return {
    equipment_id: equipmentSelect.value,
    fault_message: document.querySelector("#fault").value,
    delay_log: document.querySelector("#delay").value,
    query: document.querySelector("#query").value,
  };
}

async function runAnalysis(payload) {
  const response = await fetch("/api/analyze", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error("Analysis failed");
  lastAnalysis = await response.json();
  renderAnalysis(lastAnalysis);
}

function renderAnalysis(result) {
  document.querySelector("#updatedAt").textContent = new Date(result.timestamp).toLocaleString();
  document.querySelector("#risk").textContent = result.risk;
  document.querySelector("#priority").textContent = result.priority_score;
  document.querySelector("#rul").textContent = result.rul.hours ? `${result.rul.hours} h` : "n/a";
  document.querySelector("#diagnosis").textContent = result.diagnosis;
  document.querySelector("#urgency").textContent = result.urgency;
  const warning = document.querySelector("#warning");
  warning.textContent = result.early_warning
    ? result.catastrophic_failure_risk
      ? "Critical early warning: controlled shutdown or derating is recommended."
      : "Early warning active: intervene before the next production cycle."
    : "No active catastrophic failure warning.";
  setRiskClass(result.risk);
  itemList(document.querySelector("#evidence"), result.evidence);
  itemList(document.querySelector("#actions"), result.immediate_actions, true);
  itemList(document.querySelector("#spares"), result.spare_strategy.length ? result.spare_strategy : ["No spare escalation required."]);
  const report = document.querySelector("#reportLink");
  report.href = result.report_url;
  report.textContent = "Open structured maintenance report";
}

demoBtn.addEventListener("click", async () => {
  equipmentSelect.value = demo.equipment_id;
  document.querySelector("#fault").value = demo.fault_message;
  document.querySelector("#delay").value = demo.delay_log;
  document.querySelector("#query").value = demo.query;
  await runAnalysis(demo);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  await runAnalysis(payloadFromForm());
});

feedbackBtn.addEventListener("click", async () => {
  const outcome = document.querySelector("#feedback").value.trim();
  const status = document.querySelector("#feedbackStatus");
  if (!outcome || !lastAnalysis) {
    status.textContent = "Run an analysis and enter the actual outcome first.";
    return;
  }
  const response = await fetch("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ outcome, analysis: lastAnalysis }),
  });
  const result = await response.json();
  status.textContent = result.message || "Feedback saved.";
});

loadEquipment();
