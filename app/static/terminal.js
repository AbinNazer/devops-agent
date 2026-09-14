var term, ws, sid, fa;
var cm = false, am = false;

function setDot(s) {
  var d = document.getElementById("dot");
  d.className = "sd " + s;
}

function initTerm() {
  term = new Terminal({
    cursorBlink: true, fontSize: 15,
    fontFamily: "Cascadia Code, Fira Code, JetBrains Mono, monospace",
    theme: {
      background: "#0a0e17", foreground: "#c8d6e5", cursor: "#00d4ff",
      selectionBackground: "#00d4ff33",
      black: "#1a2332", red: "#ff5252", green: "#00e676",
      yellow: "#ffc107", blue: "#448aff", magenta: "#e040fb",
      cyan: "#00d4ff", white: "#c8d6e5"
    },
    scrollback: 50000,
    scrollSensitivity: 3
  });
  fa = new FitAddon.FitAddon();
  term.loadAddon(fa);
  try { term.loadAddon(new WebLinksAddon.WebLinksAddon()); } catch(e) {}
  term.open(document.getElementById("tc"));
  fa.fit();
  term.onData(function(d) { send(d); });
  term.onResize(function(dm) {
    if (ws && ws.readyState === 1)
      ws.send(JSON.stringify({type:"resize",rows:dm.rows,cols:dm.cols}));
  });
  window.addEventListener("resize", function() { if(fa) fa.fit(); });
}

function startSess() {
  setDot("wait");
  document.getElementById("cOv").style.display = "flex";
  document.getElementById("eOv").style.display = "none";
  fetch("/api/terminal/session", {method:"POST"})
    .then(function(r){return r.json()})
    .then(function(d){
      if(d.error) throw new Error(d.error);
      sid = d.session_id;
      connectWS();
    })
    .catch(function(e){ showErr("Failed: "+e.message); });
}

function connectWS() {
  var p = location.protocol==="https:" ? "wss:" : "ws:";
  ws = new WebSocket(p+"//"+location.host+"/ws/terminal/"+sid);
  ws.onopen = function() {
    setDot("on");
    document.getElementById("cOv").style.display = "none";
    document.getElementById("eOv").style.display = "none";
    ws.send(JSON.stringify({type:"resize",rows:term.rows,cols:term.cols}));
  };
  ws.onmessage = function(e) {
    try {
      var m = JSON.parse(e.data);
      if(m.type==="output") term.write(m.data);
      else if(m.type==="error") term.write("\r\n\x1b[31m"+m.data+"\x1b[0m\r\n");
    } catch(x){}
  };
  ws.onclose = function() {
    setDot("off");
    if(term) term.write("\r\n\x1b[33m[Disconnected]\x1b[0m\r\n");
  };
  ws.onerror = function(){ showErr("WebSocket failed"); };
}

function send(d) {
  if(ws && ws.readyState===1)
    ws.send(JSON.stringify({type:"input",data:d}));
}

function doKey(s) {
  var f = s;
  if(cm && s.length===1) { f = String.fromCharCode(s.charCodeAt(0)&0x1f); tC(); }
  if(am) { f = "\x1b"+s; tA(); }
  send(f);
  if(term) term.focus();
}

function doArr(c) {
  var s = "\x1b["+c;
  if(am) { s="\x1b\x1b["+c; tA(); }
  send(s);
  if(term) term.focus();
}

function tC() { cm=!cm; document.getElementById("cB").classList.toggle("act",cm); }
function tA() { am=!am; document.getElementById("aB").classList.toggle("act",am); }
function doClear() { if(term) term.clear(); }

function doReconnect() {
  if(ws) ws.close();
  if(sid) fetch("/api/terminal/"+sid,{method:"DELETE"}).catch(function(){});
  startSess();
}

function showErr(m) {
  document.getElementById("cOv").style.display = "none";
  document.getElementById("eOv").style.display = "flex";
  document.getElementById("eMsg").textContent = m;
  setDot("off");
}

window.addEventListener("load", function(){ initTerm(); startSess(); });
window.addEventListener("beforeunload", function(){ if(ws) ws.close(); });
