// Multi-MCP Client Dashboard - State and Orchestration
let gServers = [];
let gSelectedTool = null;
let gActiveMode = "MOCK";
let gScenarios = [];

document.addEventListener("DOMContentLoaded", async () => {
  setupModeSwitch();
  await loadServers();
  await loadScenarios();
});

function setupModeSwitch() {
  const btnMock = document.getElementById("btn-mode-mock");
  const btnSdk = document.getElementById("btn-mode-sdk");

  btnMock.addEventListener("click", () => {
    gActiveMode = "MOCK";
    btnMock.classList.add("active");
    btnSdk.classList.remove("active");
  });

  btnSdk.addEventListener("click", () => {
    gActiveMode = "SDK";
    btnSdk.classList.add("active");
    btnMock.classList.remove("active");
  });
}

async function loadServers() {
  try {
    const res = await fetch("/api/servers");
    gServers = await res.json();
    renderSidebar();
    
    // Select first tool by default
    if (gServers.length > 0 && gServers[0].tools.length > 0) {
      selectTool(gServers[0].tools[0]);
    }
  } catch (err) {
    console.error("Failed to load servers", err);
  }
}

async function loadScenarios() {
  try {
    const res = await fetch("/api/scenarios");
    gScenarios = await res.json();
    renderScenarios();
  } catch (err) {
    console.error("Failed to load scenarios", err);
  }
}

function renderSidebar() {
  const container = document.getElementById("server-list-container");
  container.innerHTML = "";

  gServers.forEach(server => {
    const card = document.createElement("div");
    card.className = "server-card";

    const header = document.createElement("div");
    header.className = "server-header";
    header.innerHTML = `
      <span class="server-name">${server.display_name}</span>
      <span class="tool-count">${server.tools.length} Tools</span>
    `;

    const toolList = document.createElement("div");
    toolList.className = "tool-list";

    server.tools.forEach(tool => {
      const toolEl = document.createElement("div");
      toolEl.className = "tool-item";
      toolEl.id = `tool-item-${tool.name}`;
      toolEl.innerHTML = `<span>⚡</span> <span>${tool.name}</span>`;
      toolEl.addEventListener("click", () => selectTool(tool));
      toolList.appendChild(toolEl);
    });

    card.appendChild(header);
    card.appendChild(toolList);
    container.appendChild(card);
  });
}

function selectTool(tool) {
  gSelectedTool = tool;

  // Highlight active
  document.querySelectorAll(".tool-item").forEach(el => el.classList.remove("active"));
  const activeEl = document.getElementById(`tool-item-${tool.name}`);
  if (activeEl) activeEl.classList.add("active");

  // Show Tool View
  document.getElementById("tool-view").style.display = "flex";
  document.getElementById("scenarios-view").style.display = "none";

  document.getElementById("tool-title").textContent = tool.name;
  document.getElementById("server-tag").textContent = tool.server_id.toUpperCase() + " MCP";
  document.getElementById("tool-desc").textContent = tool.description;

  renderForm(tool);
}

function renderForm(tool) {
  const form = document.getElementById("params-form");
  form.innerHTML = "";

  if (tool.parameters.length === 0) {
    form.innerHTML = `<div class="field-desc">This tool does not require any input parameters.</div>`;
    return;
  }

  tool.parameters.forEach(param => {
    const field = document.createElement("div");
    field.className = "form-field";

    const label = document.createElement("label");
    label.className = "field-label";
    label.innerHTML = `
      <span>${param.name} ${param.required ? '<span class="field-required">*</span>' : ''}</span>
      <span class="field-type">${param.type_name}</span>
    `;

    const input = document.createElement("input");
    input.type = param.type_name === "integer" || param.type_name === "number" ? "number" : "text";
    input.id = `param-${param.name}`;
    input.name = param.name;
    input.placeholder = param.description || param.name;

    // Use preset value if available
    const presetVal = tool.preset_arguments[param.name];
    if (presetVal !== undefined) {
      input.value = typeof presetVal === "object" ? JSON.stringify(presetVal) : presetVal;
    }

    const desc = document.createElement("div");
    desc.className = "field-desc";
    desc.textContent = param.description;

    field.appendChild(label);
    field.appendChild(input);
    if (param.description) field.appendChild(desc);
    form.appendChild(field);
  });
}

