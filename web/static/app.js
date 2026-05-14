const TOKEN_KEY = "lockdown_token";

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function formatBytes(b) {
  let n = Number(b) || 0;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i += 1; }
  return `${n.toFixed(2)} ${units[i]}`;
}

function fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("ru-RU", { hour12: false });
}

function fmtDuration(sec) {
  if (sec < 60) return `${sec.toFixed(0)}s`;
  const m = Math.floor(sec / 60);
  const s = Math.floor(sec % 60);
  return `${m}m ${s}s`;
}

async function login(username, password) {
  const res = await fetch("/auth/login", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  const data = await res.json();
  localStorage.setItem(TOKEN_KEY, data.access_token);
  return data.access_token;
}

function logout() {
  localStorage.removeItem(TOKEN_KEY);
  location.reload();
}

let sortState = { column: "total", direction: "desc" };
let lastSnapshot = null;

function compareEntries([emailA, a], [emailB, b]) {
  let valA, valB;
  switch (sortState.column) {
    case "email":    valA = emailA; valB = emailB; break;
    case "uplink":   valA = a.uplink || 0;   valB = b.uplink || 0; break;
    case "downlink": valA = a.downlink || 0; valB = b.downlink || 0; break;
    case "total":
    default:
      valA = (a.uplink || 0) + (a.downlink || 0);
      valB = (b.uplink || 0) + (b.downlink || 0);
      break;
  }
  const dir = sortState.direction === "asc" ? 1 : -1;
  if (typeof valA === "string") return valA.localeCompare(valB) * dir;
  return (valA - valB) * dir;
}

function updateSortIndicators() {
  $$("th[data-sort]").forEach(th => {
    const ind = th.querySelector(".th-indicator");
    if (th.dataset.sort === sortState.column) {
      ind.textContent = sortState.direction === "desc" ? " ↓" : " ↑";
      th.classList.add("active-sort");
    } else {
      ind.textContent = "";
      th.classList.remove("active-sort");
    }
  });
}

function setupSorting() {
  $$("th[data-sort]").forEach(th => {
    th.addEventListener("click", () => {
      const col = th.dataset.sort;
      if (sortState.column === col) {
        sortState.direction = sortState.direction === "asc" ? "desc" : "asc";
      } else {
        sortState.column = col;
        sortState.direction = col === "email" ? "asc" : "desc";
      }
      updateSortIndicators();
      if (lastSnapshot) renderUsers(lastSnapshot);
    });
  });
  updateSortIndicators();
}

function renderUsers(snapshot) {
  lastSnapshot = snapshot;
  const tbody = $("#users-tbody");
  const entries = Object.entries(snapshot || {});
  if (entries.length === 0) {
    tbody.innerHTML = `<tr><td colspan="4" style="color: var(--muted); text-align: center;">Нет данных</td></tr>`;
    return;
  }

  let totalUp = 0, totalDown = 0;
  for (const [, row] of entries) {
    totalUp  += row.uplink   || 0;
    totalDown += row.downlink || 0;
  }

  entries.sort(compareEntries);

  const summaryRow = `
    <tr class="users-total">
      <td>Всего (${entries.length})</td>
      <td class="num">${formatBytes(totalUp)}</td>
      <td class="num">${formatBytes(totalDown)}</td>
      <td class="num">${formatBytes(totalUp + totalDown)}</td>
    </tr>`;

  const userRows = entries.map(([email, row]) => {
    const total = (row.uplink || 0) + (row.downlink || 0);
    return `
      <tr>
        <td>${email}</td>
        <td class="num">${formatBytes(row.uplink || 0)}</td>
        <td class="num">${formatBytes(row.downlink || 0)}</td>
        <td class="num">${formatBytes(total)}</td>
      </tr>`;
  }).join("");

  tbody.innerHTML = summaryRow + userRows;
}

class WSClient {
  constructor(token, onSnapshot, onMetric) {
    this.token = token;
    this.onSnapshot = onSnapshot;
    this.onMetric = onMetric;
    this.ws = null;
    this.retryDelay = 1000;
    this.reconnectCount = 0;
    this.msgCount = 0;
    this.openedAt = 0;
    this.lastMsgAt = 0;
    this.lastPingSentAt = 0;
    this.rttMs = null;
    this.msgRateWindow = [];
    this.closed = false;
    this._uptimeTimer = null;
    this._log = [];
  }

  log(line) {
    const stamp = new Date().toLocaleTimeString("ru-RU", { hour12: false });
    this._log.push(`[${stamp}] ${line}`);
    if (this._log.length > 60) this._log.shift();
    this.onMetric({ log: this._log.join("\n") });
  }

  connect() {
    if (this.closed) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws/stats?token=${encodeURIComponent(this.token)}`;
    this.onMetric({ url, state: "CONNECTING" });
    this.log(`connect → ${url}`);
    const ws = new WebSocket(url);
    this.ws = ws;

    ws.onopen = () => {
      this.openedAt = Date.now();
      this.retryDelay = 1000;
      this.onMetric({ state: "OPEN" });
      this.log("open");
      this._uptimeTimer = setInterval(() => {
        const up = (Date.now() - this.openedAt) / 1000;
        this.onMetric({ uptime: fmtDuration(up) });
      }, 1000);
    };

    ws.onmessage = (ev) => {
      this.msgCount += 1;
      this.lastMsgAt = Date.now();
      this.msgRateWindow.push(this.lastMsgAt);
      const cutoff = this.lastMsgAt - 10_000;
      while (this.msgRateWindow.length && this.msgRateWindow[0] < cutoff) {
        this.msgRateWindow.shift();
      }
      const rate = (this.msgRateWindow.length / 10).toFixed(2);
      this.onMetric({
        count: this.msgCount,
        rate: `${rate} msg/s`,
        last: new Date(this.lastMsgAt).toLocaleTimeString("ru-RU", { hour12: false }),
      });

      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }

      if (msg.type === "snapshot") {
        this.onSnapshot(msg);
      } else if (msg.type === "ping") {
        this.lastPingSentAt = Date.now();
        ws.send(JSON.stringify({ type: "pong", ts: msg.ts }));
        this.log("ping → pong");
      } else if (msg.type === "pong") {
        if (this.lastPingSentAt) {
          this.rttMs = Date.now() - this.lastPingSentAt;
          this.onMetric({ rtt: `${this.rttMs} ms` });
        }
      }
    };

    ws.onclose = (ev) => {
      clearInterval(this._uptimeTimer);
      this.onMetric({ state: `CLOSED (${ev.code})`, uptime: "—" });
      this.log(`close code=${ev.code} reason=${ev.reason || "—"}`);
      if (this.closed) return;
      this.reconnectCount += 1;
      this.onMetric({ reconn: this.reconnectCount });
      const delay = this.retryDelay;
      this.retryDelay = Math.min(this.retryDelay * 2, 30000);
      this.log(`reconnect in ${delay}ms`);
      setTimeout(() => this.connect(), delay);
    };

    ws.onerror = () => {
      this.log("error");
    };
  }

  close() {
    this.closed = true;
    if (this.ws) this.ws.close();
  }
}

function stateToPill(state) {
  const el = $("#ws-state");
  el.textContent = state;
  el.className = "pill " + (
    state === "OPEN" ? "pill-ok" :
    state === "CONNECTING" ? "pill-warn" :
    "pill-err"
  );
}

function updateMetric(patch) {
  if (patch.url !== undefined) $("#m-url").textContent = patch.url;
  if (patch.state !== undefined) {
    $("#m-state").textContent = patch.state;
    stateToPill(patch.state);
  }
  if (patch.rtt !== undefined) $("#m-rtt").textContent = patch.rtt;
  if (patch.rate !== undefined) $("#m-rate").textContent = patch.rate;
  if (patch.count !== undefined) $("#m-count").textContent = patch.count;
  if (patch.reconn !== undefined) $("#m-reconn").textContent = patch.reconn;
  if (patch.uptime !== undefined) $("#m-uptime").textContent = patch.uptime;
  if (patch.last !== undefined) $("#m-last").textContent = patch.last;
  if (patch.log !== undefined) $("#m-log").textContent = patch.log;
}

function handleSnapshot(msg) {
  $("#snapshot-ts").textContent = fmtTime(msg.ts);
  $("#mock-badge").classList.toggle("hidden", !msg.mock);
  renderUsers(msg.snapshot);
}

function setupTabs() {
  $$(".tab-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      $$(".tab-btn").forEach(b => b.classList.toggle("active", b === btn));
      const target = btn.dataset.tab;
      $$(".tab-panel").forEach(p => {
        p.classList.toggle("hidden", p.dataset.panel !== target);
      });
    });
  });
}

function bootDashboard(token) {
  $("#login-view").classList.add("hidden");
  $("#dash-view").classList.remove("hidden");
  setupTabs();
  setupSorting();
  const client = new WSClient(token, handleSnapshot, updateMetric);
  client.connect();

  $("#logout-btn").addEventListener("click", () => {
    client.close();
    logout();
  });
}

function bootLogin() {
  $("#login-view").classList.remove("hidden");
  $("#dash-view").classList.add("hidden");
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("#login-error").textContent = "";
    const fd = new FormData(e.target);
    try {
      const token = await login(fd.get("username"), fd.get("password"));
      bootDashboard(token);
    } catch (err) {
      $("#login-error").textContent = err.message;
    }
  });
}

const existing = localStorage.getItem(TOKEN_KEY);
if (existing) bootDashboard(existing);
else bootLogin();
