(() => {
  const $ = id => document.getElementById(id);
  let conversationId;
  const messages = $('messages');
  const provider = $('provider');
  const appShell = document.querySelector('.app-shell');

  function addMessage(role, content) {
    const welcome = messages.querySelector('.welcome');
    if (welcome && role === 'user') welcome.remove();
    const el = document.createElement('article'); el.className = `message ${role}`;
    el.innerHTML = `<div class="message-label">${role === 'user' ? 'You' : 'JARVIS'}</div>`;
    const body = document.createElement('div'); body.textContent = content; el.append(body);
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
  $('login-form').onsubmit = async e => { e.preventDefault(); const error = $('login-error'); error.textContent = ''; const response = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:$('login-username').value, password:$('login-password').value})}); if (!response.ok) { error.textContent = 'Invalid username or password'; return; } showApp(); init().catch(err => addMessage('assistant', `Unable to connect: ${err.message}`)); };
  async function clearChat() {
    if (!conversationId || !confirm('Clear this chat?')) return;
    await fetch(`/api/conversations/${conversationId}`, {method:'DELETE'});
    conversationId = (await (await fetch('/api/conversations', {method:'POST'})).json()).id;
    localStorage.setItem('jarvis.conversationId', conversationId);
    messages.innerHTML = '<div class="welcome"><span class="eyebrow">DEVOPS CONSOLE</span><h1>Awaiting your command…</h1></div>';
    $('menu').hidden = true;
  }
  provider.onchange = () => fetch('/api/providers/switch', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({provider:provider.value})});
  async function sendMessage(text) {
    addMessage('user', text); if (/^\s*(give me|open) terminal\s*$/i.test(text)) openTerminal();
    const target = addMessage('assistant', ''); target.textContent = 'Thinking…';
    const response = await fetch(`/api/conversations/${conversationId}/chat/stream`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message:text, provider:provider.value})});
    if (!response.ok) { target.textContent = `Request failed (${response.status})`; return; }
    target.textContent = ''; const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '';
    while (true) { const {value, done} = await reader.read(); if (done) break; buffer += decoder.decode(value, {stream:true}); const chunks = buffer.split('\n\n'); buffer = chunks.pop(); chunks.forEach(chunk => { const line = chunk.split('\n').find(x => x.startsWith('data: ')); if (!line) return; const event = JSON.parse(line.slice(6)); if (event.type === 'text') target.textContent = event.content; if (event.type === 'error') target.textContent = event.content; messages.scrollTop = messages.scrollHeight; }); }
  }
  function openTerminal() { window.location.assign('/terminal'); }
  $('chat-form').onsubmit = e => { e.preventDefault(); const input = $('message'); const text = input.value.trim(); if (!text) return; input.value = ''; sendMessage(text).catch(err => addMessage('assistant', `Error: ${err.message}`)); };
  $('new-chat').onclick = () => clearChat().catch(err => addMessage('assistant', `Unable to start a new chat: ${err.message}`));
  $('menu-button').onclick = () => { const menu = $('menu'); menu.hidden = !menu.hidden; $('menu-button').setAttribute('aria-expanded', String(!menu.hidden)); };
  $('clear-chat').onclick = () => clearChat().catch(err => addMessage('assistant', `Unable to clear chat: ${err.message}`));
  $('menu-terminal').onclick = () => { $('menu').hidden = true; openTerminal(); };
  $('logout').onclick = async () => { await fetch('/api/auth/logout', {method:'POST'}); location.reload(); };
  $('menu-status').onclick = () => { $('menu').hidden = true; document.querySelector('.chat-panel').hidden = true; $('status-panel').hidden = false; loadStatus(); };
  $('status-refresh').onclick = loadStatus;
  $('back-chat').onclick = () => { $('status-panel').hidden = true; document.querySelector('.chat-panel').hidden = false; };
  document.addEventListener('click', e => { if (!e.target.closest('.menu-wrap')) $('menu').hidden = true; });
  function keepChatAtBottom() { messages.scrollTop = messages.scrollHeight; }
  function keepInputVisible(input) {
    input.scrollIntoView({block:'nearest', inline:'nearest'});
    [40, 180, 420].forEach(delay => setTimeout(keepChatAtBottom, delay));
  }
  $('message').addEventListener('focus', e => keepInputVisible(e.target));
  $('message').addEventListener('input', () => { messages.scrollTop = messages.scrollHeight; });
  function syncViewportHeight() { const height = window.visualViewport ? window.visualViewport.height : window.innerHeight; document.documentElement.style.setProperty('--viewport-height', `${height}px`); }
  syncViewportHeight(); window.addEventListener('resize', syncViewportHeight); if (window.visualViewport) window.visualViewport.addEventListener('resize', () => { syncViewportHeight(); if (document.activeElement === $('message')) keepChatAtBottom(); });
  fetch('/api/auth/me').then(r => r.json()).then(data => { if (data.authenticated) { showApp(); return init(); } $('login-screen').hidden = false; appShell.hidden = true; }).catch(() => { $('login-screen').hidden = false; appShell.hidden = true; });
  if ('serviceWorker' in navigator) window.addEventListener('load', () => navigator.serviceWorker.register('/static/sw.js').catch(() => {}));
})();
