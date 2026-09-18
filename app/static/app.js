const loginView = document.querySelector("#login-view");
const dashboardView = document.querySelector("#dashboard-view");
const globalMessage = document.querySelector("#global-message");
let latestKey = null;

async function api(path, options = {}) {
  const response = await fetch(path, { credentials: "same-origin", ...options });
  if (!response.ok) {
    let message = `Error ${response.status}`;
    let detail = null;
    try {
      const payload = await response.json();
      detail = payload.detail || payload.error;
      message = detail?.message || (typeof detail === "string" ? detail : message);
    } catch (_) {
      // Preserve the HTTP fallback when the body is not JSON.
    }
    const error = new Error(message);
    error.status = response.status;
    error.detail = detail;
    throw error;
  }
  return response.status === 204 ? null : response.json();
}

function formatDate(value) {
  if (!value) return "Sin uso todavía";
  return new Intl.DateTimeFormat("es-CO", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "America/Bogota",
  }).format(new Date(value));
}

function showMessage(text, type = "info") {
  globalMessage.textContent = text;
  globalMessage.className = type === "error" ? "message error" : "message";
  globalMessage.hidden = false;
}

function setText(selector, value) {
  document.querySelector(selector).textContent = value ?? "—";
}

function statusNode(label, kind) {
  const span = document.createElement("span");
  span.className = `status ${kind}`;
  span.textContent = label;
  return span;
}

function renderDashboard(data) {
  const participant = data.participant;
  setText("#student-meta", `${participant.display_name} · Grupo ${participant.section || "—"} · ${participant.cohort || "MLOps"}`);

  const hasKey = Boolean(data.api_key);
  document.querySelector("#key-empty").hidden = hasKey;
  document.querySelector("#key-active").hidden = !hasKey;
  if (hasKey) {
    setText("#key-prefix", `${data.api_key.key_prefix}.••••••••`);
    setText("#key-last-used", formatDate(data.api_key.last_used_at));
  }

  const hasCycle = Boolean(data.cycle);
  document.querySelector("#cycle-empty").hidden = hasCycle;
  document.querySelector("#cycle-active").hidden = !hasCycle;
  if (hasCycle) {
    setText("#cycle-id", data.cycle.public_id);
    setText("#cycle-cutoff", formatDate(data.cycle.data_cutoff));
    setText("#cycle-count", `${data.cycle.expected_predictions} objetivos`);
    setText("#cycle-closes", formatDate(data.cycle.closes_at));
  }

  const delivery = data.submissions[0];
  document.querySelector("#delivery-empty").hidden = Boolean(delivery);
  document.querySelector("#delivery-active").hidden = !delivery;
  if (delivery) {
    const status = document.querySelector("#delivery-status");
    status.replaceChildren(statusNode(delivery.status === "accepted" ? "Aceptada" : delivery.status, delivery.status === "accepted" ? "success" : "pending"));
    setText("#delivery-model", delivery.model_version);
    setText("#delivery-count", `${delivery.prediction_count} predicciones`);
    setText("#delivery-time", formatDate(delivery.received_at));
  }
}

function renderBoard(board) {
  const body = document.querySelector("#leaderboard-body");
  body.replaceChildren();
  let activated = 0;
  let delivered = 0;

  board.data.forEach((row) => {
    if (row.api_key_active) activated += 1;
    if (row.submission_status === "accepted") delivered += 1;
    const tr = document.createElement("tr");
    const values = [row.display_name, row.section_code || "—"];
    values.forEach((value) => {
      const td = document.createElement("td");
      td.textContent = value;
      tr.appendChild(td);
    });

    const keyCell = document.createElement("td");
    keyCell.appendChild(statusNode(row.api_key_active ? "Activa" : "Pendiente", row.api_key_active ? "success" : "inactive"));
    tr.appendChild(keyCell);

    const deliveryCell = document.createElement("td");
    deliveryCell.appendChild(statusNode(row.submission_status === "accepted" ? "Aceptada" : "Pendiente", row.submission_status === "accepted" ? "success" : "pending"));
    tr.appendChild(deliveryCell);

    const countCell = document.createElement("td");
    countCell.textContent = row.submission_status ? `${row.prediction_count}/${board.cycle?.expected_predictions ?? "—"}` : "—";
    tr.appendChild(countCell);

    const timeCell = document.createElement("td");
    timeCell.textContent = formatDate(row.last_submission_at);
    tr.appendChild(timeCell);
    body.appendChild(tr);
  });
  setText("#board-summary", `${activated}/${board.count} API activadas · ${delivered}/${board.count} entregas aceptadas`);
}

async function loadDashboard() {
  const [dashboard, board] = await Promise.all([
    api("/v1/portal/dashboard"),
    api("/v1/portal/leaderboard"),
  ]);
  renderDashboard(dashboard);
  renderBoard(board);
  loginView.hidden = true;
  dashboardView.hidden = false;
}

document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  const button = form.querySelector("button[type=submit]");
  const errorNode = document.querySelector("#login-error");
  errorNode.hidden = true;
  button.disabled = true;
  try {
    const values = Object.fromEntries(new FormData(form).entries());
    await api("/v1/portal/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(values),
    });
    form.reset();
    await loadDashboard();
  } catch (error) {
    errorNode.textContent = error.message;
    errorNode.hidden = false;
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#generate-key-button").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await api("/v1/portal/api-key", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    latestKey = result.api_key;
    setText("#new-api-key", latestKey);
    document.querySelector("#secret-panel").hidden = false;
    await loadDashboard();
    document.querySelector("#secret-panel").scrollIntoView({ behavior: "smooth", block: "center" });
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#copy-key-button").addEventListener("click", async (event) => {
  if (!latestKey) return;
  await navigator.clipboard.writeText(latestKey);
  event.currentTarget.textContent = "Copiada";
});

document.querySelector("#download-env-button").addEventListener("click", () => {
  if (!latestKey) return;
  const blob = new Blob([`PULSO_API_KEY=${latestKey}\n`], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = ".env";
  link.click();
  URL.revokeObjectURL(url);
});

document.querySelector("#refresh-button").addEventListener("click", async (event) => {
  const button = event.currentTarget;
  button.disabled = true;
  try {
    await loadDashboard();
    showMessage("Datos actualizados.");
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  await api("/v1/portal/logout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: "{}",
  });
  latestKey = null;
  dashboardView.hidden = true;
  loginView.hidden = false;
  document.querySelector("#secret-panel").hidden = true;
});

loadDashboard().catch((error) => {
  if (error.status !== 401) {
    const errorNode = document.querySelector("#login-error");
    errorNode.textContent = "El portal no está disponible temporalmente. Intenta de nuevo.";
    errorNode.hidden = false;
  }
});
