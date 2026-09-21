(() => {
  const $ = id => document.getElementById(id);
  const THEMES = {
    dark: '#08111f',
    light: '#dfe9f3',
    ember: '#140c0a',
    violet: '#0d0b18'
  };
  const WELCOME_HTML = '<div class="welcome"><span class="eyebrow">DEVOPS CONSOLE</span><h1>What are we investigating?</h1><div class="quick-actions"><button type="button" data-prompt="Check infrastructure health">Check infrastructure</button><button type="button" data-prompt="Show running containers">Show containers</button><button type="button" data-prompt="Check CPU and memory usage">Check CPU / memory</button><button type="button" data-prompt="Show recent errors">Recent errors</button></div></div>';
  let conversationId;
  const messages = $('messages');
  const provider = $('provider');
  const appShell = document.querySelector('.app-shell');
  const escapeHtml = value => value.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

  function applyTheme(theme) {
    const next = THEMES[theme] ? theme : 'dark';
    document.documentElement.dataset.theme = next;
    const color = THEMES[next];
    const meta = $('theme-color');
    if (meta) meta.setAttribute('content', color);
    try { localStorage.setItem('jarvis.theme', next); } catch (err) {}
    document.querySelectorAll('[data-theme-value]').forEach(button => {
      button.classList.toggle('active', button.dataset.themeValue === next);
    });
    if ($('setting-theme')) $('setting-theme').value = next;
    return next;
  }

  function hideBoot() {
    const boot = $('boot-screen');
    if (boot) boot.hidden = true;
    document.documentElement.removeAttribute('data-boot');
  }

  function setSessionHint(on) {
    try {
      if (on) localStorage.setItem('jarvis.sessionHint', '1');
      else localStorage.removeItem('jarvis.sessionHint');
    } catch (err) {}
  }

  function renderMarkdown(value) {
    let html = escapeHtml(value || '');
    html = html.replace(/```([\s\S]*?)```/g, '<pre><code>$1</code></pre>');
    html = html.replace(/^### (.*)$/gm, '<h4>$1</h4>').replace(/^## (.*)$/gm, '<h3>$1</h3>').replace(/^# (.*)$/gm, '<h2>$1</h2>');
    html = html.replace(/^\|(.+)\|\n\|[-: |]+\|\n((?:\|.*\|\n?)+)/gm, (_, head, rows) => {
      const labels = head.split('|').filter(Boolean).map(x => x.trim());
      return `<div class="report-cards">${rows.trim().split('\n').map(row => { const cells = row.split('|').filter(Boolean).map(x => x.trim()); return `<article class="report-card"><header><strong>${cells[0] || ''}</strong><span class="status-badge">${cells[1] || ''}</span></header><div class="report-detail"><span>${labels[2] || 'Details'}</span>${cells.slice(2).join(' ')}</div></article>`; }).join('')}</div>`;
    });
    html = html.replace(/&lt;(br|ul|\/ul|li|\/li)&gt;/g, '<$1>');
    html = html.replace(/^[-*] (.*)$/gm, '<li>$1</li>').replace(/(?:<li>.*<\/li>\n?)+/g, x => `<ul>${x}</ul>`);
    html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>').replace(/`([^`]+)`/g, '<code>$1</code>');
    return html.replace(/\n{2,}/g, '<br><br>').replace(/\n/g, '<br>');
  }

  function addMessage(role, content) {
    const welcome = messages.querySelector('.welcome');
    if (welcome && role === 'user') welcome.remove();
    const el = document.createElement('article'); el.className = `message ${role}`;
    el.innerHTML = `<div class="message-avatar" aria-hidden="true">${role === 'user' ? '◉' : '<span class="jarvis-orb">✦</span>'}</div><div class="message-content"><div class="message-label">${role === 'user' ? 'You' : 'JARVIS'}</div></div>`;
    const body = document.createElement('div'); body.className = 'message-body'; body.innerHTML = role === 'assistant' ? renderMarkdown(content) : escapeHtml(content); el.querySelector('.message-content').append(body);
    if (role === 'assistant') { const actions = document.createElement('div'); actions.className = 'message-actions'; actions.innerHTML = '<button type="button" data-copy aria-label="Copy response" title="Copy response">⧉</button>'; el.querySelector('.message-content').append(actions); }
    messages.append(el); messages.scrollTop = messages.scrollHeight; return body;
  }

  async function init() {
    const savedConversation = localStorage.getItem('jarvis.conversationId');
    const conversationRequest = savedConversation
      ? fetch(`/api/conversations/${savedConversation}`).then(r => r.ok ? {id: savedConversation} : null).catch(() => null)
      : Promise.resolve(null);
    const [existing, providers] = await Promise.all([conversationRequest, fetch('/api/providers')]);
    if (existing) conversationId = existing.id;
    else {
      const conv = await (await fetch('/api/conversations', {method:'POST'})).json();
      conversationId = conv.id;
      localStorage.setItem('jarvis.conversationId', conversationId);
    }
    const data = await providers.json();
    provider.replaceChildren();
    data.providers.forEach(p => { const option = new Option(`${p.name}${p.available ? '' : ' · unavailable'}`, p.id); option.disabled = !p.available; provider.add(option); });
    if (data.active_provider) provider.value = data.active_provider.split('(')[0].trim().toLowerCase();
  }

  function showApp() {
    setSessionHint(true);
    $('login-screen').hidden = true;
    appShell.hidden = false;
    hideBoot();
  }

  function showLoginScreen() {
    setSessionHint(false);
    appShell.hidden = true;
    $('register-form').hidden = true;
    $('login-form').hidden = false;
    $('login-screen').hidden = false;
    hideBoot();
  }

  async function loadStatus() {
    $('stat-agent').textContent = 'Checking…';
    try {
      const [health, infra] = await Promise.all([fetch('/api/health'), fetch('/api/infra/status')]);
      const h = await health.json(); const i = await infra.json();
      $('stat-agent').textContent = h.status === 'ok' ? 'Online' : 'Degraded';
      $('stat-infra').textContent = i.status === 'ok' ? 'Healthy' : 'Needs attention';
      $('stat-time').textContent = new Date().toLocaleTimeString([], {hour:'2-digit', minute:'2-digit'});
      $('status-detail').textContent = i.cached ? 'Using the backend health-check cache.' : 'Fresh health check completed.';
    } catch (err) { $('stat-agent').textContent = 'Offline'; $('stat-infra').textContent = 'Unavailable'; $('status-detail').textContent = 'Could not reach the backend.'; }
  }

  function showLogin() { $('register-form').hidden = true; $('login-form').hidden = false; $('register-error').textContent = ''; }
  $('login-form').onsubmit = async e => { e.preventDefault(); const error = $('login-error'); const form = $('login-form'); error.textContent = ''; form.classList.add('is-loading'); const response = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:$('login-username').value.trim(), password:$('login-password').value})}); const data = await response.json().catch(() => ({})); form.classList.remove('is-loading'); if (!response.ok) { error.textContent = data.detail || 'Invalid username or password.'; form.classList.add('has-error'); setTimeout(() => form.classList.remove('has-error'), 450); return; } showApp(); init().catch(err => addMessage('assistant', `Unable to connect: ${err.message}`)); };
  $('register-toggle').onclick = () => { $('login-form').hidden = true; $('register-form').hidden = false; $('register-first-name').focus(); };
  $('register-back').onclick = showLogin;
  $('register-form').onsubmit = async e => { e.preventDefault(); const form = $('register-form'); const error = $('register-error'); error.textContent = ''; if ($('register-password').value !== $('register-confirm-password').value) { error.textContent = 'Passwords do not match.'; return; } form.classList.add('is-loading'); const response = await fetch('/api/auth/register', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({first_name:$('register-first-name').value.trim(), last_name:$('register-last-name').value.trim(), email:$('register-email').value.trim(), username:$('register-username').value.trim(), password:$('register-password').value, confirm_password:$('register-confirm-password').value})}); const data = await response.json().catch(() => ({})); form.classList.remove('is-loading'); if (!response.ok) { error.textContent = data.detail || 'Account creation failed.'; return; } showApp(); init().catch(err => addMessage('assistant', `Unable to connect: ${err.message}`)); };

  async function clearChat() {
    if (!conversationId || !confirm('Clear this chat?')) return;
    await fetch(`/api/conversations/${conversationId}`, {method:'DELETE'});
    conversationId = (await (await fetch('/api/conversations', {method:'POST'})).json()).id;
    localStorage.setItem('jarvis.conversationId', conversationId);
    messages.innerHTML = WELCOME_HTML;
    $('menu').hidden = true;
  }

  provider.onchange = () => fetch('/api/providers/switch', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({provider:provider.value})});

  async function sendMessage(text) {
    addMessage('user', text); if (/^\s*(give me|open) terminal\s*$/i.test(text)) openTerminal();
    const target = addMessage('assistant', '');
    target.innerHTML = '<div class="assistant-status"><span class="typing"><i></i><i></i><i></i></span> <span class="status-text">Thinking…</span></div>';
    let streamed = '';
    try {
      const response = await fetch(`/api/conversations/${conversationId}/chat/stream`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json', 'Accept': 'text/event-stream'},
        body: JSON.stringify({message: text, provider: provider.value})
      });
      if (!response.ok) { target.textContent = `Request failed (${response.status})`; return; }
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let buffer = '';
      while (true) {
        const {value, done} = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, {stream: true});
        const chunks = buffer.split('\n\n');
        buffer = chunks.pop();
        for (const chunk of chunks) {
          const line = chunk.split('\n').find(x => x.startsWith('data: '));
          if (!line) continue;
          try {
            const event = JSON.parse(line.slice(6));
            const statusEl = target.querySelector('.status-text');
            if (event.type === 'status' && statusEl) {
              statusEl.textContent = event.content || 'Thinking…';
            } else if (event.type === 'tool_call' && statusEl) {
              statusEl.textContent = `Checking ${(event.name || '').replace(/_/g, ' ')}…`;
            } else if (event.type === 'tool_result' && statusEl) {
              statusEl.textContent = `Completed ${(event.name || '').replace(/_/g, ' ')}…`;
            } else if (event.type === 'text' || event.type === 'delta') {
              streamed = event.delta ? streamed + event.delta : (event.content || streamed);
              target.innerHTML = renderMarkdown(streamed);
            } else if (event.type === 'error') {
              target.textContent = event.content;
            }
          } catch (pe) {}
          keepChatAtBottom();
        }
      }
      // Some servers/proxies close immediately after the final event without
      // adding a second blank line. Process that buffered event as well.
      if (buffer.trim()) {
        const line = buffer.split('\n').find(x => x.startsWith('data: '));
        if (line) {
          try {
            const event = JSON.parse(line.slice(6));
            if (event.type === 'text' || event.type === 'delta') {
              streamed = event.delta ? streamed + event.delta : (event.content || streamed);
              target.innerHTML = renderMarkdown(streamed);
            } else if (event.type === 'error') {
              target.textContent = event.content;
            }
          } catch (pe) {}
        }
      }
    } catch (err) {
      target.textContent = `Connection error: ${err.message}`;
    }
  }

  function openTerminal() { window.location.assign('/terminal'); }
  const messageInput = $('message');
  function autoResizeInput() {
    messageInput.style.height = 'auto';
    messageInput.style.height = Math.min(messageInput.scrollHeight, 120) + 'px';
  }
  messageInput.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      if (messageInput.value.trim()) $('chat-form').requestSubmit();
    }
  });
  $('chat-form').onsubmit = e => {
    e.preventDefault();
    const text = messageInput.value.trim();
    if (!text) return;
    messageInput.value = '';
    messageInput.style.height = 'auto';
    sendMessage(text).catch(err => addMessage('assistant', `Error: ${err.message}`));
  };
  $('new-chat').onclick = () => clearChat().catch(err => addMessage('assistant', `Unable to start a new chat: ${err.message}`));
  $('menu-button').onclick = () => { const menu = $('menu'); menu.hidden = !menu.hidden; $('menu-button').setAttribute('aria-expanded', String(!menu.hidden)); };
  $('clear-chat').onclick = () => clearChat().catch(err => addMessage('assistant', `Unable to clear chat: ${err.message}`));
  $('menu-terminal').onclick = () => { $('menu').hidden = true; openTerminal(); };
  $('logout').onclick = async () => { setSessionHint(false); await fetch('/api/auth/logout', {method:'POST'}); location.reload(); };
  function showPanel(name) {
    document.querySelector('.chat-panel').hidden = name !== 'chat';
    $('status-panel').hidden = name !== 'status';
    $('settings-panel').hidden = name !== 'settings';
  }
  $('menu-status').onclick = () => { $('menu').hidden = true; showPanel('status'); loadStatus(); };
  $('menu-settings').onclick = async () => { $('menu').hidden = true; showPanel('settings'); await loadSettings(); };
  $('status-refresh').onclick = loadStatus;
  $('back-chat').onclick = () => showPanel('chat');
  $('settings-back').onclick = () => showPanel('chat');
  document.querySelectorAll('[data-settings-section]').forEach(button => button.onclick = () => { document.querySelectorAll('.settings-nav-item').forEach(item => item.classList.toggle('active', item === button)); document.querySelectorAll('[data-settings-section-content]').forEach(section => section.hidden = section.dataset.settingsSectionContent !== button.dataset.settingsSection); if (button.dataset.settingsSection === 'infrastructure') loadInfrastructureTargets(); });
  $('research-form').onsubmit = async event => { event.preventDefault(); const status = $('research-status'); const query = $('research-query').value.trim(); status.textContent = 'Starting research…'; $('research-results').textContent = ''; const response = await fetch('/api/research/learn/stream', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({tool:query, version:$('research-version').value.trim(), refresh:$('research-refresh').checked})}); if (!response.ok) { const data = await response.json().catch(() => ({})); status.textContent = data.detail || 'Research failed'; return; } const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; while (true) { const {value, done} = await reader.read(); if (done) break; buffer += decoder.decode(value, {stream:true}); const chunks = buffer.split('\n\n'); buffer = chunks.pop(); chunks.forEach(chunk => { const line = chunk.split('\n').find(item => item.startsWith('data: ')); if (!line) return; const eventData = JSON.parse(line.slice(6)); if (eventData.stage === 'started') status.textContent = `Preparing research for ${eventData.tool}…`; if (eventData.stage === 'searching_official_docs') status.textContent = 'Searching official documentation…'; if (eventData.stage === 'completed') { const data = eventData.result; status.textContent = data.status === 'already_known' ? 'This skill is already cached.' : 'Research complete — skill saved.'; $('research-results').innerHTML = `<strong>${escapeHtml(data.skill || query)}</strong><p>Sources: ${(data.sources || []).length}. Local version and project references were recorded. No execution performed.</p>`; } if (eventData.stage === 'failed') status.textContent = eventData.result?.error || 'Research failed'; }); } };
  // ── Tools & Automation (Tool Factory) ──
  async function loadTools() { const status = $('tools-status'); const list = $('tools-list'); status.textContent = ''; try { const response = await fetch('/api/tools'); if (!response.ok) { const data = await response.json().catch(() => ({})); list.innerHTML = `<p class="muted">${escapeHtml(data.detail || 'Tools are unavailable.')}</p>`; return; } const data = await response.json(); const tools = data.tools || []; if (!tools.length) { list.innerHTML = '<p class="muted">No tools registered yet.</p>'; return; } list.replaceChildren(...tools.map(tool => { const card = document.createElement('article'); card.className = 'tool-card'; const statusClass = tool.enabled ? 'ok' : (tool.status === 'rejected' ? 'bad' : 'idle'); card.innerHTML = `<div class="tool-head"><strong>${escapeHtml(tool.display_name || tool.name)}</strong><span class="status-badge ${statusClass}">${escapeHtml(tool.status)}</span></div><small>${escapeHtml(tool.description || '')}</small><div class="tool-meta"><span>v${escapeHtml(tool.version)}</span><span>${escapeHtml(tool.execution_mode)}</span><span>${escapeHtml(tool.required_permission)}</span><span>${tool.read_only ? 'read-only' : 'MUTATING'}</span></div><div class="tool-actions"><button type="button" data-tool-detail="${escapeHtml(tool.name)}">Details</button>${tool.enabled ? `<button type="button" data-tool-deactivate="${escapeHtml(tool.name)}">Deactivate</button>` : `<button type="button" data-tool-activate="${escapeHtml(tool.name)}">Activate</button>`}</div>`; return card; })); } catch (err) { list.innerHTML = '<p class="muted">Could not reach the tools API.</p>'; } }
  $('tools-refresh').onclick = loadTools;
  $('research-progress').onclick = async () => { const detail = $('tools-detail'); detail.hidden = false; detail.textContent = 'Loading research progress…'; const response = await fetch('/api/research/progress'); const data = await response.json(); detail.replaceChildren(...(data.skills || []).map(skill => { const row = document.createElement('div'); row.className = 'tool-card'; row.innerHTML = `<strong>${escapeHtml(skill.skill)}</strong><small>${escapeHtml(String(skill.sources))} sources · ${escapeHtml(skill.source_comparison)} · researched ${escapeHtml((skill.researched_at || '').slice(0, 10))}</small>`; return row; })); if (!(data.skills || []).length) detail.textContent = 'No research profiles yet.'; };
  $('research-disagreements').onclick = async () => { const detail = $('tools-detail'); detail.hidden = false; detail.textContent = 'Loading disagreements…'; const response = await fetch('/api/research/disagreements'); const data = await response.json(); if (!(data.disagreements || []).length) { detail.textContent = 'No source disagreements recorded.'; return; } detail.replaceChildren(...data.disagreements.map(item => { const row = document.createElement('div'); row.className = 'tool-card'; row.innerHTML = `<strong>${escapeHtml(item.skill)}</strong><span class="status-badge bad">${escapeHtml(item.status)}</span><small>${escapeHtml(item.recommendation)}</small>`; return row; })); };
  $('project-scan').onclick = async () => { const detail = $('tools-detail'); detail.hidden = false; detail.textContent = 'Scanning project context…'; const response = await fetch('/api/project/scan', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({project_path:'.', mode:'overview'})}); const data = await response.json().catch(() => ({})); if (!response.ok) { detail.textContent = data.detail || 'Scan failed.'; return; } const techs = (data.technologies || []).map(item => item.name).join(', ') || 'none detected'; detail.innerHTML = `<div class="tool-card"><strong>Project: ${escapeHtml((data.project || {}).name || '')}</strong><small>${escapeHtml(String(data.file_count || 0))} files · technologies: ${escapeHtml(techs)}</small></div>`; };
  $('tools-list').onclick = async event => { const detail = $('tools-detail'); const detailButton = event.target.closest('[data-tool-detail]'); const activateButton = event.target.closest('[data-tool-activate]'); const deactivateButton = event.target.closest('[data-tool-deactivate]'); if (detailButton) { const name = detailButton.dataset.toolDetail; detail.hidden = false; detail.textContent = 'Loading details…'; const [toolResponse, versionsResponse, auditResponse] = await Promise.all([fetch(`/api/tools/${encodeURIComponent(name)}`), fetch(`/api/tools/${encodeURIComponent(name)}/versions`), fetch(`/api/tools/${encodeURIComponent(name)}/audit`)]); const toolData = await toolResponse.json().catch(() => ({})); const versionsData = await versionsResponse.json().catch(() => ({})); const auditData = await auditResponse.json().catch(() => ({})); const tool = toolData.tool || {}; const versions = versionsData.versions || []; const audits = auditData.audits || []; detail.replaceChildren(...[]); const wrap = document.createElement('div'); wrap.className = 'tool-card'; wrap.innerHTML = `<strong>${escapeHtml(tool.name || name)}</strong><small>${escapeHtml(tool.description || '')}</small><div class="tool-meta"><span>status ${escapeHtml(tool.status || '?')}</span><span>active ${escapeHtml(toolData.active_version || '—')}</span><span>timeout ${escapeHtml(String(tool.timeout_seconds ?? '?'))}s</span><span>max ${escapeHtml(String(tool.max_output_bytes ?? '?'))}B</span></div>`; const versionsBlock = document.createElement('div'); versionsBlock.className = 'tool-versions'; versionsBlock.innerHTML = `<em>Versions</em>` + (versions.length ? versions.map(version => `<div class="tool-version-row"><span>v${escapeHtml(version.version)}${version.active ? ' · ACTIVE' : ''}</span><span>${escapeHtml(String(version.created_at || '').slice(0, 19))}</span><span>${escapeHtml(version.created_by || '')}</span></div>`).join('') : '<small>No versions recorded.</small>'); const auditBlock = document.createElement('div'); auditBlock.className = 'tool-versions'; auditBlock.innerHTML = `<em>Recent executions</em>` + (audits.length ? audits.slice(0, 10).map(audit => `<div class="tool-version-row"><span class="${audit.success ? 'ok' : 'bad'}">${audit.success ? '✓' : '✗'}</span><span>${escapeHtml(String(audit.timestamp || '').slice(0, 19))}</span><span>${escapeHtml(String(audit.duration_ms))}ms</span><span>${escapeHtml(audit.execution_mode || '')}</span><span>${escapeHtml(audit.error_code || '')}</span></div>`).join('') : '<small>No executions recorded.</small>'); detail.replaceChildren(wrap, versionsBlock, auditBlock); } if (activateButton) { const name = activateButton.dataset.toolActivate; const response = await fetch(`/api/tools/${encodeURIComponent(name)}/activate`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({})}); const data = await response.json().catch(() => ({})); $('tools-status').textContent = response.ok ? `Activated ${name}.` : (data.detail || 'Activation failed.'); loadTools(); } if (deactivateButton) { const name = deactivateButton.dataset.toolDeactivate; const response = await fetch(`/api/tools/${encodeURIComponent(name)}/deactivate`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({})}); const data = await response.json().catch(() => ({})); $('tools-status').textContent = response.ok ? `Deactivated ${name}.` : (data.detail || 'Deactivation failed.'); loadTools(); } };
  document.querySelectorAll('[data-settings-section]').forEach(button => { if (button.dataset.settingsSection === 'tools') button.addEventListener('click', loadTools); });
  $('target-kind').onchange = () => { const aws = $('target-kind').value === 'aws'; $('target-host-label').hidden = aws; $('target-username').closest('label').hidden = aws; $('target-region-label').hidden = !aws; $('target-endpoint-label').hidden = !aws; };
  async function loadInfrastructureTargets() { const response = await fetch('/api/settings/infrastructure'); if (!response.ok) return; const data = await response.json(); $('infrastructure-targets').replaceChildren(...(data.targets || []).map(target => { const card = document.createElement('article'); card.className = 'target-card'; card.innerHTML = `<div><strong>${escapeHtml(target.name)}</strong><span>${escapeHtml(target.kind.toUpperCase())}</span><small>${escapeHtml(target.host || target.region || target.endpoint || 'No endpoint')}</small></div><button type="button" data-delete-target="${escapeHtml(target.id)}" aria-label="Remove ${escapeHtml(target.name)}">×</button>`; return card; })); }
  $('infrastructure-form').onsubmit = async event => { event.preventDefault(); const status = $('target-status'); const payload = {name:$('target-name').value.trim(), kind:$('target-kind').value, host:$('target-host').value.trim(), port:$('target-port').value ? Number($('target-port').value) : null, username:$('target-username').value.trim(), region:$('target-region').value.trim(), endpoint:$('target-endpoint').value.trim(), notes:$('target-notes').value.trim()}; const response = await fetch('/api/settings/infrastructure', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}); const data = await response.json().catch(() => ({})); if (!response.ok) { status.textContent = data.detail || 'Could not save target'; return; } event.target.reset(); status.textContent = 'Infrastructure target added'; loadInfrastructureTargets(); };
  $('infrastructure-targets').onclick = async event => { const button = event.target.closest('[data-delete-target]'); if (!button) return; await fetch(`/api/settings/infrastructure/${button.dataset.deleteTarget}`, {method:'DELETE'}); loadInfrastructureTargets(); };
  let settingsProviders = [];
  async function loadSettings() { const [user, org, providers] = await Promise.all([fetch('/api/settings/user'), fetch('/api/settings/organization'), fetch('/api/settings/providers')]); const userData = await user.json(); const orgData = await org.json(); const providerData = await providers.json(); settingsProviders = providerData.providers || []; applyTheme(userData.values.theme || localStorage.getItem('jarvis.theme') || 'dark'); $('setting-timezone').value = orgData.values.timezone || ''; const preferences = providerData.preferences || {}; $('setting-fallback').checked = preferences.fallback_enabled !== false; renderProviderSettings(preferences); loadInfrastructureTargets(); }
  function renderProviderSettings(preferences = {}) { const selected = preferences.primary_provider || provider.value || settingsProviders.find(p => p.configured)?.id || settingsProviders[0]?.id || ''; $('setting-primary-provider').replaceChildren(...settingsProviders.map(p => new Option(`${p.name || p.id}${p.configured ? '' : ' · not configured'}`, p.id))); $('setting-primary-provider').value = selected; const models = settingsProviders.find(p => p.id === selected)?.models || []; $('setting-primary-model').replaceChildren(...models.map(m => new Option(m.id, m.id))); $('setting-primary-model').value = preferences.primary_model || models[0]?.id || ''; $('settings-providers').replaceChildren(...settingsProviders.map(p => { const row = document.createElement('div'); row.className = 'provider-row'; row.innerHTML = `<strong>${escapeHtml(p.name || p.id)}</strong><span class="status-badge">${p.configured ? 'configured' : 'not configured'}</span>`; return row; })); }
  async function saveSettings(scope, values) { const response = await fetch(`/api/settings/${scope}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({values})}); if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || 'Could not save settings'); return response.json(); }
  $('setting-primary-provider').onchange = () => renderProviderSettings({primary_provider:$('setting-primary-provider').value});
  $('setting-theme').onchange = () => applyTheme($('setting-theme').value);
  $('theme-swatches').onclick = event => {
    const button = event.target.closest('[data-theme-value]');
    if (!button) return;
    applyTheme(button.dataset.themeValue);
  };
  $('save-user-settings').onclick = async () => { const status = $('user-settings-status'); try { const theme = applyTheme($('setting-theme').value); await saveSettings('user', {theme}); status.textContent = 'Saved'; } catch (err) { status.textContent = err.message; } };
  $('save-org-settings').onclick = async () => { const status = $('org-settings-status'); try { await saveSettings('organization', {timezone:$('setting-timezone').value.trim()}); status.textContent = 'Saved'; } catch (err) { status.textContent = err.message; } };
  $('save-provider-settings').onclick = async () => { const status = $('provider-settings-status'); try { const providerId = $('setting-primary-provider').value; const values = {primary_provider:providerId, primary_model:$('setting-primary-model').value, fallback_enabled:$('setting-fallback').checked}; await saveSettings('providers', values); const key = $('setting-provider-key').value.trim(); if (key) { const response = await fetch(`/api/settings/providers/${providerId}/credential`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({api_key:key})}); if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || 'Could not save provider key'); $('setting-provider-key').value = ''; } status.textContent = 'Provider settings saved'; } catch (err) { status.textContent = err.message; } };
  document.addEventListener('click', e => { if (!e.target.closest('.menu-wrap')) $('menu').hidden = true; });
  function keepChatAtBottom() { messages.scrollTop = messages.scrollHeight; }
  function keepInputVisible(input) {
    input.scrollIntoView({block:'nearest', inline:'nearest'});
    [40, 180, 420].forEach(delay => setTimeout(keepChatAtBottom, delay));
  }
  $('message').addEventListener('focus', e => keepInputVisible(e.target));
  $('message').addEventListener('input', () => { autoResizeInput(); keepChatAtBottom(); });
  messages.addEventListener('click', e => {
    const prompt = e.target.closest('[data-prompt]');
    if (prompt) { $('message').value = prompt.dataset.prompt; autoResizeInput(); $('message').focus(); keepChatAtBottom(); return; }
    const copy = e.target.closest('[data-copy]');
    if (copy) { const body = copy.closest('.message').querySelector('.message-body'); const text = body.innerText; const done = () => { copy.textContent = '✓'; setTimeout(() => copy.textContent = '⧉', 1200); }; if (navigator.clipboard) navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done)); else fallbackCopy(text, done); }
  });
  function fallbackCopy(text, done) { const area = document.createElement('textarea'); area.value = text; document.body.append(area); area.select(); try { document.execCommand('copy'); done(); } finally { area.remove(); } }
  function syncViewportHeight() {
    const vv = window.visualViewport;
    const height = vv ? vv.height : window.innerHeight;
    document.documentElement.style.setProperty('--viewport-height', `${height}px`);
  }
  syncViewportHeight();
  window.addEventListener('resize', syncViewportHeight);
  window.addEventListener('orientationchange', () => setTimeout(syncViewportHeight, 150));
  if (window.visualViewport) {
    window.visualViewport.addEventListener('resize', () => {
      syncViewportHeight();
      if (document.activeElement === $('message')) keepChatAtBottom();
    });
    window.visualViewport.addEventListener('scroll', () => {
      if (window.scrollY > 0) window.scrollTo(0, 0);
    });
  }

  try { applyTheme(localStorage.getItem('jarvis.theme') || 'dark'); } catch (err) { applyTheme('dark'); }

  fetch('/api/auth/me').then(r => r.json()).then(data => {
    if (data.authenticated) {
      showApp();
      return init();
    }
    showLoginScreen();
  }).catch(() => showLoginScreen());

  const registerWorker = () => navigator.serviceWorker.register('/sw.js', {scope: '/'}).catch(() => {});
  if ('serviceWorker' in navigator) {
    if ('requestIdleCallback' in window) requestIdleCallback(registerWorker);
    else window.addEventListener('load', registerWorker);
  }
})();
