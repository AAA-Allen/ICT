const STORAGE_KEY = 'agent-workbench-history-v1';
const SESSION_KEY = 'agent-workbench-session-v1';
const AGENT_NAME = 'workbench';
const TERMINAL_STATUSES = ['COMPLETED', 'FAILED', 'BLOCKED', 'CANCELED'];

function currentSessionId() {
  let id = null;
  try { id = window.localStorage.getItem(SESSION_KEY); } catch { /* storage unavailable */ }
  if (!id) {
    id = `web-${Math.random().toString(36).slice(2, 10)}`;
    try { window.localStorage.setItem(SESSION_KEY, id); } catch { /* storage unavailable */ }
  }
  return id;
}

const state = { tasks: [], activeId: null, running: false, view: 'tasks', live: false };
const $ = (id) => document.getElementById(id);
const nowTime = () => new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit' }).format(new Date());
const clockTime = (iso) => {
  if (!iso) return '--:--';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return '--:--';
  return new Intl.DateTimeFormat('zh-CN', { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(date);
};
const sleep = (ms) => new Promise((resolve) => window.setTimeout(resolve, ms));

function icon(name) { return `<i data-lucide="${name}"></i>`; }
function refreshIcons() { if (window.lucide) window.lucide.createIcons(); }
function escapeHtml(value) { return String(value).replace(/[&<>'"]/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

// ---------------------------------------------------------------- storage
function loadHistory() {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const tasks = JSON.parse(raw);
    if (Array.isArray(tasks)) state.tasks = tasks.filter((task) => task && task.id && task.title);
  } catch { state.tasks = []; }
}
function persistHistory() {
  try { window.localStorage.setItem(STORAGE_KEY, JSON.stringify(state.tasks.slice(0, 30))); } catch { /* storage unavailable */ }
}

// ---------------------------------------------------------------- api
// Credentials stay outside task/history state and are never persisted.
const approvalAuth = (() => {
  let token = '';
  let revision = 0;
  let required = null;
  let notice = '';
  return {
    configure(value) { required = typeof value === 'boolean' ? value : null; this.render(); },
    set(value) {
      if (!/^[\x21-\x7e]{1,4096}$/.test(value)) {
        notice = '请输入不含空白的 ASCII token（最多 4096 字符）。';
        this.render();
        return false;
      }
      token = value; revision += 1; notice = ''; this.render(); return true;
    },
    clear() { token = ''; revision += 1; notice = ''; this.render(); },
    prepare(path, options) {
      const url = new URL(path, window.location.href);
      const protectedWrite = url.origin === window.location.origin
        && (options.method || 'GET').toUpperCase() === 'POST'
        && /^\/api\/runs\/[^/]+\/(approval-decision|approval-resolution|resume)$/.test(url.pathname);
      if (!protectedWrite) return { options, protectedWrite, revision };
      if (required === true && !token) {
        notice = '此操作需要审批凭证，请设置后手动重试。'; this.render();
        const error = new Error('需要审批凭证'); error.code = 'APPROVAL_TOKEN_REQUIRED'; throw error;
      }
      const headers = new Headers(options.headers || {});
      if (token) headers.set('Authorization', `Bearer ${token}`);
      return { options: { ...options, headers, redirect: 'error' }, protectedWrite, revision };
    },
    reject(requestRevision) {
      required = true;
      if (requestRevision === revision) {
        token = ''; revision += 1;
        notice = '审批凭证缺失或无效，请重新设置后手动重试。';
      }
      this.render();
    },
    render() {
      const status = $('approvalTokenStatus');
      if (status) status.textContent = notice || (token
        ? '凭证已在本页内存设置；是否有效以服务端操作结果为准。'
        : required === true ? '服务端要求审批凭证；当前未设置。'
          : required === false ? '服务端当前未要求审批凭证。' : '尚未获知服务端是否要求审批凭证。');
    },
  };
})();

function applyApprovalToken() {
  const input = $('approvalTokenInput');
  const value = input.value;
  input.value = '';
  approvalAuth.set(value);
}

function isApprovalAuthError(error) {
  return ['APPROVAL_AUTH_REQUIRED', 'APPROVAL_TOKEN_REQUIRED'].includes(error.code);
}

async function handleApprovalAuthError(task, error, previous) {
  if (!isApprovalAuthError(error)) return false;
  Object.assign(task, previous);
  $('settingsModal').showModal();
  $('approvalTokenInput').focus();
  try {
    // A preceding decision/resolution may already be durable. Only read it back;
    // never retry a write automatically after credential failure.
    const payload = await apiFetch(`/api/runs/${task.id}`, { cache: 'no-store' });
    if (['RUNNING', 'PENDING'].includes(payload.run.status)) {
      task.run = payload.run; task.status = 'running'; pollLiveTask(task);
    } else completeLiveTask(task, payload.run, payload.session);
  } catch {
    persistHistory();
    if (state.activeId === task.id) selectTask(task.id); else renderHistory();
  } finally { state.running = task.status === 'running'; }
  return true;
}

async function apiFetch(path, options = {}, timeoutMs = 4000) {
  const request = approvalAuth.prepare(path, options);
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(path, { ...request.options, signal: controller.signal });
    if (!response.ok) {
      if (request.protectedWrite && response.status === 401) {
        approvalAuth.reject(request.revision);
        const error = new Error('审批凭证缺失或无效');
        error.code = 'APPROVAL_AUTH_REQUIRED';
        throw error;
      }
      let message = `HTTP ${response.status}`;
      let code = `HTTP_${response.status}`;
      try {
        const body = await response.json();
        if (body && body.error) { message = body.error.message || message; code = body.error.code || code; }
      } catch { /* non-json error body */ }
      const error = new Error(message);
      error.code = code;
      throw error;
    }
    return await response.json();
  } catch (error) {
    if (error.name === 'AbortError') { error.code = 'TIMEOUT'; error.message = '请求超时'; }
    throw error;
  } finally { window.clearTimeout(timer); }
}

// ---------------------------------------------------------------- harness evidence
function summarizeHarness(payload, runId) {
  if (!payload || payload.run_id !== runId || !Array.isArray(payload.events)) throw new Error('INVALID_HARNESS_EVIDENCE');
  const events = payload.events;
  const ids = new Set();
  events.forEach((event, index) => {
    if (!event || event.run_id !== runId || event.sequence !== index + 1
        || typeof event.event_id !== 'string' || !event.event_id || ids.has(event.event_id)
        || typeof event.event_type !== 'string' || typeof event.status !== 'string'
        || !event.summary || typeof event.summary !== 'object' || Array.isArray(event.summary)) {
      throw new Error('INVALID_HARNESS_EVIDENCE');
    }
    ids.add(event.event_id);
  });
  if (!events.length) return { status: 'EMPTY', events };
  const count = type => events.filter(event => event.event_type === type).length;
  const replies = events.filter(event => event.event_type === 'model.completed');
  if (replies.some(event => event.summary.usage != null && (typeof event.summary.usage !== 'object'
      || Array.isArray(event.summary.usage)))) throw new Error('INVALID_HARNESS_EVIDENCE');
  const usage = {};
  ['prompt_tokens', 'completion_tokens', 'total_tokens'].forEach(key => {
    const values = replies.filter(event => Object.hasOwn(event.summary.usage || {}, key))
      .map(event => event.summary.usage[key]);
    if (values.some(value => !Number.isSafeInteger(value) || value < 0)) throw new Error('INVALID_HARNESS_EVIDENCE');
    if (values.length) {
      const total = values.reduce((sum, value) => sum + value, 0);
      if (!Number.isSafeInteger(total)) throw new Error('INVALID_HARNESS_EVIDENCE');
      usage[key] = { total, covered: values.length };
    }
  });
  const retries = new Set();
  let plannedRetries = 0;
  events.forEach(event => {
    if (/^(model|tool)\.attempt_(failed|completed|blocked)$/.test(event.event_type)) {
      const attempt = event.summary.attempt;
      if (!Number.isSafeInteger(attempt) || attempt < 1) throw new Error('INVALID_HARNESS_EVIDENCE');
      if (Object.hasOwn(event.summary, 'retry') && typeof event.summary.retry !== 'boolean') throw new Error('INVALID_HARNESS_EVIDENCE');
      if (attempt > 1) retries.add(JSON.stringify([event.event_type.split('.')[0], event.node_id,
        event.execution_id, event.call_id, attempt]));
      if (event.summary.retry === true) plannedRetries += 1;
    }
  });
  const claims = events.filter(event => event.event_type === 'run_budget.claimed');
  const settlements = events.filter(event => event.event_type === 'run_budget.settled');
  let budget = null;
  if (claims.length) {
    const limits = claims[0].summary.limits;
    const valid = limits && ['model', 'tool'].every(key => Number.isSafeInteger(limits[key]) && limits[key] > 0);
    const claimMap = new Map(claims.map(event => [event.event_id, event]));
    const settled = new Set();
    let consistent = valid;
    claims.forEach(event => {
      consistent &&= ['model', 'tool'].includes(event.summary.kind)
        && event.summary.limits?.model === limits.model && event.summary.limits?.tool === limits.tool;
    });
    settlements.forEach(event => {
      const claim = claimMap.get(event.summary.claim_id);
      consistent &&= !!claim && claim.summary.kind === event.summary.kind && !settled.has(event.summary.claim_id);
      settled.add(event.summary.claim_id);
    });
    if (consistent) budget = { limits, used: { model: claims.filter(e => e.summary.kind === 'model').length,
      tool: claims.filter(e => e.summary.kind === 'tool').length }, outstanding: claims.length - settled.size };
    else budget = { inconsistent: true };
  } else if (settlements.length) budget = { inconsistent: true };
  return { status: 'RECORDED', events, modelRequests: count('model.requested'), modelReplies: replies.length,
    toolRequests: count('tool.requested'), toolResults: count('tool.completed'),
    retries: retries.size, plannedRetries, usage, budget };
}

function harnessBody(summary) {
  if (summary.status === 'EMPTY') return '<p>未记录调用证据，调用次数、预算与 usage 均未知。</p>';
  const budget = !summary.budget ? '运行级预算未记录，尝试占用未知。'
    : summary.budget.inconsistent ? '预算记录不一致，额度未知。'
    : ['model', 'tool'].map(kind => `${kind === 'model' ? '模型' : '工具'}尝试占用 ${summary.budget.used[kind]} / ${summary.budget.limits[kind]}`)
      .join('；') + `；未结算 ${summary.budget.outstanding}（不代表仍在运行）。`;
  const usageEntries = Object.entries(summary.usage);
  const usage = usageEntries.length ? usageEntries.map(([key, value]) =>
    `${key}: ${value.total}（覆盖 ${value.covered}/${summary.modelReplies} 份回复）`).join('；') : '未报告 token usage，数值未知。';
  const events = summary.events.slice(-200);
  return `<div class="harness-counts"><span>模型请求记录 <b>${summary.modelRequests}</b> / 完成回复 <b>${summary.modelReplies}</b></span>
    <span>工具请求记录 <b>${summary.toolRequests}</b> / 成功结果 <b>${summary.toolResults}</b></span>
    <span>已记录额外重试尝试 <b>${summary.retries}</b> / 重试计划 <b>${summary.plannedRetries}</b></span></div>
    <p>${escapeHtml(budget)}</p><p>${escapeHtml(usage)}</p>
    <p class="harness-note">仅当前运行的审计快照。请求、预算占用与外部实际执行次数不同；重试计划可能被取消。占用可含同步委派，usage 不跨子运行合并。</p>
    <details><summary>调用事件（展示末 ${events.length} / 共 ${summary.events.length} 条）</summary>
    <div class="harness-events"><table><thead><tr><th>#</th><th>节点</th><th>事件 / 工具</th><th>状态 / 原因</th></tr></thead><tbody>${events.map(event =>
      `<tr><td>${event.sequence}</td><td>${escapeHtml(event.node_id || '—')}</td><td>${escapeHtml(event.event_type)}${event.summary.tool ? ' · ' + escapeHtml(event.summary.tool) : ''}</td><td>${escapeHtml(event.status)}${event.reason_code ? ' · ' + escapeHtml(event.reason_code) : ''}</td></tr>`).join('')}</tbody></table></div></details>`;
}

async function loadHarnessEvidence(runId, panel, isCurrent = () => true) {
  const version = (panel.harnessVersion || 0) + 1;
  panel.harnessVersion = version;
  const current = () => panel.harnessVersion === version && panel.isConnected && isCurrent();
  panel.innerHTML = '<strong>调用事实</strong><p>正在读取服务端审计；计数与额度暂未知。</p>';
  let body;
  try {
    const payload = await apiFetch(`/api/runs/${encodeURIComponent(runId)}/harness-events`, { cache: 'no-store' });
    body = harnessBody(summarizeHarness(payload, runId));
  } catch {
    body = '<p>调用证据不可用或格式异常；计数、额度及 usage 未知。运行状态仍以运行结果为准。</p>';
  }
  if (!current()) return;
  panel.innerHTML = `<div class="harness-head"><strong>调用事实</strong><button type="button" class="text-button" data-harness-refresh>刷新</button></div>${body}`;
  panel.querySelector('[data-harness-refresh]').addEventListener('click', () => loadHarnessEvidence(runId, panel, isCurrent));
}

function renderHarnessPanel(task) {
  const old = $('harnessPanel');
  if (old) old.remove();
  if (task.mode !== 'live' || !state.live) return;
  const panel = document.createElement('section');
  panel.id = 'harnessPanel';
  panel.className = 'harness-panel';
  $('timeline').after(panel);
  const run = task.run;
  const status = task.status;
  loadHarnessEvidence(task.id, panel, () => $('harnessPanel') === panel && state.activeId === task.id
    && task.run === run && task.status === status);
}

const AGENT_SELECT_KEY = 'agent-workbench-agent-v1';

async function populateAgentSelect() {
  const select = $('agentSelect');
  if (!select) return;
  try {
    const payload = await apiFetch('/api/agents', {}, 3000);
    select.innerHTML = payload.agents.map((agent) => `<option value="${escapeHtml(agent.name)}">${escapeHtml(agent.name)}</option>`).join('');
    let preferred = null;
    try { preferred = window.localStorage.getItem(AGENT_SELECT_KEY); } catch { /* storage unavailable */ }
    const names = payload.agents.map((agent) => agent.name);
    if (preferred && names.includes(preferred)) select.value = preferred;
    else if (names.includes(AGENT_NAME)) select.value = AGENT_NAME;
    select.addEventListener('change', () => {
      try { window.localStorage.setItem(AGENT_SELECT_KEY, select.value); } catch { /* storage unavailable */ }
    });
  } catch { select.innerHTML = `<option value="${AGENT_NAME}">${AGENT_NAME}</option>`; }
}

async function detectMode() {
  try {
    const health = await apiFetch('/api/health', {}, 1500);
    approvalAuth.configure(health.approval_auth_required);
    state.live = true;
  } catch { state.live = false; approvalAuth.configure(null); }
  updateModeUi();
  if (state.live) populateAgentSelect();
}

function updateModeUi() {
  document.body.classList.toggle('live', state.live);
  $('brandStatusText').textContent = state.live ? '在线运行' : '离线演示';
  $('demoBadgeText').textContent = state.live ? 'Runtime 已连接' : '本地演示模式';
  $('inputMode').textContent = state.live ? '实时执行' : '演示模式';
  const note = document.querySelector('.settings-note code');
  if (note) note.textContent = 'python -m agent_core.server';
}

async function clearSessionMemory() {
  if (!state.live) return;
  try {
    await apiFetch(`/api/sessions/${currentSessionId()}`, { method: 'DELETE' }, 4000);
    const button = $('clearSessionButton');
    if (button) { const original = button.textContent; button.textContent = '已清除'; window.setTimeout(() => { button.textContent = original; }, 1500); }
  } catch { /* session may not exist yet */ }
}

function markOffline() {
  if (!state.live) return;
  state.live = false;
  updateModeUi();
}

// ---------------------------------------------------------------- rendering
function statusLabel(task) {
  if (task.status === 'running') return '执行中';
  if (task.status === 'waiting') return '等待批准';
  if (task.status === 'failed') return '执行失败';
  return '已完成';
}
function pillForTask(task) {
  if (task.status === 'running') return ['执行中', 'running'];
  if (task.status === 'waiting') return ['待审批', 'waiting'];
  if (task.status === 'failed') return ['失败', 'failed'];
  return ['已完成', 'completed'];
}
function taskIcon(task) {
  if (task.status === 'failed') return 'triangle-alert';
  if (task.status === 'waiting') return 'shield-alert';
  return 'wand-sparkles';
}

function renderHistory() {
  const list = $('historyList');
  $('taskCount').textContent = `${state.tasks.length} 个`;
  $('emptyHistory').classList.toggle('hidden', state.tasks.length > 0);
  list.innerHTML = state.tasks.map((task) => `<button class="history-item ${task.id === state.activeId ? 'active' : ''}" data-task-id="${escapeHtml(task.id)}"><span class="history-icon">${icon(taskIcon(task))}</span><span class="history-content"><span class="history-name">${escapeHtml(task.title)}</span><span class="history-meta"><span class="mini-status ${task.status}"></span>${statusLabel(task)} · ${escapeHtml(task.time)}</span></span></button>`).join('');
  list.querySelectorAll('[data-task-id]').forEach((item) => item.addEventListener('click', () => selectTask(item.dataset.taskId, true)));
  refreshIcons();
}

function timelineHtml(steps) {
  return `<div class="timeline-list">${steps.map((step, index) => `<div class="timeline-step ${step.state}"><span class="step-marker">${icon(step.marker)}</span>${index < steps.length - 1 ? '<span class="step-rail"></span>' : ''}<div><div class="step-name">${escapeHtml(step.title)}</div><div class="step-desc">${escapeHtml(step.desc)}</div></div><span class="step-time">${escapeHtml(step.time || '--:--')}</span></div>`).join('')}</div>`;
}

function demoTimeline(task, phase = task.status) {
  const steps = [
    { title: '任务已接收', desc: '已理解你的目标，准备拆解执行。', marker: 'inbox', state: 'done', time: task.receivedAt },
    { title: '任务处理中', desc: 'Agent 正在整理信息并执行步骤。', marker: phase === 'running' ? 'loader-circle' : 'loader-circle', state: phase === 'running' ? 'current' : 'done', time: phase === 'running' ? '现在' : (task.completedAt || '--:--') },
    { title: phase === 'failed' ? '任务未完成' : '任务执行完成', desc: phase === 'failed' ? '执行过程中遇到问题，请查看错误提示。' : '所有步骤已完成，结果已经准备好。', marker: phase === 'failed' ? 'triangle-alert' : 'check', state: phase === 'failed' ? 'failed' : 'done', time: task.completedAt || '--:--' },
  ];
  if (phase === 'running') steps[2].state = '';
  $('timeline').innerHTML = timelineHtml(steps);
  refreshIcons();
}

const NODE_OUTCOMES = { 'node.completed': ['成功', 'done', 'check'], 'node.blocked': ['已阻断', 'failed', 'shield-alert'], 'node.failed': ['失败', 'failed', 'triangle-alert'] };

function runTimeline(run) {
  const steps = [{ title: '任务已接收', desc: `运行 ${run.run_id}`, marker: 'inbox', state: 'done', time: clockTime(run.trace[0] && run.trace[0].started_at) }];
  const terminalByExecution = new Map();
  run.trace.forEach((event) => {
    if (NODE_OUTCOMES[event.event_type]) terminalByExecution.set(event.execution_id, event);
  });
  const seen = new Set();
  const roundsByNode = {};
  (run.state && typeof run.state === 'object' ? Object.entries(run.state) : []).forEach(([key, value]) => {
    if (key.endsWith('_rounds') && Array.isArray(value)) {
      value.forEach((round) => {
        if (round && typeof round === 'object' && typeof round.tool === 'string') {
          (roundsByNode[key.replace(/_rounds$/, '')] || (roundsByNode[key.replace(/_rounds$/, '')] = [])).push(round);
        }
      });
    }
  });
  run.trace.forEach((event) => {
    if (event.event_type !== 'node.started' || seen.has(event.node_id)) return;
    seen.add(event.node_id);
    const outcome = terminalByExecution.get(event.execution_id);
    const [label, nodeState, marker] = outcome ? NODE_OUTCOMES[outcome.event_type] : ['执行中', 'current', 'loader-circle'];
    steps.push({ title: event.node_id, desc: label, marker, state: nodeState, time: clockTime(event.started_at) });
    (roundsByNode[event.node_id] || []).forEach((round, index) => {
      const args = JSON.stringify(round.arguments || {});
      const output = JSON.stringify(round.output || {});
      steps.push({
        title: `↳ ${round.tool} · 第 ${index + 1} 轮`,
        desc: `${args.length > 80 ? args.slice(0, 80) + '…' : args} → ${output.length > 80 ? output.slice(0, 80) + '…' : output}`,
        marker: 'wrench',
        state: nodeState === 'current' ? 'current' : 'done',
        time: '',
      });
    });
  });
  const final = run.trace[run.trace.length - 1];
  const waiting = run.status === 'WAITING_APPROVAL';
  const failed = run.status !== 'COMPLETED' && !waiting;
  steps.push({
    title: waiting ? '等待人工批准' : failed ? '任务未完成' : '任务执行完成',
    desc: waiting
      ? `工具 ${run.pending_approval ? run.pending_approval.tool_name : ''} 需要批准后才能继续`
      : failed ? `原因码 ${run.reason_code || run.status}` : '所有步骤已完成，结果已就绪。',
    marker: waiting ? 'shield-alert' : failed ? 'triangle-alert' : 'check',
    state: waiting ? 'current' : failed ? 'failed' : 'done',
    time: clockTime(final && final.started_at),
  });
  return steps;
}

const LIVE_PHASES = { 'run.created': ['任务已接收', 'inbox'], 'run.running': ['Runtime 执行中', 'loader-circle'], 'run.resumed': ['恢复执行', 'rotate-cw'], 'run.recovered': ['边界恢复', 'rotate-cw'] };

function liveTimeline(task) {
  const events = task.lifecycle || [];
  const steps = [];
  events.forEach((event) => {
    const phase = LIVE_PHASES[event.event_type];
    if (phase) steps.push({ title: phase[0], desc: `事件 ${event.event_type}`, marker: event.event_type === 'run.running' ? 'loader-circle' : phase[1], state: event.event_type === 'run.running' ? 'current' : 'done', time: clockTime(event.occurred_at) });
  });
  if (!steps.length) steps.push({ title: '已提交', desc: '等待 Runtime 接收…', marker: 'loader-circle', state: 'current', time: task.receivedAt });
  return steps;
}

function chatHtml(messages) {
  return `<div class="chat-thread">${messages.map((m) => `<div class="chat-row ${m.role}"><span class="chat-role">${m.role === 'user' ? '你' : '助手'}</span><div class="chat-bubble">${escapeHtml(m.content)}</div></div>`).join('')}</div>`;
}

function updateStreamOutput(task) {
  if (state.view === 'logs') return;
  let box = $('streamOutput');
  if (!task.streamText && !task.streamGap) { if (box) box.remove(); return; }
  if (!box) {
    box = document.createElement('div');
    box.id = 'streamOutput';
    box.className = 'stream-output';
    box.innerHTML = `<div class="stream-head">${icon('type')}<span>模型输出 · ${escapeHtml(task.streamNode || '')}</span></div><pre class="stream-body"></pre>`;
    $('timeline').after(box);
    refreshIcons();
  }
  const body = box.querySelector('.stream-body');
  body.textContent = (task.streamGap ? '流式预览不完整，请以运行结果为准。\n' : '') + (task.streamText || '');
  body.scrollTop = body.scrollHeight;
}

function renderTaskView(task) {
  if (state.view === 'logs') return;
  const staleChat = $('chatView');
  if (staleChat) staleChat.remove();
  const [pillText, pillClass] = pillForTask(task);
  $('statePill').className = `state-pill ${pillClass}`;
  $('statePill').textContent = pillText;
  updateCancelButton(task);
  updateResumeButton(task);
  const chatMessages = task.session && task.session.memory && Array.isArray(task.session.memory.messages)
    ? task.session.memory.messages : null;
  if (task.status === 'running' && task.mode === 'live') {
    $('timeline').innerHTML = timelineHtml(liveTimeline(task));
  } else if (task.mode === 'live' && chatMessages && chatMessages.length) {
    $('timeline').innerHTML = `<div class="timeline-empty">${icon('messages-square')}<span>会话共 ${chatMessages.length} 条消息</span></div>`;
    $('timeline').after(Object.assign(document.createElement('div'), { id: 'chatView', className: 'chat-view', innerHTML: chatHtml(chatMessages) }));
  } else if (task.mode === 'live' && task.run) {
    $('timeline').innerHTML = timelineHtml(runTimeline(task.run));
  } else {
    demoTimeline(task);
  }
  updateStreamOutput(task);
  updateResult(task);
  renderApprovalPanel(task);
  renderHarnessPanel(task);
  renderRecoveryPanel(task);
  refreshIcons();
}

function selectTask(id, activate = false) {
  const task = state.tasks.find((item) => item.id === id); if (!task) return;
  if (activate) {
    state.view = 'tasks';
    document.querySelectorAll('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.view === 'tasks'));
  }
  if (state.view === 'logs') { renderHistory(); return; }
  state.activeId = id; $('runId').textContent = task.id; $('executionTitle').textContent = task.title;
  renderTaskView(task); renderHistory();
}

function updateResult(task) {
  const panel = $('resultPanel');
  if (task.status === 'running') { panel.classList.add('hidden'); updateCancelButton(task); return; }
  panel.classList.remove('hidden');
  panel.classList.toggle('failed', task.status === 'failed');
  panel.classList.toggle('waiting', task.status === 'waiting');
  $('resultTitle').textContent = task.status === 'failed' ? '任务执行失败'
    : task.status === 'waiting' ? '等待人工批准' : '任务已完成';
  $('resultSummary').textContent = task.summary;
  panel.querySelector('.result-icon').innerHTML = icon(task.status === 'failed' ? 'triangle-alert'
    : task.status === 'waiting' ? 'shield-alert' : 'check');
  updateResumeButton(task);
  refreshIcons();
}

function updateCancelButton(task) {
  let button = $('cancelButton');
  const show = task && task.mode === 'live' && task.status === 'running' && state.live;
  if (!show) { if (button) button.remove(); return; }
  if (!button) {
    button = document.createElement('button');
    button.id = 'cancelButton';
    button.className = 'cancel-button';
    button.innerHTML = `${icon('circle-slash')}<span>取消运行</span>`;
    button.addEventListener('click', () => requestCancel());
    document.querySelector('.card-topline').appendChild(button);
  }
  refreshIcons();
}

function updateResumeButton(task) {
  let button = $('resumeButton');
  const show = task && task.mode === 'live' && task.status !== 'running' && !!task.run && task.run.resumable && state.live;
  if (!show) { if (button) button.remove(); return; }
  if (!button) {
    button = document.createElement('button');
    button.id = 'resumeButton';
    button.className = 'resume-button';
    button.innerHTML = `${icon('rotate-cw')}<span>恢复运行</span>`;
    button.addEventListener('click', () => beginResume(task));
    document.querySelector('.card-topline').appendChild(button);
  }
  refreshIcons();
}

let resumeContext = null;

function shallowDiff(original, edited) {
  const updates = {};
  for (const key of Object.keys(edited)) {
    if (JSON.stringify(original[key]) !== JSON.stringify(edited[key])) updates[key] = edited[key];
  }
  return updates;
}

async function beginResume(task) {
  if (!task.run || !task.run.resumable || state.running) return;
  let checkpoint;
  try {
    const payload = await apiFetch(`/api/runs/${task.id}/checkpoint`, {}, 5000);
    checkpoint = payload.checkpoint;
  } catch (error) {
    window.alert(`无法读取检查点:${error.message}`);
    return;
  }
  resumeContext = { task, original: checkpoint.state && typeof checkpoint.state === 'object' ? checkpoint.state : {} };
  $('resumeTitle').textContent = `恢复运行`;
  $('resumeHint').textContent = `将从节点 ${checkpoint.next_node} 继续（状态版本 ${checkpoint.state_version}，已执行 ${checkpoint.step_count} 步）。检查点状态如下，可直接编辑；确认后仅提交修改过的顶层键。`;
  $('resumeStateEditor').value = JSON.stringify(checkpoint.state || {}, null, 2);
  $('resumeModal').showModal(); refreshIcons();
}

async function confirmResume() {
  if (!resumeContext) return;
  const resumeDraft = resumeContext;
  let edited;
  try {
    edited = JSON.parse($('resumeStateEditor').value);
  } catch {
    window.alert('状态不是合法 JSON,请修正后重试。');
    return;
  }
  if (!edited || typeof edited !== 'object' || Array.isArray(edited)) {
    window.alert('状态需要是 JSON 对象。');
    return;
  }
  const updates = shallowDiff(resumeContext.original, edited);
  const task = resumeContext.task;
  const previous = { status: task.status, lifecycle: task.lifecycle };
  resumeContext = null;
  $('resumeModal').close();
  state.running = true;
  task.status = 'running';
  task.lifecycle = [];
  selectTask(task.id);
  try {
    const payload = await apiFetch(`/api/runs/${task.id}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state_updates: updates }),
    }, 30000);
    completeLiveTask(task, payload.run, payload.session);
  } catch (error) {
    if (isApprovalAuthError(error)) {
      resumeContext = resumeDraft;
      $('resumeModal').showModal();
      await handleApprovalAuthError(task, error, previous);
      return;
    }
    state.running = false;
    if (error.code === 'UNKNOWN_RUN') { failTask(task, '服务端没有这条运行的记录。'); return; }
    task.status = 'failed';
    task.completedAt = nowTime();
    task.summary = `恢复失败:${error.message}`;
    persistHistory();
    selectTask(task.id);
  }
}

async function requestCancel() {
  const task = state.tasks.find((item) => item.id === state.activeId);
  if (!task || task.status !== 'running' || task.mode !== 'live') return;
  try {
    await apiFetch(`/api/runs/${task.id}/cancel`, { method: 'POST' }, 4000);
    const pill = $('statePill');
    pill.textContent = '取消请求已发送';
  } catch { /* run may have finished already */ }
}

// ---------------------------------------------------------------- approvals
const shortDigest = (digest) => (typeof digest === 'string' && digest.length > 19
  ? `${digest.slice(0, 19)}…` : String(digest || '—'));

let approvalPanelVersion = 0;

function removeApprovalPanel() {
  approvalPanelVersion += 1;
  const panel = $('approvalPanel');
  if (panel) panel.remove();
}

function renderApprovalPanel(task) {
  removeApprovalPanel();
  if (!state.live || task.mode !== 'live' || !task.run || task.status === 'running') return;
  const run = task.run;
  if (run.status === 'WAITING_APPROVAL' && run.pending_approval) {
    $('timeline').after(buildWaitingPanel(task, run.pending_approval));
    refreshIcons();
    // A crash can leave an approved run waiting with a STARTED tool.
    // The HTTP preflight now refuses resume without changing that status.
    buildReconcilePanel(task);
  } else if (run.status === 'BLOCKED' && run.reason_code === 'TOOL_OUTCOME_UNKNOWN') {
    buildReconcilePanel(task);
  }
}

function buildWaitingPanel(task, request) {
  const el = document.createElement('div');
  el.id = 'approvalPanel';
  el.className = 'approval-panel';
  el.innerHTML = `
    <div class="approval-head">${icon('shield-alert')}<span>工具调用等待人工批准</span></div>
    <div class="approval-meta">
      <div><span>工具</span><strong>${escapeHtml(request.tool_name)}</strong></div>
      <div><span>请求 ID</span><code>${escapeHtml(request.request_id)}</code></div>
      <div><span>参数摘要</span><code title="${escapeHtml(request.argument_digest)}">${escapeHtml(shortDigest(request.argument_digest))}</code></div>
    </div>
    ${approvalDisplayHtml(request.display)}
    <p class="approval-note">仅展示工具声明的动作和允许公开的字段。批准只记录决定，继续执行需要显式恢复；结果不明时停止重放并人工核对。拒绝后不可直接恢复。</p>
    <div class="approval-actions">
      <button class="text-button" data-approval="reject">拒绝</button>
      <button class="primary-button" data-approval="approve"><span>批准并继续</span><i data-lucide="check"></i></button>
    </div>`;
  el.querySelector('[data-approval="approve"]').addEventListener('click', () => submitApprovalDecision(task, 'APPROVED'));
  el.querySelector('[data-approval="reject"]').addEventListener('click', () => submitApprovalDecision(task, 'REJECTED'));
  return el;
}

function approvalDisplayHtml(display) {
  if (!display || typeof display.description !== 'string' || !display.fields
      || !display.description.trim() || display.description.length > 500
      || typeof display.fields !== 'object' || Array.isArray(display.fields)
      || Object.keys(display.fields).length > 8
      || Object.entries(display.fields).some(([key, value]) => key.length > 64
        || typeof value !== 'string' || value.length > 200)) {
    return '<p class="approval-note">此请求未提供可公开的动作说明；请由宿主核对具体动作，不应只凭摘要批准。</p>';
  }
  return `<div class="approval-action-summary"><strong>即将执行的动作</strong><p>${escapeHtml(display.description)}</p>
    ${Object.entries(display.fields).map(([key, value]) => `<div><span>${escapeHtml(key)}</span>：<code>${escapeHtml(value)}</code></div>`).join('')}</div>`;
}

function recoveryExplanation(run) {
  const recovery = run.recovery;
  if (!recovery || typeof recovery.allowed !== 'boolean') return '恢复资格未知，请重新查询服务端记录。';
  if (recovery.allowed) return '当前事实允许尝试显式恢复；执行时仍会重新校验配置、能力、额度和外部依赖。';
  const messages = {
    APPROVAL_PENDING: '尚未批准。批准本身不执行工具，决定后仍需显式恢复。',
    APPROVAL_REJECTED: '本次审批已拒绝，不能通过普通恢复绕过。',
    TOOL_OUTCOME_UNKNOWN: '工具调用或额度事实未结清。先核对外部结果与审计，不要重复执行。',
    MODEL_OUTCOME_UNKNOWN: '模型尝试的额度占用未结清，不能直接重发请求。',
    APPROVAL_VISIT_UNKNOWN: '旧快照缺少可信节点访问身份，请由宿主核对，不能猜测恢复。',
    APPROVAL_REQUEST_MISMATCH: '工具、参数或访问身份与原审批不匹配，请核对配置和记录。',
    CHECKPOINT_MISSING: '没有可信的下一节点检查点，不能自动重建执行现场。',
    INVALID_CHECKPOINT: '检查点无法读取，请先核对持久记录。',
    INVALID_APPROVAL_RECORD: '审批记录无法读取，请先核对持久记录。',
    PENDING_INVOCATION_INVALID: '私有调用快照无法读取，请先核对记录。',
    AUDIT_READ_FAILED: '调用审计不可读，不能把未知事实当成零调用。',
    RUN_ALREADY_ACTIVE: '运行仍在执行，请先查询当前状态。',
    TERMINAL_STATUS: '当前终态不允许恢复。',
  };
  return messages[recovery.detail] || messages[recovery.reason_code] || '当前事实不允许直接恢复，请核对服务端原因码与记录。';
}

function renderRecoveryPanel(task) {
  const old = $('recoveryPanel');
  if (old) old.remove();
  if (!state.live || task.mode !== 'live' || !task.run || task.status === 'running'
      || !['BLOCKED', 'FAILED', 'WAITING_APPROVAL'].includes(task.run.status)) return;
  const panel = document.createElement('section');
  panel.id = 'recoveryPanel';
  panel.className = 'harness-panel';
  const recovery = task.run.recovery;
  panel.innerHTML = `<strong>恢复资格</strong><p>${escapeHtml(recoveryExplanation(task.run))}</p>
    <code>${escapeHtml(recovery?.reason_code || (recovery?.allowed ? '允许尝试' : 'UNKNOWN'))}</code>`;
  $('timeline').after(panel);
}

async function submitApprovalDecision(task, decision) {
  const request = task.run && task.run.pending_approval;
  if (!request || state.running) return;
  const previous = { status: task.status, lifecycle: task.lifecycle };
  state.running = true;
  try {
    await apiFetch(`/api/runs/${task.id}/approval-decision`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: request.request_id, decision }),
    }, 8000);
    if (decision === 'REJECTED') {
      const payload = await apiFetch(`/api/runs/${task.id}`, {}, 8000);
      state.running = false;
      completeLiveTask(task, payload.run, payload.session);
      return;
    }
    // APPROVED: the durable decision alone does not execute anything;
    // recovery needs the explicit resume call.
    task.status = 'running';
    task.lifecycle = [];
    removeApprovalPanel();
    if (state.activeId === task.id) renderTaskView(task);
    persistHistory();
    try {
      const payload = await apiFetch(`/api/runs/${task.id}/resume`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({}),
      }, 120000);
      state.running = false;
      completeLiveTask(task, payload.run, payload.session);
    } catch (error) {
      if (await handleApprovalAuthError(task, error, previous)) return;
      // 恢复请求中断不代表运行终止：回退为观察服务端状态。
      pollLiveTask(task);
    }
  } catch (error) {
    if (await handleApprovalAuthError(task, error, previous)) return;
    state.running = false;
    failTask(task, `审批处理失败：${error.message}`);
  }
}

async function buildReconcilePanel(task) {
  const run = task.run;
  const panelVersion = approvalPanelVersion;
  let pending = null;
  try {
    const payload = await apiFetch(`/api/runs/${task.id}/approval-decision`, {}, 5000);
    pending = payload.pending;
  } catch { return; }
  if (!pending || pending.execution_state !== 'STARTED' || state.activeId !== task.id
      || task.status === 'running' || task.run !== run
      || panelVersion !== approvalPanelVersion) return;
  if (run.status === 'WAITING_APPROVAL') {
    if (pending.request_id !== run.pending_approval?.request_id) return;
  } else if (run.status !== 'BLOCKED' || run.reason_code !== 'TOOL_OUTCOME_UNKNOWN') return;
  const el = document.createElement('div');
  el.id = 'approvalPanel';
  el.className = 'approval-panel';
  el.dataset.requestId = pending.request_id;
  el.innerHTML = `
    <div class="approval-head">${icon('triangle-alert')}<span>外部结果未知，需要人工核对</span></div>
    <div class="approval-meta">
      <div><span>工具</span><strong>${escapeHtml(pending.tool_name)}</strong></div>
      <div><span>请求 ID</span><code>${escapeHtml(pending.request_id)}</code></div>
    </div>
    ${approvalDisplayHtml(run.pending_approval?.display)}
    <p class="approval-note">上次执行在副作用开始后中断，无法确认该工具是否已生效。请先到外部系统核实，再记录核实结果；不会自动重放该调用。</p>
    <div class="reconcile-form">
      <label><input type="radio" name="reconcileOutcome" value="COMPLETED" checked /> 已确认生效</label>
      <label><input type="radio" name="reconcileOutcome" value="FAILED" /> 确认未生效/失败</label>
      <textarea id="reconcileOutput" rows="3" placeholder="已确认生效时填写核实到的输出（JSON 对象，可留空 {}）">{}</textarea>
      <input id="reconcileReason" placeholder="确认失败时填写原因码，如 OPERATOR_VERIFIED_FAILED" />
      <div class="approval-actions">
        <button class="primary-button" id="reconcileSubmit"><span>记录核实结果并继续</span><i data-lucide="check"></i></button>
      </div>
    </div>`;
  el.querySelector('#reconcileSubmit').addEventListener('click', () => submitResolution(task));
  removeApprovalPanel();
  $('timeline').after(el);
  refreshIcons();
}

async function submitResolution(task) {
  const panel = $('approvalPanel');
  if (!panel || state.running) return;
  const previous = { status: task.status, lifecycle: task.lifecycle };
  const requestId = panel.dataset.requestId;
  if (!requestId) { window.alert('无法确定待核对的请求 ID。'); return; }
  const outcome = panel.querySelector('input[name="reconcileOutcome"]:checked').value;
  const payloadBody = { request_id: requestId, outcome };
  if (outcome === 'COMPLETED') {
    let output;
    try { output = JSON.parse(panel.querySelector('#reconcileOutput').value || '{}'); } catch {
      window.alert('核实到的输出不是合法 JSON。');
      return;
    }
    if (!output || typeof output !== 'object' || Array.isArray(output)) {
      window.alert('核实到的输出需要是 JSON 对象。');
      return;
    }
    payloadBody.output = output;
  } else {
    const reason = panel.querySelector('#reconcileReason').value.trim();
    if (!reason) { window.alert('确认失败时需要填写原因码。'); return; }
    payloadBody.reason_code = reason;
  }
  state.running = true;
  try {
    await apiFetch(`/api/runs/${task.id}/approval-resolution`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payloadBody),
    }, 8000);
    task.status = 'running';
    task.lifecycle = [];
    removeApprovalPanel();
    if (state.activeId === task.id) renderTaskView(task);
    persistHistory();
    // 核对结果落盘后仍需显式恢复,运行才会继续。
    const payload = await apiFetch(`/api/runs/${task.id}/resume`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    }, 120000);
    state.running = false;
    completeLiveTask(task, payload.run, payload.session);
  } catch (error) {
    if (await handleApprovalAuthError(task, error, previous)) return;
    state.running = false;
    failTask(task, `记录核实结果失败：${error.message}`);
  }
}

// ---------------------------------------------------------------- live run
function failTask(task, message) {
  task.status = 'failed'; task.completedAt = nowTime(); task.summary = message; state.running = false;
  persistHistory();
  if (state.activeId === task.id) { selectTask(task.id); } else { renderHistory(); }
}

function completeLiveTask(task, run, session = null) {
  task.status = run.status === 'COMPLETED' ? 'completed'
    : run.status === 'WAITING_APPROVAL' ? 'waiting' : 'failed';
  task.completedAt = nowTime();
  task.run = run;
  task.session = session || null;
  task.summary = run.status === 'COMPLETED'
    ? (run.state && run.state.summary) || '运行已完成。'
    : run.status === 'WAITING_APPROVAL'
      ? `等待批准：工具 ${(run.pending_approval && run.pending_approval.tool_name) || '未知'}`
      : `${run.status} · 原因码 ${run.reason_code || '未提供'}`;
  state.running = false;
  persistHistory();
  if (state.activeId === task.id) { selectTask(task.id); } else { renderHistory(); }
}

function acceptStreamGap(task, item) {
  if (item.run_id !== task.id) return;
  task.streamGap = true;
  task.streamText = '';
  task.streamNode = null;
  task.streamCursor = item.cursor || null;
}

function acceptStreamToken(task, item) {
  if (item.run_id !== task.id || typeof item.text !== 'string') return false;
  if (item.cursor && task.streamCursor) {
    const [epoch, sequence] = item.cursor.split(':');
    const [previousEpoch, previousSequence] = task.streamCursor.split(':');
    if (epoch === previousEpoch && BigInt(sequence) <= BigInt(previousSequence)) return false;
  }
  task.streamCursor = item.cursor || null;
  task.streamNode = item.node_id;
  task.streamText = (task.streamText || '') + item.text;
  return true;
}

function watchLiveTask(task) {
  return new Promise((resolve) => {
    let settled = false;
    let watchdog = null;
    const finish = (status) => {
      if (settled) return;
      settled = true;
      window.clearInterval(watchdog);
      try { source.close(); } catch { /* already closed */ }
      resolve(status);
    };
    const cursorQuery = task.streamCursor ? `?cursor=${encodeURIComponent(task.streamCursor)}` : '';
    const source = new EventSource(`/api/runs/${task.id}/events/stream${cursorQuery}`);
    source.addEventListener('lifecycle', (event) => {
      try {
        const item = JSON.parse(event.data);
        if (!task.lifecycle.some((entry) => entry.event_id === item.event_id)) {
          task.lifecycle.push(item);
          if (state.activeId === task.id) renderTaskView(task);
        }
      } catch { /* malformed frame */ }
    });
    source.addEventListener('token', (event) => {
      try {
        const item = JSON.parse(event.data);
        if (!acceptStreamToken(task, item)) return;
        if (state.activeId === task.id) updateStreamOutput(task);
      } catch { /* malformed frame */ }
    });
    source.addEventListener('gap', (event) => {
      try {
        acceptStreamGap(task, JSON.parse(event.data));
        if (state.activeId === task.id) updateStreamOutput(task);
      } catch { /* malformed frame */ }
    });
    source.addEventListener('done', (event) => {
      let status = 'UNKNOWN';
      try { status = JSON.parse(event.data).status; } catch { /* malformed frame */ }
      finish(status);
    });
    source.onerror = () => { /* EventSource 自带重连;断连由看门狗兜底 */ };
    watchdog = window.setInterval(async () => {
      try {
        const status = await apiFetch(`/api/runs/${task.id}/status`, {}, 3000);
        if (TERMINAL_STATUSES.includes(status.status)) finish(status.status);
      } catch (error) {
        if (error.code === 'TIMEOUT' || error.name === 'TypeError') { markOffline(); finish('CONNECTION_LOST'); }
      }
    }, 2000);
  });
}

async function pollLiveTask(task) {
  const status = await watchLiveTask(task);
  if (task.status !== 'running') return;
  if (status === 'CONNECTION_LOST') { failTask(task, '与 Runtime 服务的连接中断。'); return; }
  // 终态在内存先于落盘可见,结果读取遇到短暂 404 时有界重试
  for (let attempt = 0; attempt < 10; attempt++) {
    try {
      const payload = await apiFetch(`/api/runs/${task.id}`, {}, 8000);
      completeLiveTask(task, payload.run, payload.session);
      return;
    } catch (error) {
      if (error.code !== 'UNKNOWN_RUN' || attempt === 9) {
        failTask(task, error.code === 'UNKNOWN_RUN'
          ? '服务端没有这条运行的记录，可能已被重启清除。'
          : `获取运行结果失败：${error.message}`);
        return;
      }
      await sleep(300);
    }
  }
}

async function runLiveTask(task) {
  try {
    const start = await apiFetch('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ agent: task.agent || AGENT_NAME, input: { task: task.title }, wait: false, session_id: currentSessionId() }),
    }, 8000);
    if (state.activeId === task.id) state.activeId = start.run_id;
    task.id = start.run_id;
    task.lifecycle = [];
    persistHistory();
    if (state.activeId === task.id) { $('runId').textContent = task.id; renderTaskView(task); }
    pollLiveTask(task);
  } catch (error) {
    if (error.code === 'TIMEOUT' || error.name === 'TypeError') markOffline();
    failTask(task, `无法提交任务：${error.message}`);
  }
}

// ---------------------------------------------------------------- demo run
function runDemoTask(task) {
  window.setTimeout(() => { if (state.activeId === task.id) selectTask(task.id); }, 80);
  window.setTimeout(() => {
    const failed = /失败|错误|报错|fail/i.test(task.title);
    task.status = failed ? 'failed' : 'completed';
    task.completedAt = nowTime();
    task.summary = failed ? '演示流程模拟了一个可恢复的错误，请重试或调整任务描述。' : `Agent 已完成对“${task.title}”的演示处理，输出已整理为可查看的结果。`;
    state.running = false;
    persistHistory();
    selectTask(task.id);
  }, 1600);
}

// ---------------------------------------------------------------- actions
function submitTask(raw) {
  const text = raw.trim(); if (!text || state.running) return;
  const agentName = ($('agentSelect') && $('agentSelect').value) || AGENT_NAME;
  const task = { id: `pending-${Date.now().toString(36)}`, title: text, status: 'running', time: nowTime(), receivedAt: nowTime(), summary: '', mode: state.live ? 'live' : 'demo', agent: agentName, run: null };
  state.tasks.unshift(task); state.activeId = task.id; state.running = true;
  $('topInput').value = ''; $('bottomInput').value = '';
  selectTask(task.id, true); persistHistory();
  if (task.mode === 'live') runLiveTask(task); else runDemoTask(task);
}

function nestedRunRefs(run) {
  const refs = [];
  const nested = run.state && typeof run.state === 'object' ? run.state : {};
  for (const [key, value] of Object.entries(nested)) {
    if (value && typeof value === 'object' && !Array.isArray(value)
        && typeof value.run_id === 'string' && typeof value.status === 'string'
        && value.state && typeof value.state === 'object') {
      refs.push({ key, run_id: value.run_id, status: value.status, summary: value.state.summary || null });
    }
  }
  return refs;
}

function runInspectorBody(run, session = null) {
  const nodes = run.trace.filter((event) => event.event_type === 'node.started').map((event) => event.node_id).join(' → ') || '（无节点执行）';
  const traceRows = run.trace.map((event) => `<tr><td>${escapeHtml(event.sequence)}</td><td>${escapeHtml(event.event_type)}</td><td>${escapeHtml(event.node_id || '—')}</td><td>${escapeHtml(event.status)}</td><td>${escapeHtml(event.duration_ms != null ? `${event.duration_ms} ms` : '—')}</td></tr>`).join('');
  const lifecycleRows = (run.lifecycle_events || []).map((event) => `<tr><td>${escapeHtml(event.sequence)}</td><td>${escapeHtml(event.event_type)}</td><td>${escapeHtml(event.from_status || '—')} → ${escapeHtml(event.to_status)}</td><td>${escapeHtml(event.reason_code || '—')}</td></tr>`).join('');
  const nestedRefs = nestedRunRefs(run);
  const nestedRows = nestedRefs.map((ref) => `<button class="history-item server-run nested-run" data-nested-run-id="${escapeHtml(ref.run_id)}"><span class="history-icon">${icon(ref.status === 'COMPLETED' ? 'check' : 'triangle-alert')}</span><span class="history-content"><span class="history-name">${escapeHtml(ref.summary || ref.run_id)}</span><span class="history-meta"><span class="mini-status ${ref.status === 'COMPLETED' ? 'completed' : 'failed'}"></span>${escapeHtml(ref.key)} · ${escapeHtml(ref.status)}</span></span></button>`).join('');
  return `<div class="detail-grid">
    <div class="detail-item"><span>运行 ID</span><strong>${escapeHtml(run.run_id)}</strong></div>
    <div class="detail-item"><span>状态</span><strong>${escapeHtml(run.status)}</strong></div>
    <div class="detail-item"><span>原因码</span><strong>${escapeHtml(run.reason_code || '—')}</strong></div>
    <div class="detail-item"><span>停止于</span><strong>${escapeHtml(run.stopped_at_node || '—')}</strong></div>
    <div class="detail-item"><span>模拟标记</span><strong>${run.simulated ? 'simulated' : '真实执行'}</strong></div>
    <div class="detail-item"><span>Trace 事件</span><strong>${run.trace.length} 条</strong></div>
  </div>
  <div class="detail-output"><strong>恢复资格</strong><p>${escapeHtml(recoveryExplanation(run))}</p><code>${escapeHtml(run.recovery?.reason_code || (run.recovery?.allowed ? "允许尝试" : "UNKNOWN"))}</code></div>
  <section id="detailHarnessPanel" class="harness-panel"></section>
  <div class="detail-output"><strong>执行路径</strong><br />${escapeHtml(nodes)}</div>
  ${nestedRefs.length ? `<div class="detail-output"><strong>嵌套运行</strong>${nestedRows}</div>` : ''}
  ${traceRows ? `<table class="detail-table"><thead><tr><th>#</th><th>事件</th><th>节点</th><th>状态</th><th>耗时</th></tr></thead><tbody>${traceRows}</tbody></table>` : ''}
  ${lifecycleRows ? `<table class="detail-table"><thead><tr><th>#</th><th>生命周期</th><th>状态迁移</th><th>原因码</th></tr></thead><tbody>${lifecycleRows}</tbody></table>` : ''}
  ${session ? `<div class="detail-output"><strong>会话 ${escapeHtml(session.id)} 的记忆</strong><br /><pre class="detail-json">${escapeHtml(JSON.stringify(session.memory || {}, null, 2))}</pre></div>` : ''}
  <pre class="detail-json">${escapeHtml(JSON.stringify(run.state, null, 2))}</pre>`;
}

function bindInspectorHandlers(container) {
  container.querySelectorAll('[data-nested-run-id]').forEach((item) => item.addEventListener('click', () => openServerRunDetail(item.dataset.nestedRunId)));
}

let inspectorVersion = 0;

function openDetail() {
  inspectorVersion += 1;
  const task = state.tasks.find((item) => item.id === state.activeId); if (!task) return;
  let body;
  if (task.mode === 'live' && task.run) {
    body = runInspectorBody(task.run, task.session);
  } else {
    body = `<div class="detail-grid">
      <div class="detail-item"><span>运行 ID</span><strong>${escapeHtml(task.id)}</strong></div>
      <div class="detail-item"><span>状态</span><strong>${task.status === 'failed' ? 'FAILED' : 'COMPLETED'}</strong></div>
      <div class="detail-item"><span>接收时间</span><strong>${escapeHtml(task.receivedAt)}</strong></div>
      <div class="detail-item"><span>执行模式</span><strong>${task.mode === 'live' ? 'RUNTIME_API' : 'LOCAL_DEMO'}</strong></div>
    </div>
    <div class="detail-output"><strong>结果摘要</strong><br />${escapeHtml(task.summary || '—')}</div>`;
  }
  $('modalTitle').textContent = task.title;
  $('modalBody').innerHTML = body;
  bindInspectorHandlers($('modalBody'));
  if (task.mode === 'live' && task.run) {
    const panel = $('detailHarnessPanel');
    loadHarnessEvidence(task.id, panel, () => $('detailHarnessPanel') === panel);
  }
  $('detailModal').showModal(); refreshIcons();
}

const serverRunsView = { offset: 0, total: null, items: [] };
let logsViewVersion = 0;

function serverRunRow(item) {
  const ok = item.status === 'COMPLETED';
  const meta = [item.agent || '未知 Agent', item.status + (item.reason_code ? ` · ${item.reason_code}` : ''), clockTime(item.started_at)]
    .filter(Boolean).join(' · ');
  return `<button class="history-item server-run" data-run-id="${escapeHtml(item.run_id)}"><span class="history-icon">${icon(ok ? 'check' : 'triangle-alert')}</span><span class="history-content"><span class="history-name">${escapeHtml(item.summary || item.run_id)}</span><span class="history-meta"><span class="mini-status ${ok ? 'completed' : 'failed'}"></span>${escapeHtml(meta)}</span></span></button>`;
}

async function renderLogsView(reset = true) {
  const version = ++logsViewVersion;
  removeApprovalPanel();
  ['recoveryPanel', 'harnessPanel', 'streamOutput', 'chatView'].forEach(id => {
    const panel = $(id); if (panel) panel.remove();
  });
  $('executionTitle').textContent = '运行日志';
  $('runId').textContent = '服务端运行记录';
  $('statePill').className = 'state-pill neutral';
  $('statePill').textContent = '记录查询';
  const current = () => state.view === 'logs' && version === logsViewVersion;
  if (reset) { serverRunsView.offset = 0; serverRunsView.total = null; serverRunsView.items = []; }
  const rows = [];
  if (state.live) {
    try {
      const payload = await apiFetch(`/api/runs?limit=10&offset=${serverRunsView.offset}`, {}, 5000);
      if (!current()) return;
      serverRunsView.items = serverRunsView.items.concat(payload.runs);
      serverRunsView.total = payload.total;
      serverRunsView.offset += payload.runs.length;
      rows.push(`<div class="history-heading server-heading"><h2>服务端运行记录</h2><span>${payload.total} 条 · 活跃 ${payload.active.length}</span></div>`);
      if (serverRunsView.items.length) {
        rows.push(serverRunsView.items.map(serverRunRow).join(''));
        if (serverRunsView.items.length < serverRunsView.total) {
          rows.push(`<div class="load-more-wrap"><button class="text-button" id="loadMoreRuns">加载更多(还有 ${serverRunsView.total - serverRunsView.items.length} 条)</button></div>`);
        }
      } else {
        rows.push('<div class="timeline-empty">服务端暂无持久化运行</div>');
      }
    } catch { rows.push('<div class="timeline-empty">无法读取服务端运行记录</div>'); }
  }
  if (!current()) return;
  rows.push('<div class="history-heading server-heading"><h2>本页任务</h2></div>');
  rows.push(state.tasks.map((task) => `<div class="timeline-step ${task.status === 'failed' ? 'failed' : 'done'}"><span class="step-marker">${icon(task.status === 'failed' ? 'triangle-alert' : 'check')}</span><span class="step-rail"></span><div><div class="step-name">${escapeHtml(task.title)}</div><div class="step-desc">${escapeHtml(`${task.mode === 'live' ? 'Runtime API' : '本地演示'} · ${statusLabel(task)}`)}</div></div><span class="step-time">${escapeHtml(task.time)}</span></div>`).join('') || '<div class="timeline-empty">暂无本页任务</div>');
  $('timeline').innerHTML = `<div class="timeline-list">${rows.join('')}</div>`;
  $('timeline').querySelectorAll('[data-run-id]').forEach((item) => item.addEventListener('click', () => openServerRunDetail(item.dataset.runId)));
  const more = $('loadMoreRuns');
  if (more) more.addEventListener('click', () => renderLogsView(false));
  $('resultPanel').classList.add('hidden');
  updateCancelButton(null);
  updateResumeButton(null);
  refreshIcons();
}

async function openServerRunDetail(runId) {
  const version = ++inspectorVersion;
  let record;
  try {
    const payload = await apiFetch(`/api/runs/${runId}`, { cache: 'no-store' }, 6000);
    record = { run: payload.run, session: payload.session || null };
  } catch { return; }
  if (version !== inspectorVersion) return;
  $('modalTitle').textContent = `服务端运行 ${runId}`;
  $('modalBody').innerHTML = runInspectorBody(record.run, record.session);
  bindInspectorHandlers($('modalBody'));
  const panel = $('detailHarnessPanel');
  loadHarnessEvidence(runId, panel, () => $('detailHarnessPanel') === panel);
  $('detailModal').showModal(); refreshIcons();
}

async function renderAgentTopology() {
  const container = $('agentTopology');
  if (!container) return;
  if (!state.live) { container.innerHTML = '<div class="settings-note"><span>离线模式下无法读取 Agent 拓扑。</span></div>'; return; }
  try {
    const payload = await apiFetch('/api/agents', {}, 3000);
    const count = $('topologyCount');
    if (count) count.textContent = `${payload.agents.length} 个已注册`;
    container.innerHTML = payload.agents.map((agent) => {
      const graph = agent.graph || { nodes: [], edges: [], entry: '?' };
      const flow = `${escapeHtml(graph.entry)} → ${escapeHtml(graph.nodes.filter((n) => n !== graph.entry).join(' → ') || '—')}`;
      const edges = graph.edges.map((edge) => `${escapeHtml(edge.source)}→${escapeHtml(edge.target)}${edge.when ? ` [${escapeHtml(edge.when)}]` : ''}`).join('，');
      return `<div class="topology-card"><strong>${escapeHtml(agent.name)}</strong><span>${escapeHtml(agent.description || agent.instructions || '')}</span><code>${flow}</code><span class="topology-edges">${edges || '无边'}</span></div>`;
    }).join('');
  } catch {
    container.innerHTML = '<div class="settings-note"><span>读取 Agent 拓扑失败。</span></div>';
  }
}

function resetWorkspace() {
  state.tasks.length = 0; state.activeId = null; state.running = false;
  persistHistory(); renderHistory();
  $('runId').textContent = '等待新任务'; $('executionTitle').textContent = '准备好开始';
  $('statePill').className = 'state-pill neutral'; $('statePill').textContent = '待机';
  $('timeline').innerHTML = `<div class="timeline-empty">${icon('route')}<span>提交任务后，这里会显示执行过程</span></div>`;
  $('resultPanel').classList.add('hidden');
  updateCancelButton(null);
  updateResumeButton(null);
  refreshIcons();
}

function switchView(view) {
  state.view = view;
  document.querySelectorAll('.nav-item').forEach((item) => item.classList.toggle('active', item.dataset.view === view));
  if (view === 'logs') { renderLogsView(); }
  else if (view === 'settings') { $('settingsModal').showModal(); renderAgentTopology(); switchView('tasks'); }
  else if (state.activeId) selectTask(state.activeId);
}

document.addEventListener('DOMContentLoaded', () => {
  refreshIcons();
  loadHistory();
  renderHistory();
  detectMode();
  state.tasks.filter((task) => task.status === 'running' && task.mode === 'live').forEach((task) => pollLiveTask(task));

  $('topComposer').addEventListener('submit', (event) => { event.preventDefault(); submitTask($('topInput').value); });
  $('bottomComposer').addEventListener('submit', (event) => { event.preventDefault(); submitTask($('bottomInput').value); });
  $('topInput').addEventListener('keydown', (event) => { if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); submitTask(event.target.value); } });
  $('bottomInput').addEventListener('keydown', (event) => { if (event.key === 'Enter') { event.preventDefault(); submitTask(event.target.value); } });
  $('detailButton').addEventListener('click', openDetail);
  $('settingsButton').addEventListener('click', () => $('settingsModal').showModal());
  $('clearSessionButton').addEventListener('click', clearSessionMemory);
  $('approvalTokenApply').addEventListener('click', applyApprovalToken);
  $('approvalTokenClear').addEventListener('click', () => { $('approvalTokenInput').value = ''; approvalAuth.clear(); });
  $('settingsModal').addEventListener('close', () => { $('approvalTokenInput').value = ''; });
  // Back/forward cache can retain a document; leaving the page also forgets credentials.
  window.addEventListener('pagehide', () => { $('approvalTokenInput').value = ''; approvalAuth.clear(); });
  approvalAuth.render();
  $('resumeConfirm').addEventListener('click', confirmResume);
  $('resumeCancel').addEventListener('click', () => { resumeContext = null; $('resumeModal').close(); });
  $('clearHistory').addEventListener('click', resetWorkspace);
  document.querySelectorAll('.nav-item').forEach((item) => item.addEventListener('click', () => switchView(item.dataset.view)));
  document.querySelectorAll('[data-close]').forEach((button) => button.addEventListener('click', () => $(button.dataset.close).close()));
  document.addEventListener('keydown', (event) => { if ((event.metaKey || event.ctrlKey) && ['1','2','3'].includes(event.key)) { event.preventDefault(); switchView(['tasks','logs','settings'][Number(event.key) - 1]); } });
});
