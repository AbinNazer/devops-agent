var term, ws, sid, fa;
var cm = false, am = false;
var writeBuffer = [];
var writeRafId = null;
var bufferByteLen = 0;
var resizeTimer = null;
var wsOpened = false;
var connectTimer = null;
var reconnectAttempted = false;

function flushBuffer() {
  writeRafId = null;
  if (writeBuffer.length > 0 && term) {
    var chunk = writeBuffer.join("");
    writeBuffer = [];
    bufferByteLen = 0;
    term.write(chunk);
  }
}

function queueWrite(data) {
  writeBuffer.push(data);
  bufferByteLen += data.length;
  if (bufferByteLen > 65536) {
    if (writeRafId) { cancelAnimationFrame(writeRafId); writeRafId = null; }
    flushBuffer();
  } else if (!writeRafId) {
    writeRafId = requestAnimationFrame(flushBuffer);
  }
}

function setDot(s) {
  var d = document.getElementById("dot");
  if (d) d.className = "sd " + s;
}

function debouncedFit() {
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(function() {
    if (fa && term) {
      try { fa.fit(); } catch(e) {}
    }
  }, 75);
}

function syncViewport() {
  var vv = window.visualViewport;
  var h = vv ? vv.height : window.innerHeight;
  document.documentElement.style.setProperty("--viewport-height", h + "px");
  debouncedFit();
}

function initTerm() {
  term = new Terminal({
    cursorBlink: true, fontSize: 14,
    fontFamily: "Cascadia Code, Fira Code, JetBrains Mono, SFMono-Regular, monospace",
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
  debouncedFit();
  term.onData(function(d) { send(d); });
  term.onResize(function(dm) {
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({type:"resize",rows:dm.rows,cols:dm.cols}));
    }
  });

  window.addEventListener("resize", debouncedFit);
  window.addEventListener("orientationchange", function() { setTimeout(syncViewport, 100); });
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", syncViewport);
  }
}

function startSess() {
  clearTimeout(connectTimer);
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
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  var p = location.protocol==="https:" ? "wss:" : "ws:";
  wsOpened = false;
  ws = new WebSocket(p+"//"+location.host+"/ws/terminal/"+sid);
  connectTimer = setTimeout(function() {
    if (ws && ws.readyState !== WebSocket.OPEN) {
      try { ws.close(); } catch(e) {}
      showErr("Terminal connection timed out. Check SSH/local mode settings.");
    }
  }, 22000);
  ws.onopen = function() {
    clearTimeout(connectTimer);
    wsOpened = true;
    reconnectAttempted = false;
    setDot("on");
    document.getElementById("cOv").style.display = "none";
    document.getElementById("eOv").style.display = "none";
    if (term) {
      ws.send(JSON.stringify({type:"resize",rows:term.rows,cols:term.cols}));
    }
  };
  ws.onmessage = function(e) {
    try {
      var m = JSON.parse(e.data);
      if(m.type==="output") queueWrite(m.data);
      else if(m.type==="error") queueWrite("\r\n\x1b[31m"+m.data+"\x1b[0m\r\n");
    } catch(x){}
  };
  ws.onclose = function() {
    clearTimeout(connectTimer);
    flushBuffer();
    setDot("off");
    if (!wsOpened) {
      showErr("Terminal connection failed. Check the server logs and execution mode.");
    } else if(term) {
      term.write("\r\n\x1b[33m[Disconnected]\x1b[0m\r\n");
    }
  };
  ws.onerror = function(){
    if (!wsOpened) showErr("WebSocket connection failed");
  };
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
  if (writeRafId) { cancelAnimationFrame(writeRafId); writeRafId = null; }
  writeBuffer = [];
  bufferByteLen = 0;
  if (ws) {
    ws.onclose = null;
    ws.onerror = null;
    ws.onmessage = null;
    try { ws.close(); } catch(e) {}
    ws = null;
  }
  if (sid) {
    fetch("/api/terminal/"+sid, {method:"DELETE"}).catch(function(){});
    sid = null;
  }
  startSess();
}

function showErr(m) {
  document.getElementById("cOv").style.display = "none";
  document.getElementById("eOv").style.display = "flex";
  document.getElementById("eMsg").textContent = m;
  setDot("off");
}

window.addEventListener("load", function(){
  initTerm();
  syncViewport();
  startSess();
});
window.addEventListener("beforeunload", function(){
  if (ws) {
    try { ws.close(); } catch(e) {}
  }
  if (sid) {
    navigator.sendBeacon("/api/terminal/" + sid);
  }
});
