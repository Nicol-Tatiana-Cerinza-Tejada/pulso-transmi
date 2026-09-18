const loginView = document.querySelector("#login-view");
const dashboardView = document.querySelector("#dashboard-view");
const globalMessage = document.querySelector("#global-message");
const SVG_NS = "http://www.w3.org/2000/svg";
const DEMO_MODE = new URLSearchParams(window.location.search).get("demo") === "1";

const RUNNER_COLORS = [
  "#1b5e3b", "#2d7987", "#7161a7", "#b86743", "#c8a951", "#d94f70",
  "#3478bf", "#8c5f3d", "#4e9471", "#db7a32", "#6b7c2a", "#a94f92",
  "#176b40", "#4779a8", "#7e6bb5", "#d15e42", "#92751f", "#c24965",
  "#2f8c89", "#805744", "#5c8869", "#b96385", "#576aa6", "#a36b2d",
  "#22705a", "#3c82a2", "#8a559d", "#c66b3d", "#7c822d", "#b44b59",
  "#397e68", "#536f90", "#9a6e36", "#6d5594", "#bf5650", "#63824a",
];

let latestKey = null;
let currentParticipantId = null;

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

function formatShortDate(value) {
  return new Intl.DateTimeFormat("es-CO", {
    day: "numeric",
    month: "short",
    timeZone: "America/Bogota",
  }).format(new Date(value));
}

function showMessage(text, type = "info") {
  globalMessage.textContent = text;
  globalMessage.className = type === "error" ? "message error" : "message";
  globalMessage.hidden = false;
}

function setText(selector, value) {
  const node = document.querySelector(selector);
  if (node) node.textContent = value ?? "—";
}

function statusNode(label, kind) {
  const span = document.createElement("span");
  span.className = `status ${kind}`;
  span.textContent = label;
  return span;
}

function avatarPosition(index = 0) {
  const safeIndex = Math.abs(Number(index) || 0) % 36;
  return {
    x: `${(safeIndex % 6) * 20}%`,
    y: `${Math.floor(safeIndex / 6) * 20}%`,
  };
}

function applyAvatar(node, index, label = "") {
  const position = avatarPosition(index);
  node.style.setProperty("--avatar-x", position.x);
  node.style.setProperty("--avatar-y", position.y);
  if (label) {
    node.setAttribute("role", "img");
    node.setAttribute("aria-label", `Avatar ilustrado de ${label}`);
    node.removeAttribute("aria-hidden");
  }
}

function avatarNode(index, label, extraClass = "") {
  const avatar = document.createElement("span");
  avatar.className = `avatar ${extraClass}`.trim();
  applyAvatar(avatar, index, label);
  return avatar;
}

function renderDashboard(data) {
  const participant = data.participant;
  currentParticipantId = participant.participant_id || participant.public_id || participant.id;
  const preferredName = participant.preferred_name || participant.display_name;
  setText("#student-meta", `${preferredName} · ${participant.section || "Grupo único"}`);

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

  const delivery = data.submissions?.[0];
  document.querySelector("#delivery-empty").hidden = Boolean(delivery);
  document.querySelector("#delivery-active").hidden = !delivery;
  if (delivery) {
    const accepted = delivery.status === "accepted";
    const status = document.querySelector("#delivery-status");
    status.replaceChildren(statusNode(accepted ? "Aceptada" : delivery.status, accepted ? "success" : "pending"));
    setText("#delivery-model", delivery.model_version);
    setText("#delivery-count", `${delivery.prediction_count} predicciones`);
    setText("#delivery-time", formatDate(delivery.received_at));
  }
}

function highlightParticipant(participantId) {
  document.querySelectorAll("[data-runner-id]").forEach((node) => {
    node.classList.toggle("is-highlighted", node.dataset.runnerId === participantId);
  });
}

