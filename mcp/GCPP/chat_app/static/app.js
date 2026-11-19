// Enterprise MCP Chat Agent Client Controller
let gActiveMode = "MOCK";
let gSampleQuestions = [];
let gCurrentCategory = "ALL";

document.addEventListener("DOMContentLoaded", async () => {
  setupModeSwitch();
  setupChatInput();
  setupCategoryFilters();
  await loadSampleQuestions();
});

// Setup Mode Switcher
function setupModeSwitch() {
  const btnMock = document.getElementById("btn-mode-mock");
  const btnSdk = document.getElementById("btn-mode-sdk");
  const badge = document.getElementById("input-mode-badge");

  btnMock.addEventListener("click", () => {
    gActiveMode = "MOCK";
    btnMock.classList.add("active");
    btnSdk.classList.remove("active");
    badge.textContent = "MODE: MOCK";
    badge.className = "active-mode-badge";
  });

  btnSdk.addEventListener("click", () => {
    gActiveMode = "SDK";
    btnSdk.classList.add("active");
    btnMock.classList.remove("active");
    badge.textContent = "MODE: SDK";
    badge.className = "active-mode-badge sdk-mode";
  });
}

// Setup Chat Input
function setupChatInput() {
  const input = document.getElementById("chat-input");
  const btnSend = document.getElementById("btn-send");
  const btnClear = document.getElementById("btn-clear-chat");

  // Auto-resize input
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 140) + "px";
  });

  // Enter to send
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  btnSend.addEventListener("click", sendMessage);

  btnClear.addEventListener("click", () => {
    const container = document.getElementById("chat-messages");
    container.innerHTML = `
      <div class="message-row assistant">
        <div class="avatar">🤖</div>
        <div class="message-content">
          <div class="message-header">
            <span class="sender-name">MCP Chat Agent</span>
            <span class="message-time">Just now</span>
          </div>
          <div class="message-bubble">
            <p>Chat cleared. Ready for your next MCP question!</p>
          </div>
        </div>
      </div>
    `;
  });
}

// Setup Category Filters
function setupCategoryFilters() {
  const pills = document.querySelectorAll(".cat-pill");
  pills.forEach((p) => {
    p.addEventListener("click", () => {
      pills.forEach((el) => el.classList.remove("active"));
      p.classList.add("active");
      gCurrentCategory = p.dataset.category;
      renderSampleQuestions();
    });
  });
}

// Load Sample Questions from API
async function loadSampleQuestions() {
  const container = document.getElementById("samples-container");
  try {
    const res = await fetch("/api/sample-questions");
    gSampleQuestions = await res.json();
    document.getElementById("sample-count").textContent = gSampleQuestions.length;
    renderSampleQuestions();
  } catch (err) {
    container.innerHTML = `<div class="sample-desc" style="color:var(--accent-red);">Failed to load sample questions: ${err}</div>`;
  }
}

// Render Sample Questions in Sidebar
function renderSampleQuestions() {
  const container = document.getElementById("samples-container");
  container.innerHTML = "";

  const filtered = gSampleQuestions.filter((q) => {
    if (gCurrentCategory === "ALL") return true;
    return q.category.toUpperCase() === gCurrentCategory.toUpperCase();
  });

  if (filtered.length === 0) {
    container.innerHTML = `<div class="sidebar-desc" style="padding:12px;">No questions in this category.</div>`;
    return;
  }

  filtered.forEach((q) => {
    const card = document.createElement("div");
    card.className = "sample-card";

    const tagClass = `tag-${q.category.toLowerCase()}`;
    card.innerHTML = `
      <div class="sample-card-header">
        <span class="sample-category-tag ${tagClass}">${q.category}</span>
        <span class="sample-tool-target">${q.target_tool}</span>
      </div>
      <div class="sample-question-text">"${q.question}"</div>
      <div class="sample-desc">${q.description}</div>
    `;

    card.addEventListener("click", () => {
      const input = document.getElementById("chat-input");
      input.value = q.question;
      input.focus();
      sendMessage();
    });

    container.appendChild(card);
  });
}

