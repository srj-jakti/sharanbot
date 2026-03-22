// ═══════════════════════════════════════════════
// Native Dashboard Panel — WebSocket client & renderer
// Connects to SharanBot dashboard server
// ═══════════════════════════════════════════════

(function () {
  'use strict';

  const PORTS = [18795, 18791];
  let ws = null;
  let dashPort = null;
  let logBuffer = [];
  let reconnectTimer = null;
  let initialized = false;

  // DOM refs (lazy)
  const el = (id) => document.getElementById(id);

  function esc(s) {
    if (!s) return '';
    const d = document.createElement('div');
    d.textContent = String(s);
    return d.innerHTML;
  }

  function formatUptime(s) {
    if (!s || s <= 0) return '-';
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = Math.floor(s % 60);
    if (h > 0) return `${h}h ${m}m`;
    if (m > 0) return `${m}m ${sec}s`;
    return `${sec}s`;
  }

  // ── Sub-tab navigation ──
  function initDashNav() {
    document.querySelectorAll('.dash-nav-btn').forEach(btn => {
      btn.addEventListener('click', () => {
        document.querySelectorAll('.dash-nav-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        const tab = btn.dataset.dashTab;
        document.querySelectorAll('.dash-section').forEach(s => s.classList.remove('active'));
        const target = el('dash-' + tab);
        if (target) target.classList.add('active');
        // Lazy-load integrations
        if (tab === 'integrations') loadIntegrations();
      });
    });
  }

  // ── WebSocket ──
  async function discoverPort() {
    for (const port of PORTS) {
      try {
        const res = await fetch(`http://127.0.0.1:${port}/api/status`, {
          signal: AbortSignal.timeout(1500)
        });
        if (res.ok) return port;
      } catch (_) { /* try next */ }
    }
    return PORTS[0]; // fallback
  }

  async function connect() {
    if (ws && (ws.readyState === WebSocket.CONNECTING || ws.readyState === WebSocket.OPEN)) return;
    if (!dashPort) dashPort = await discoverPort();

    const dot = el('dash-dot');
    const statusText = el('dash-status-text');

    try {
      ws = new WebSocket(`ws://127.0.0.1:${dashPort}/ws`);
    } catch (e) {
      if (statusText) statusText.textContent = 'Connection failed';
      scheduleReconnect();
      return;
    }

    ws.onopen = () => {
      if (dot) dot.classList.add('connected');
      if (statusText) statusText.textContent = 'Connected';
      addLog('Dashboard connected');
    };

    ws.onclose = () => {
      if (dot) dot.classList.remove('connected');
      if (statusText) statusText.textContent = 'Disconnected';
      addLog('Disconnected — reconnecting...');
      scheduleReconnect();
    };

    ws.onerror = () => { /* onclose will fire */ };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'snapshot' || msg.type === 'update') {
          updateDashboard(msg.data);
        }
      } catch (_) { /* malformed */ }
    };
  }

  function scheduleReconnect() {
    if (reconnectTimer) return;
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null;
      connect();
    }, 4000);
  }

  // ── Update dashboard ──
  function updateDashboard(data) {
    // Overview stats
    if (data.services) {
      setText('d-svc-running', data.services.running || 0);
      renderServices(data.services.services || []);
    }
    setText('d-teams', (data.teams || []).length);
    setText('d-cron', data.cron_jobs || 0);

    if (data.learning_summary) {
      setText('d-learned', data.learning_summary.total_entries || 0);
      renderLearning(data.learning_summary);
    }

    renderTeams(data.teams || []);

    if (data.health) renderHealth(data.health);

    // Uptime
    if (data.uptime) {
      const u = el('dash-uptime');
      if (u) u.textContent = formatUptime(data.uptime);
    }

    // Summary integrations counts
    if (data.integrations) {
      setText('d-mcp', (data.integrations.mcp_servers || []).length);
      setText('d-tools', data.integrations.tools || 0);
      setText('d-skills', data.integrations.skills || 0);
    }

    addLog('Data updated');
  }

  function setText(id, val) {
    const e = el(id);
    if (e) e.textContent = val;
  }

  // ── Render services ──
  function renderServices(services) {
    const container = el('dash-svc-list');
    if (!container) return;
    if (!services.length) {
      container.innerHTML = '<span class="dash-empty">No services registered</span>';
      return;
    }
    container.innerHTML = services.map(s => `
      <div class="dash-svc-item">
        <span class="svc-name">${esc(s.name)}</span>
        <span class="svc-kind">${esc(s.kind)}</span>
        <span class="dash-badge ${s.status}">${esc(s.status)}</span>
      </div>
    `).join('');
  }

  // ── Render teams ──
  function renderTeams(teams) {
    const container = el('dash-team-list');
    if (!container) return;
    if (!teams.length) {
      container.innerHTML = '<span class="dash-empty">No active teams</span>';
      return;
    }
    container.innerHTML = teams.map(t => {
      const p = t.progress || {};
      return `
        <div class="dash-team-item">
          <div class="team-header">
            <span class="team-id">${esc(t.team_id)}</span>
            <span class="dash-badge ${t.status}">${esc(t.status)}</span>
          </div>
          <div class="team-task">${esc(t.task)} — ${p.completed || 0}/${p.total || 0}</div>
        </div>
      `;
    }).join('');
  }

  // ── Render health ──
  function renderHealth(health) {
    const container = el('dash-circuits');
    if (!container) return;
    if (health.circuits) {
      container.innerHTML = Object.entries(health.circuits).map(([name, state]) => {
        const isOpen = state.is_open || state === 'open';
        return `<div class="dash-circuit">
          <span class="dot ${isOpen ? 'open' : 'closed'}"></span>
          <span class="name">${esc(name)}${isOpen ? ' (OPEN)' : ''}</span>
        </div>`;
      }).join('');
    } else {
      container.innerHTML = '<span class="dash-empty">All circuits healthy</span>';
    }
  }

  // ── Render learning ──
  function renderLearning(summary) {
    const container = el('dash-learning');
    if (!container) return;
    if (summary.total_entries > 0) {
      let html = `<div style="font-size:10px;color:var(--text-dim);margin-bottom:4px">
        Patterns: ${summary.total_entries} | Fixes: ${summary.successful_fixes || 0}
      </div>`;
      if (summary.by_category) {
        html += Object.entries(summary.by_category).map(([cat, count]) =>
          `<span class="dash-badge pending" style="margin:2px">${esc(cat)}: ${count}</span>`
        ).join('');
      }
      container.innerHTML = html;
    }
  }

  // ── Load integrations via REST ──
  async function loadIntegrations() {
    if (!dashPort) dashPort = await discoverPort();
    try {
      const res = await fetch(`http://127.0.0.1:${dashPort}/api/integrations`);
      const data = await res.json();
      renderIntegrations(data);
    } catch (_) { /* silent */ }
  }

  function renderIntegrations(data) {
    setText('d-mcp', (data.mcp_servers || []).length);
    setText('d-tools', (data.tools || []).length);
    setText('d-skills', (data.skills || []).length);

    // MCP servers
    const mcpEl = el('dash-mcp-list');
    if (mcpEl) {
      const servers = data.mcp_servers || [];
      if (servers.length) {
        mcpEl.innerHTML = servers.map(s => {
          const ok = s.connected !== false;
          return `<div class="dash-integ-item">
            <span class="dot" style="background:${ok ? '#10b981' : '#ef4444'}"></span>
            <span class="iname">${esc(s.name)}</span>
            <span class="itag">${ok ? 'On' : 'Off'}</span>
          </div>`;
        }).join('');
      } else {
        mcpEl.innerHTML = '<span class="dash-empty">No MCP servers</span>';
      }
    }

    // Tools
    const toolEl = el('dash-tool-list');
    if (toolEl) {
      const tools = data.tools || [];
      if (tools.length) {
        toolEl.innerHTML = tools.map(t => `
          <div class="dash-integ-item">
            <span class="dot" style="background:${t.category === 'mcp' ? '#fbbf24' : '#10b981'}"></span>
            <span class="iname">${esc(t.name)}</span>
            <span class="itag">${esc(t.category)}</span>
          </div>
        `).join('');
      } else {
        toolEl.innerHTML = '<span class="dash-empty">No tools</span>';
      }
    }
  }

  // ── Live log ──
  function addLog(msg) {
    const ts = new Date().toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    logBuffer.push(`[${ts}] ${msg}`);
    if (logBuffer.length > 100) logBuffer = logBuffer.slice(-100);
    const logEl = el('dash-live-log');
    if (logEl) {
      logEl.innerHTML = logBuffer.slice(-20).map(l => `<div class="log-line">${esc(l)}</div>`).join('');
      logEl.scrollTop = logEl.scrollHeight;
    }
    // Also push to system logs
    const sysEl = el('dash-sys-log');
    if (sysEl) {
      sysEl.innerHTML = logBuffer.slice(-30).map(l => `<div class="log-line">${esc(l)}</div>`).join('');
      sysEl.scrollTop = sysEl.scrollHeight;
    }
  }

  // ── Public init ──
  window.initDashboardPanel = function () {
    if (!initialized) {
      initialized = true;
      initDashNav();
    }
    connect();
  };

  window.destroyDashboardPanel = function () {
    if (reconnectTimer) { clearTimeout(reconnectTimer); reconnectTimer = null; }
    if (ws) { ws.close(); ws = null; }
  };
})();