function renderBoard(board) {
  const body = document.querySelector("#leaderboard-body");
  body.replaceChildren();
  let activated = 0;
  let delivered = 0;

  board.data.forEach((row, index) => {
    if (row.api_key_active) activated += 1;
    if (row.submission_status === "accepted") delivered += 1;
    const avatarIndex = row.avatar_index ?? index;
    const tr = document.createElement("tr");

    const nameCell = document.createElement("td");
    const identity = document.createElement("div");
    identity.className = "table-identity";
    identity.append(avatarNode(avatarIndex, row.display_name, "avatar-small"));
    const name = document.createElement("span");
    name.textContent = row.display_name;
    identity.append(name);
    nameCell.append(identity);
    tr.append(nameCell);

    const sectionCell = document.createElement("td");
    sectionCell.textContent = row.section_code || "—";
    tr.append(sectionCell);

    const keyCell = document.createElement("td");
    keyCell.append(statusNode(row.api_key_active ? "Activa" : "Pendiente", row.api_key_active ? "success" : "inactive"));
    tr.append(keyCell);

    const deliveryCell = document.createElement("td");
    deliveryCell.append(statusNode(row.submission_status === "accepted" ? "Aceptada" : "Pendiente", row.submission_status === "accepted" ? "success" : "pending"));
    tr.append(deliveryCell);

    const countCell = document.createElement("td");
    countCell.textContent = row.submission_status ? `${row.prediction_count}/${board.cycle?.expected_predictions ?? "—"}` : "—";
    tr.append(countCell);

    const timeCell = document.createElement("td");
    timeCell.textContent = formatDate(row.last_submission_at);
    tr.append(timeCell);
    body.append(tr);

    if ((row.participant_id || row.public_id) === currentParticipantId) {
      applyAvatar(document.querySelector("#current-avatar"), avatarIndex, row.display_name);
    }
  });

  setText("#board-summary", `${activated}/${board.count} API activadas · ${delivered}/${board.count} entregas aceptadas`);
  setText("#cohort-count", board.count);
  setText("#connected-count", activated);
  setText("#delivered-count", delivered);
}

function svgElement(name, attributes = {}) {
  const node = document.createElementNS(SVG_NS, name);
  Object.entries(attributes).forEach(([key, value]) => node.setAttribute(key, value));
  return node;
}