// Send Message Flow
async function sendMessage() {
  const input = document.getElementById("chat-input");
  const text = input.value.trim();
  if (!text) return;

  const btnSend = document.getElementById("btn-send");
  const typingIndicator = document.getElementById("typing-indicator");

  // Append user message
  appendUserMessage(text);
  input.value = "";
  input.style.height = "auto";
  btnSend.disabled = true;
  typingIndicator.style.display = "inline";

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message: text,
        mode: gActiveMode,
      }),
    });

    const data = await res.json();
    appendAssistantMessage(data);
  } catch (err) {
    appendAssistantMessage({
      reply: `⚠️ **Network / Server Error:** ${err.message}`,
      tool_used: "none",
      server_id: "none",
      arguments: {},
      reasoning: "Failed to communicate with chat server",
      execution_time_ms: 0,
      status: "error",
      mode: gActiveMode,
      result_payload: { error: err.toString() },
    });
  } finally {
    btnSend.disabled = false;
    typingIndicator.style.display = "none";
  }
}

// Append User Message Bubble
function appendUserMessage(text) {
  const container = document.getElementById("chat-messages");
  const row = document.createElement("div");
  row.className = "message-row user";

  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  row.innerHTML = `
    <div class="avatar">👤</div>
    <div class="message-content">
      <div class="message-header">
        <span class="sender-name">You</span>
        <span class="message-time">${now}</span>
      </div>
      <div class="message-bubble">${escapeHtml(text)}</div>
    </div>
  `;

  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
}

// Append Assistant Message Bubble
function appendAssistantMessage(data) {
  const container = document.getElementById("chat-messages");
  const row = document.createElement("div");
  row.className = "message-row assistant";

  const now = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const parsedMarkdown = typeof marked !== "undefined" ? marked.parse(data.reply || "") : `<p>${data.reply}</p>`;

  const isSuccess = data.status === "success";
  const statusColor = isSuccess ? "var(--accent-green)" : "var(--accent-red)";
  const statusIcon = isSuccess ? "✅" : "⚠️";

  row.innerHTML = `
    <div class="avatar">🤖</div>
    <div class="message-content">
      <div class="message-header">
        <span class="sender-name">MCP Chat Agent</span>
        <span class="message-time">${now}</span>
      </div>
      <div class="message-bubble">${parsedMarkdown}</div>

      <!-- Execution Metadata Card -->
      <div class="execution-card">
        <div class="execution-header">
          <div class="tool-badge" style="color:${statusColor}">
            <span>${statusIcon}</span>
            <span>${data.tool_used}</span>
          </div>
          <div class="meta-stats">
            <span class="stat-pill">Server: ${data.server_id.toUpperCase()}</span>
            <span class="stat-pill">Mode: ${data.mode}</span>
            <span class="stat-pill">${data.execution_time_ms} ms</span>
          </div>
        </div>
        <div class="reasoning-bar">
          <span>🧠 Router:</span>
          <span>${data.reasoning || "Automatic intent match"}</span>
        </div>
        
        <!-- Collapsible JSON Inspector -->
        <div class="json-inspector">
          <button class="json-toggle" onclick="toggleJson(this)">
            <span>Inspect MCP Data & Arguments</span>
            <span>▼</span>
          </button>
          <div class="json-content">
            <div style="margin-bottom:6px; font-weight:600; color:var(--text-secondary); font-size:10px;">ARGUMENTS:</div>
            <pre>${escapeHtml(JSON.stringify(data.arguments, null, 2))}</pre>
            <div style="margin:8px 0 6px 0; font-weight:600; color:var(--text-secondary); font-size:10px;">RAW MCP PAYLOAD:</div>
            <pre>${escapeHtml(JSON.stringify(data.result_payload, null, 2))}</pre>
          </div>
        </div>
      </div>
    </div>
  `;

  container.appendChild(row);
  container.scrollTop = container.scrollHeight;
}

// Collapsible JSON Toggle
function toggleJson(btn) {
  const content = btn.nextElementSibling;
  content.classList.toggle("open");
  const arrow = btn.querySelector("span:last-child");
  arrow.textContent = content.classList.contains("open") ? "▲" : "▼";
}

function escapeHtml(str) {
  if (!str) return "";
  return str
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
