(() => {
  const $ = id => document.getElementById(id);
  let conversationId;
  const messages = $('messages');
  const provider = $('provider');
  const appShell = document.querySelector('.app-shell');
  const escapeHtml = value => value.replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
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
  function showApp() { $('login-screen').hidden = true; appShell.hidden = false; }
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
    messages.innerHTML = '<div class="welcome"><span class="eyebrow">DEVOPS CONSOLE</span><h1>What are we investigating?</h1><div class="quick-actions"><button type="button" data-prompt="Check infrastructure health">Check infrastructure</button><button type="button" data-prompt="Show running containers">Show containers</button><button type="button" data-prompt="Check CPU and memory usage">Check CPU / memory</button><button type="button" data-prompt="Show recent errors">Recent errors</button></div></div>';
    $('menu').hidden = true;
  }
  provider.onchange = () => fetch('/api/providers/switch', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({provider:provider.value})});
  async function sendMessage(text) {
    addMessage('user', text); if (/^\s*(give me|open) terminal\s*$/i.test(text)) openTerminal();
    const target = addMessage('assistant', '');
    target.innerHTML = '<div class="assistant-status"><span class="typing"><i></i><i></i><i></i></span> <span class="status-text">Thinking…</span></div>';
    try {
      const response = await fetch(`/api/conversations/${conversationId}/chat/stream`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
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
              const friendlyName = (event.name || '').replace(/_/g, ' ');
              statusEl.textContent = `Checking ${friendlyName}…`;
            } else if (event.type === 'tool_result' && statusEl) {
              const friendlyName = (event.name || '').replace(/_/g, ' ');
              statusEl.textContent = `Completed ${friendlyName}…`;
            } else if (event.type === 'text') {
              target.innerHTML = renderMarkdown(event.content);
            } else if (event.type === 'error') {
              target.textContent = event.content;
            }
          } catch (pe) {}
          keepChatAtBottom();
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
  $('logout').onclick = async () => { await fetch('/api/auth/logout', {method:'POST'}); location.reload(); };
  $('menu-status').onclick = () => { $('menu').hidden = true; document.querySelector('.chat-panel').hidden = true; $('status-panel').hidden = false; loadStatus(); };
  $('menu-settings').onclick = async () => { $('menu').hidden = true; document.querySelector('.chat-panel').hidden = true; $('status-panel').hidden = true; $('settings-panel').hidden = false; await loadSettings(); };
  $('status-refresh').onclick = loadStatus;
  $('back-chat').onclick = () => { $('status-panel').hidden = true; document.querySelector('.chat-panel').hidden = false; };
  $('settings-back').onclick = () => { $('settings-panel').hidden = true; document.querySelector('.chat-panel').hidden = false; };
  document.querySelectorAll('[data-settings-section]').forEach(button => button.onclick = () => { document.querySelectorAll('.settings-nav-item').forEach(item => item.classList.toggle('active', item === button)); document.querySelectorAll('[data-settings-section-content]').forEach(section => section.hidden = section.dataset.settingsSectionContent !== button.dataset.settingsSection); if (button.dataset.settingsSection === 'infrastructure') loadInfrastructureTargets(); });
  $('research-form').onsubmit = async event => { event.preventDefault(); const status = $('research-status'); const query = $('research-query').value.trim(); status.textContent = 'Starting research…'; $('research-results').textContent = ''; const response = await fetch('/api/research/learn/stream', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({tool:query, version:$('research-version').value.trim(), refresh:$('research-refresh').checked})}); if (!response.ok) { const data = await response.json().catch(() => ({})); status.textContent = data.detail || 'Research failed'; return; } const reader = response.body.getReader(); const decoder = new TextDecoder(); let buffer = ''; while (true) { const {value, done} = await reader.read(); if (done) break; buffer += decoder.decode(value, {stream:true}); const chunks = buffer.split('\n\n'); buffer = chunks.pop(); chunks.forEach(chunk => { const line = chunk.split('\n').find(item => item.startsWith('data: ')); if (!line) return; const eventData = JSON.parse(line.slice(6)); if (eventData.stage === 'started') status.textContent = `Preparing research for ${eventData.tool}…`; if (eventData.stage === 'searching_official_docs') status.textContent = 'Searching official documentation…'; if (eventData.stage === 'completed') { const data = eventData.result; status.textContent = data.status === 'already_known' ? 'This skill is already cached.' : 'Research complete — skill saved.'; $('research-results').innerHTML = `<strong>${escapeHtml(data.skill || query)}</strong><p>Sources: ${(data.sources || []).length}. Local version and project references were recorded. No execution performed.</p>`; } if (eventData.stage === 'failed') status.textContent = eventData.result?.error || 'Research failed'; }); } };
  $('target-kind').onchange = () => { const aws = $('target-kind').value === 'aws'; $('target-host-label').hidden = aws; $('target-username').closest('label').hidden = aws; $('target-region-label').hidden = !aws; $('target-endpoint-label').hidden = !aws; };
  async function loadInfrastructureTargets() { const response = await fetch('/api/settings/infrastructure'); if (!response.ok) return; const data = await response.json(); $('infrastructure-targets').replaceChildren(...(data.targets || []).map(target => { const card = document.createElement('article'); card.className = 'target-card'; card.innerHTML = `<div><strong>${escapeHtml(target.name)}</strong><span>${escapeHtml(target.kind.toUpperCase())}</span><small>${escapeHtml(target.host || target.region || target.endpoint || 'No endpoint')}</small></div><button type="button" data-delete-target="${escapeHtml(target.id)}" aria-label="Remove ${escapeHtml(target.name)}">×</button>`; return card; })); }
  $('infrastructure-form').onsubmit = async event => { event.preventDefault(); const status = $('target-status'); const payload = {name:$('target-name').value.trim(), kind:$('target-kind').value, host:$('target-host').value.trim(), port:$('target-port').value ? Number($('target-port').value) : null, username:$('target-username').value.trim(), region:$('target-region').value.trim(), endpoint:$('target-endpoint').value.trim(), notes:$('target-notes').value.trim()}; const response = await fetch('/api/settings/infrastructure', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(payload)}); const data = await response.json().catch(() => ({})); if (!response.ok) { status.textContent = data.detail || 'Could not save target'; return; } event.target.reset(); status.textContent = 'Infrastructure target added'; loadInfrastructureTargets(); };
  $('infrastructure-targets').onclick = async event => { const button = event.target.closest('[data-delete-target]'); if (!button) return; await fetch(`/api/settings/infrastructure/${button.dataset.deleteTarget}`, {method:'DELETE'}); loadInfrastructureTargets(); };
  let settingsProviders = [];
  async function loadSettings() { const [user, org, providers] = await Promise.all([fetch('/api/settings/user'), fetch('/api/settings/organization'), fetch('/api/settings/providers')]); const userData = await user.json(); const orgData = await org.json(); const providerData = await providers.json(); settingsProviders = providerData.providers || []; $('setting-theme').value = userData.values.theme || 'dark'; document.documentElement.dataset.theme = $('setting-theme').value; $('setting-timezone').value = orgData.values.timezone || ''; const preferences = providerData.preferences || {}; $('setting-fallback').checked = preferences.fallback_enabled !== false; renderProviderSettings(preferences); loadInfrastructureTargets(); }
  function renderProviderSettings(preferences = {}) { const selected = preferences.primary_provider || provider.value || settingsProviders.find(p => p.configured)?.id || settingsProviders[0]?.id || ''; $('setting-primary-provider').replaceChildren(...settingsProviders.map(p => new Option(`${p.name || p.id}${p.configured ? '' : ' · not configured'}`, p.id))); $('setting-primary-provider').value = selected; const models = settingsProviders.find(p => p.id === selected)?.models || []; $('setting-primary-model').replaceChildren(...models.map(m => new Option(m.id, m.id))); $('setting-primary-model').value = preferences.primary_model || models[0]?.id || ''; $('settings-providers').replaceChildren(...settingsProviders.map(p => { const row = document.createElement('div'); row.className = 'provider-row'; row.innerHTML = `<strong>${escapeHtml(p.name || p.id)}</strong><span class="status-badge">${p.configured ? 'configured' : 'not configured'}</span>`; return row; })); }
  async function saveSettings(scope, values) { const response = await fetch(`/api/settings/${scope}`, {method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify({values})}); if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || 'Could not save settings'); return response.json(); }
  $('setting-primary-provider').onchange = () => renderProviderSettings({primary_provider:$('setting-primary-provider').value});
  $('save-user-settings').onclick = async () => { const status = $('user-settings-status'); try { document.documentElement.dataset.theme = $('setting-theme').value; await saveSettings('user', {theme:$('setting-theme').value}); status.textContent = 'Saved'; } catch (err) { status.textContent = err.message; } };
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
  fetch('/api/auth/me').then(r => r.json()).then(data => { if (data.authenticated) { showApp(); return init(); } $('login-screen').hidden = false; appShell.hidden = true; }).catch(() => { $('login-screen').hidden = false; appShell.hidden = true; });
  if ('serviceWorker' in navigator) window.addEventListener('load', () => navigator.serviceWorker.register('/static/sw.js').catch(() => {}));
})();