function renderRace(board) {
  const svg = document.querySelector("#race-chart");
  const list = document.querySelector("#race-list");
  const empty = document.querySelector("#chart-empty");
  const notice = document.querySelector("#preview-notice");
  const timeline = board.timeline || [];
  const rowsById = new Map(board.data.map((row, index) => [
    row.participant_id || row.public_id,
    { ...row, avatar_index: row.avatar_index ?? index },
  ]));

  svg.querySelectorAll(":scope > :not(title):not(desc)").forEach((node) => node.remove());
  list.replaceChildren();
  notice.hidden = !DEMO_MODE;
  setText("#race-mode-label", DEMO_MODE ? "Warmup · vista previa" : "Warmup");
  setText("#race-period", DEMO_MODE ? "Simulado" : (board.cycle?.cycle_id || board.cycle?.public_id || "Warmup"));

  if (!timeline.length) {
    empty.hidden = false;
    board.data.forEach((row, index) => list.append(renderRaceListItem({
      id: row.participant_id || row.public_id,
      display_name: row.display_name,
      avatar_index: row.avatar_index ?? index,
      rank: null,
      accuracy: null,
      coverage: row.submission_status === "accepted" ? 1 : 0,
    })));
    return;
  }

  empty.hidden = true;
  if (DEMO_MODE) {
    setText("#home-title", "Así se verá cuando empiece la carrera.");
    setText("#phase-copy", "Los datos de esta vista son simulados para probar la lectura de 32 trayectorias. El portal real continúa en warmup.");
  }

  const width = 960;
  const height = 520;
  const plot = { left: 62, right: 32, top: 24, bottom: 52 };
  const timestamps = timeline.map((point) => new Date(point.calculated_at).getTime());
  const minTime = Math.min(...timestamps);
  const maxTime = Math.max(...timestamps);
  const timeRange = Math.max(maxTime - minTime, 1);
  const x = (time) => plot.left + ((time - minTime) / timeRange) * (width - plot.left - plot.right);
  const y = (value) => plot.top + ((100 - Number(value)) / 100) * (height - plot.top - plot.bottom);

  [0, 20, 40, 60, 80, 100].forEach((value) => {
    const lineY = y(value);
    svg.append(svgElement("line", { x1: plot.left, y1: lineY, x2: width - plot.right, y2: lineY, class: "chart-grid" }));
    const label = svgElement("text", { x: plot.left - 12, y: lineY + 4, class: "chart-axis-label", "text-anchor": "end" });
    label.textContent = `${value}%`;
    svg.append(label);
  });

  const tickCount = 5;
  for (let tick = 0; tick < tickCount; tick += 1) {
    const time = minTime + (timeRange * tick) / (tickCount - 1);
    const lineX = x(time);
    svg.append(svgElement("line", { x1: lineX, y1: plot.top, x2: lineX, y2: height - plot.bottom, class: "chart-grid" }));
    const label = svgElement("text", { x: lineX, y: height - 19, class: "chart-axis-label", "text-anchor": "middle" });
    label.textContent = formatShortDate(time);
    svg.append(label);
  }

  const grouped = new Map();
  timeline.forEach((point) => {
    const id = point.participant_id;
    if (!grouped.has(id)) grouped.set(id, []);
    grouped.get(id).push(point);
  });

  const latest = [];
  [...grouped.entries()].forEach(([id, points], index) => {
    points.sort((a, b) => new Date(a.calculated_at) - new Date(b.calculated_at));
    const row = rowsById.get(id) || {};
    const color = RUNNER_COLORS[(row.avatar_index ?? index) % RUNNER_COLORS.length];
    const group = svgElement("g", {
      class: "runner-series",
      tabindex: "0",
      role: "img",
      "data-runner-id": id,
      "aria-label": `${row.display_name || points[0].display_name}: ${Number(points.at(-1).accuracy).toFixed(1)} por ciento de accuracy`,
    });
    const pathData = points.map((point, pointIndex) => {
      const prefix = pointIndex === 0 ? "M" : "L";
      return `${prefix}${x(new Date(point.calculated_at).getTime()).toFixed(2)},${y(point.accuracy).toFixed(2)}`;
    }).join(" ");
    group.append(svgElement("path", { d: pathData, class: "runner-line", stroke: color }));
    const last = points.at(-1);
    group.append(svgElement("circle", {
      cx: x(new Date(last.calculated_at).getTime()),
      cy: y(last.accuracy),
      r: 5,
      fill: color,
      class: "runner-point",
    }));
    group.addEventListener("mouseenter", () => highlightParticipant(id));
    group.addEventListener("mouseleave", () => highlightParticipant(""));
    group.addEventListener("focus", () => highlightParticipant(id));
    group.addEventListener("blur", () => highlightParticipant(""));
    svg.append(group);
    latest.push({
      id,
      display_name: row.display_name || last.display_name,
      avatar_index: row.avatar_index ?? index,
      rank: Number(last.rank) || null,
      accuracy: Number(last.accuracy),
      coverage: Number(last.coverage ?? 1),
    });
  });

  latest.sort((a, b) => (a.rank ?? 999) - (b.rank ?? 999) || b.accuracy - a.accuracy);
  latest.forEach((runner, index) => {
    if (!runner.rank) runner.rank = index + 1;
    list.append(renderRaceListItem(runner));
  });
}

