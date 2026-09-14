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
  $('login-form').onsubmit = async e => { e.preventDefault(); const error = $('login-error'); error.textContent = ''; const response = await fetch('/api/auth/login', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username:$('login-username').value, password:$('login-password').value})}); if (!response.ok) { error.textContent = 'Invalid username or password'; return; } showApp(); init().catch(err => addMessage('assistant', `Unable to connect: ${err.message}`)); };
  async function clearChat() {
    if (!conversationId || !confirm('Clear this chat?')) return;
    await fetch(`/api/conversations/${conversationId}`, {method:'DELETE'});
    conversationId = (await (await fetch('/api/conversations', {method:'POST'})).json()).id;
    localStorage.setItem('jarvis.conversationId', conversationId);
    messages.innerHTML = '<div class="welcome"><h1>How can I help?</h1><p>Ask JARVIS about your infrastructure, or type <button id="welcome-terminal">give me terminal</button>.</p></div>';
    $('welcome-terminal').onclick = openTerminal;
    $('menu').hidden = true;
  }
  provider.onchange = () => fetch('/api/providers/switch', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({provider:provider.value})});
  async function sendMessage(text) {
    addMessage('user', text); if (/^\s*(give me|open) terminal\s*$/i.test(text)) openTerminal();
    const target = addMessage('assistant', ''); target.textContent = 'Thinking…';
    const response = await fetch(`/api/conversations/${conversationId}/chat/stream`, {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({message:text, provider:provider.value})});
    if (!response.ok) { target.textContent = `Request failed (${response.status})`; return; }
    target.textContent = ''; const reader = response.body.getReader(), decoder = new TextDecoder(); let buffer = '';
    while (true) { const {value, done} = await reader.read(); if (done) break; buffer += decoder.decode(value, {stream:true}); const chunks = buffer.split('\n\n'); buffer = chunks.pop(); chunks.forEach(chunk => { const line = chunk.split('\n').find(x => x.startsWith('data: ')); if (!line) return; const event = JSON.parse(line.slice(6)); if (event.type === 'text') target.textContent = event.content; if (event.type === 'error') target.textContent = event.content; }); }
  }
  function openTerminal() { window.location.assign('/terminal'); }
  $('chat-form').onsubmit = e => { e.preventDefault(); const input = $('message'); const text = input.value.trim(); if (!text) return; input.value = ''; sendMessage(text).catch(err => addMessage('assistant', `Error: ${err.message}`)); };
  $('terminal-button').onclick = openTerminal; $('welcome-terminal').onclick = openTerminal;
  $('menu-button').onclick = () => { const menu = $('menu'); menu.hidden = !menu.hidden; $('menu-button').setAttribute('aria-expanded', String(!menu.hidden)); };
  $('clear-chat').onclick = () => clearChat().catch(err => addMessage('assistant', `Unable to clear chat: ${err.message}`));
  $('menu-terminal').onclick = () => { $('menu').hidden = true; openTerminal(); };
  $('logout').onclick = async () => { await fetch('/api/auth/logout', {method:'POST'}); location.reload(); };
  document.addEventListener('click', e => { if (!e.target.closest('.menu-wrap')) $('menu').hidden = true; });
  function keepInputVisible(input) { setTimeout(() => input.scrollIntoView({block:'nearest', inline:'nearest'}), 250); }
  $('message').addEventListener('focus', e => keepInputVisible(e.target));
  function syncViewportHeight() { const height = window.visualViewport ? window.visualViewport.height : window.innerHeight; document.documentElement.style.setProperty('--viewport-height', `${height}px`); }
  syncViewportHeight(); window.addEventListener('resize', syncViewportHeight); if (window.visualViewport) window.visualViewport.addEventListener('resize', syncViewportHeight);
  fetch('/api/auth/me').then(r => r.json()).then(data => { if (data.authenticated) { showApp(); return init(); } $('login-screen').hidden = false; appShell.hidden = true; }).catch(() => { $('login-screen').hidden = false; appShell.hidden = true; });
  if ('serviceWorker' in navigator) window.addEventListener('load', () => navigator.serviceWorker.register('/static/sw.js').catch(() => {}));
})();