async function invokeCurrentTool() {
  if (!gSelectedTool) return;

  const btn = document.getElementById("btn-run-tool");
  btn.disabled = true;
  btn.textContent = "Executing...";

  const args = {};
  gSelectedTool.parameters.forEach(param => {
    const input = document.getElementById(`param-${param.name}`);
    if (input && input.value !== "") {
      const trimmed = input.value.trim();
      if (param.type_name === "integer") {
        args[param.name] = parseInt(trimmed, 10);
      } else if (param.type_name === "number") {
        args[param.name] = parseFloat(trimmed);
      } else if (trimmed.startsWith("[") || trimmed.startsWith("{")) {
        try {
          args[param.name] = JSON.parse(trimmed);
        } catch {
          args[param.name] = input.value;
        }
      } else {
        args[param.name] = input.value;
      }
    }
  });

  const payload = {
    server_id: gSelectedTool.server_id,
    tool_name: gSelectedTool.name,
    arguments: args,
    mode: gActiveMode,
  };

  updateStatus("RUNNING", 0);

  try {
    const res = await fetch("/api/invoke", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const rawText = await res.text();
      let errObj;
      try {
        errObj = JSON.parse(rawText);
      } catch {
        errObj = { error: rawText || `HTTP ${res.status}: ${res.statusText}` };
      }
      renderOutput({
        status: "error",
        error: errObj.message || errObj.error || errObj.detail || `HTTP ${res.status}`,
        payload: errObj,
        duration_ms: 0,
      });
      return;
    }
    const data = await res.json();
    renderOutput(data);
  } catch (err) {
    renderOutput({ status: "error", error: err.toString(), duration_ms: 0 });
  } finally {
    btn.disabled = false;
    btn.innerHTML = `<span>▶</span> Run Tool`;
  }
}

function resetFormToPreset() {
  if (gSelectedTool) {
    renderForm(gSelectedTool);
  }
}

function renderOutput(result) {
  const statusBadge = document.getElementById("status-badge");
  const durationEl = document.getElementById("meta-duration");
  const timestampEl = document.getElementById("meta-timestamp");
  const responseBody = document.getElementById("response-body");

  statusBadge.className = `status-badge ${result.status}`;
  statusBadge.textContent = result.status.toUpperCase();

  durationEl.textContent = `${result.duration_ms} ms`;
  timestampEl.textContent = result.timestamp ? result.timestamp.split("T")[1].substring(0, 8) : "--:--:--";

  const displayData = result.payload !== undefined ? result.payload : result;
  responseBody.textContent = JSON.stringify(displayData, null, 2);
}

function updateStatus(statusText, durationMs) {
  const statusBadge = document.getElementById("status-badge");
  statusBadge.className = `status-badge idle`;
  statusBadge.textContent = statusText;
  document.getElementById("meta-duration").textContent = `${durationMs} ms`;
}

function copyResponse() {
  const text = document.getElementById("response-body").textContent;
  navigator.clipboard.writeText(text);
  const btn = document.getElementById("btn-copy-response");
  btn.textContent = "Copied!";
  setTimeout(() => (btn.textContent = "Copy JSON"), 2000);
}

// Scenarios Tab View
function showScenarios() {
  document.getElementById("tool-view").style.display = "none";
  document.getElementById("scenarios-view").style.display = "flex";
  document.querySelectorAll(".tool-item").forEach(el => el.classList.remove("active"));
}

function renderScenarios() {
  const container = document.getElementById("scenarios-list");
  container.innerHTML = "";

  gScenarios.forEach(sc => {
    const card = document.createElement("div");
    card.className = "scenario-card";
    card.innerHTML = `
      <div class="scenario-title">${sc.title}</div>
      <div class="scenario-desc">${sc.description}</div>
      <div class="scenario-steps-count">${sc.steps.length} Automated Pipeline Steps</div>
      <button class="btn-primary" style="margin-top:0.5rem;" onclick="runScenario('${sc.scenario_id}')">
        <span>⚡</span> Run Full Scenario
      </button>
    `;
    container.appendChild(card);
  });
}

async function runScenario(scenarioId) {
  updateStatus("RUNNING SCENARIO", 0);
  try {
    const res = await fetch(`/api/scenarios/${scenarioId}/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: gActiveMode }),
    });
    if (!res.ok) {
      const rawText = await res.text();
      let errObj;
      try {
        errObj = JSON.parse(rawText);
      } catch {
        errObj = { error: rawText || `HTTP ${res.status}: ${res.statusText}` };
      }
      renderOutput({
        status: "error",
        error: errObj.message || errObj.error || errObj.detail || `HTTP ${res.status}`,
        payload: errObj,
        duration_ms: 0,
      });
      return;
    }
    const data = await res.json();
    renderOutput(data);
  } catch (err) {
    renderOutput({ status: "error", error: err.toString() });
  }
}