function renderRaceListItem(runner) {
  const item = document.createElement("li");
  item.tabIndex = 0;
  item.dataset.runnerId = runner.id;

  const rank = document.createElement("span");
  rank.className = "race-rank";
  rank.textContent = runner.rank ? String(runner.rank).padStart(2, "0") : "—";
  item.append(rank, avatarNode(runner.avatar_index, runner.display_name));

  const identity = document.createElement("span");
  identity.className = "race-name";
  const name = document.createElement("strong");
  name.textContent = runner.display_name;
  const coverage = document.createElement("span");
  coverage.textContent = runner.accuracy == null ? "Esperando primer score" : `${Math.round(runner.coverage * 100)}% cobertura`;
  identity.append(name, coverage);

  const score = document.createElement("span");
  score.className = "race-score";
  score.textContent = runner.accuracy == null ? "—" : `${runner.accuracy.toFixed(1)}%`;
  item.append(identity, score);
  item.addEventListener("mouseenter", () => highlightParticipant(runner.id));
  item.addEventListener("mouseleave", () => highlightParticipant(""));
  item.addEventListener("focus", () => highlightParticipant(runner.id));
  item.addEventListener("blur", () => highlightParticipant(""));
  return item;
}

function activateModule(moduleName, updateHash = true) {
  const valid = ["home", "api", "connection"];
  const selected = valid.includes(moduleName) ? moduleName : "home";
  document.querySelectorAll("[data-module]").forEach((module) => {
    const active = module.dataset.module === selected;
    module.hidden = !active;
    module.classList.toggle("is-active", active);
  });
  document.querySelectorAll("[data-module-target]").forEach((button) => {
    const active = button.dataset.moduleTarget === selected;
    button.classList.toggle("is-active", active);
    if (active) button.setAttribute("aria-current", "page");
    else button.removeAttribute("aria-current");
  });
  if (updateHash) {
    if (window.location.hash !== `#${selected}`) history.replaceState(null, "", `#${selected}`);
    const resetScroll = () => {
      document.documentElement.scrollTop = 0;
      document.body.scrollTop = 0;
      document.querySelector(".dashboard-workspace").scrollTop = 0;
    };
    resetScroll();
    window.requestAnimationFrame(resetScroll);
  }
}

function buildDemoData() {
  const names = [
    "Ana M. Rojas", "Andrés P. Gil", "Camila Torres", "Carlos Méndez", "Catalina León", "Daniel Ríos",
    "David Vargas", "Elena Castro", "Felipe Mora", "Gabriela Díaz", "Isabella Rey", "Juan A. Peña",
    "Laura Gómez", "Lucas Herrera", "Manuela Cruz", "María J. Lara", "Mateo Suárez", "Miguel Parra",
    "Natalia Ruiz", "Nicolás Acosta", "Paula Bernal", "Pedro Salazar", "Sara Cárdenas", "Sergio Muñoz",
    "Sofía Prieto", "Tomás Lozano", "Valentina Ortiz", "Victoria Silva", "Alejandro Cano", "Daniela Arias",
    "Esteban Roa", "Juliana Pardo",
  ];
  // Explicitly reviewed against the 6x6 sprite. Do not infer avatar presentation
  // from a person's name in production; the server-provided avatar_index is canonical.
  const avatarIndexes = [
    0, 1, 2, 3, 4, 5, 6, 7,
    8, 9, 11, 10, 12, 13, 14, 16,
    15, 17, 19, 18, 21, 20, 23, 22,
    24, 25, 26, 28, 27, 30, 29, 32,
  ];
  const start = Date.parse("2026-09-08T13:00:00Z");
  const board = names.map((displayName, index) => ({
    participant_id: `demo-${index + 1}`,
    display_name: displayName,
    section_code: "MLOps",
    avatar_index: avatarIndexes[index],
    api_key_active: index < 27,
    submission_status: index < 22 ? "accepted" : null,
    prediction_count: index < 22 ? 48 : 0,
    last_submission_at: index < 22 ? new Date(start + 9 * 86400000 + index * 420000).toISOString() : null,
  }));
  const timeline = [];
  const finalScores = [];
  board.forEach((student, index) => {
    let lastScore = 0;
    for (let step = 0; step < 10; step += 1) {
      const trend = 55 + step * (2.7 + (index % 5) * 0.16);
      const personal = ((index * 13) % 19) - 8;
      const wave = Math.sin(index * 0.9 + step * 0.85) * 4.2;
      const setback = step === 6 && index % 4 === 0 ? -8 : 0;
      lastScore = Math.max(37, Math.min(96.2, trend + personal + wave + setback));
      timeline.push({
        participant_id: student.participant_id,
        display_name: student.display_name,
        calculated_at: new Date(start + step * 86400000).toISOString(),
        accuracy: Number(lastScore.toFixed(2)),
        coverage: Number(Math.min(1, 0.72 + step * 0.03).toFixed(2)),
        rank: null,
      });
    }
    finalScores.push({ id: student.participant_id, accuracy: lastScore });
  });
  finalScores.sort((a, b) => b.accuracy - a.accuracy).forEach((entry, index) => {
    timeline.filter((point) => point.participant_id === entry.id).at(-1).rank = index + 1;
  });
  return {
    dashboard: {
      participant: { public_id: "demo-1", display_name: names[0], preferred_name: "Ana", section: "MLOps" },
      api_key: { key_prefix: "ptm_demo_a7f2", last_used_at: "2026-09-18T14:24:00Z" },
      cycle: { public_id: "warmup-preview", data_cutoff: "2026-09-18T12:00:00Z", expected_predictions: 48, closes_at: "2026-09-19T23:59:00-05:00" },
      submissions: [{ status: "accepted", model_version: "baseline-v1", prediction_count: 48, received_at: "2026-09-18T14:24:00Z" }],
    },
    board: { mode: "preview", count: board.length, cycle: { public_id: "warmup-preview", expected_predictions: 48 }, data: board, timeline },
  };
}

async function loadDashboard() {
  const payload = DEMO_MODE ? buildDemoData() : {
    dashboard: await api("/v1/portal/dashboard"),
    board: await api("/v1/portal/leaderboard"),
  };
  renderDashboard(payload.dashboard);
  renderBoard(payload.board);
  renderRace(payload.board);
  loginView.hidden = true;
  dashboardView.hidden = false;
  document.body.classList.add("dashboard-mode");
  activateModule(window.location.hash.slice(1), false);
}

document.querySelectorAll("[data-module-target]").forEach((button) => {
  button.addEventListener("click", () => activateModule(button.dataset.moduleTarget));
});

window.addEventListener("hashchange", () => activateModule(window.location.hash.slice(1), false));

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
  if (DEMO_MODE) return showMessage("Vista de propuesta: no se generó ninguna credencial real.");
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await api("/v1/portal/api-key", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
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

document.querySelector("#rotate-key-button").addEventListener("click", async (event) => {
  if (DEMO_MODE) return showMessage("Vista de propuesta: la credencial de demostración no se puede rotar.");
  const accepted = window.confirm("La API key actual dejará de funcionar inmediatamente. ¿Generar una nueva?");
  if (!accepted) return;
  const button = event.currentTarget;
  button.disabled = true;
  try {
    const result = await api("/v1/portal/api-key/rotate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm_revoke: true }),
    });
    latestKey = result.api_key;
    setText("#new-api-key", latestKey);
    document.querySelector("#copy-key-button").textContent = "Copiar API key";
    document.querySelector("#secret-panel").hidden = false;
    await loadDashboard();
    showMessage("API key anterior revocada. Guarda la nueva antes de salir o recargar.");
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
    showMessage(DEMO_MODE ? "Vista simulada restablecida." : "Datos actualizados.");
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#logout-button").addEventListener("click", async () => {
  if (DEMO_MODE) return showMessage("Vista de propuesta: abre la URL sin ?demo=1 para volver al acceso real.");
  await api("/v1/portal/logout", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
  latestKey = null;
  dashboardView.hidden = true;
  loginView.hidden = false;
  document.body.classList.remove("dashboard-mode");
  document.querySelector("#secret-panel").hidden = true;
});

loadDashboard().catch((error) => {
  document.body.classList.remove("dashboard-mode");
  if (error.status !== 401) {
    const errorNode = document.querySelector("#login-error");
    errorNode.textContent = "El portal no está disponible temporalmente. Intenta de nuevo.";
    errorNode.hidden = false;
  }
});
