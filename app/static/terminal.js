var term, ws, sid, fa;
var cm = false, am = false;
var writeBuffer = [];
var writeRafId = null;
var bufferByteLen = 0;
var resizeTimer = null;
var wsOpened = false;
var connectTimer = null;
var reconnectAttempted = false;
var started = false;
var sessionToken = null;

// Server-side close codes (see app/terminal/routes.py).
var CLOSE_AUTH_REQUIRED = 4401;
var CLOSE_SESSION_MISSING = 4004;

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

// Xterm assets are self-hosted; if they are ever missing we say so instead of
// spinning forever. Returning false means "do not start a session".
function initTerm() {
  if (typeof Terminal === "undefined") {
    showErr("Terminal UI failed to load: the xterm bundle at /static/vendor/xterm/ is unavailable. Reload the page; if it persists, the server is serving the terminal page without its assets.", { hint: "Check that app/static/vendor/xterm/ is deployed." });
    return false;
  }
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
  if (typeof FitAddon !== "undefined") {
    fa = new FitAddon.FitAddon();
    term.loadAddon(fa);
  }
  try {
    if (typeof WebLinksAddon !== "undefined") term.loadAddon(new WebLinksAddon.WebLinksAddon());
  } catch (e) {}
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
  return true;
}

function showConnecting() {
  var c = document.getElementById("cOv");
  var e = document.getElementById("eOv");
  if (c) c.style.display = "flex";
  if (e) e.style.display = "none";
}

function showErr(m, options) {
  options = options || {};
  var c = document.getElementById("cOv");
  var e = document.getElementById("eOv");
  if (c) c.style.display = "none";
  if (e) e.style.display = "flex";
  var msg = document.getElementById("eMsg");
  if (msg) msg.textContent = m;
  var hint = document.getElementById("eHint");
  if (hint) {
    hint.textContent = options.hint || "";
    hint.style.display = options.hint ? "block" : "none";
  }
  var retry = document.getElementById("eRetry");
  if (retry) retry.style.display = options.hideRetry ? "none" : "inline-block";
  var signin = document.getElementById("eSignin");
  if (signin) signin.style.display = options.signIn ? "inline-block" : "none";
  setDot("off");
}

function notAuthenticated() {
  showErr("Not signed in. This browser context has no valid JARVIS session.", {
    signIn: true,
    hint: "Installed home-screen apps keep their own storage, so a login from a normal browser tab does not carry over and logins are not shared back. Sign in again inside this app."
  });
}

// Turn an HTTP/network failure into a message that names the actual cause.
function describeFetchError(err) {
  if (err && err.httpStatus === 401) {
    return;
  }
  if (err && err.httpStatus === 403) {
    showErr("The server refused the terminal request (HTTP 403, origin not allowed).", {
      hint: "Add this exact address to JARVIS_ALLOWED_ORIGINS on the server, then reload."
    });
    return;
  }
  if (err && err.httpStatus) {
    showErr("The server rejected the terminal request with HTTP " + err.httpStatus + ".", { hint: "Check the server logs for details." });
    return;
  }
  if (err && err.message === "OFFLINE") {
    showErr("You are offline, so the terminal cannot connect.", { hint: "Reconnect to the network and press Retry." });
    return;
  }
  showErr("Could not reach the JARVIS server: " + ((err && err.message) || "network error"), {
    hint: "The request never completed. Check the connection to the server, then press Retry."
  });
}

function startSess() {
  clearTimeout(connectTimer);
  if (!started) return;
  if (navigator.onLine === false) {
    showErr("You are offline, so the terminal cannot connect.", { hint: "Reconnect to the network and press Retry." });
    return;
  }
  setDot("wait");
  showConnecting();
  fetch("/api/terminal/session", { method: "POST", credentials: "same-origin" })
    .then(function(r) {
      if (r.status === 401) {
        var authErr = new Error("unauthorized");
        authErr.httpStatus = 401;
        throw authErr;
      }
      if (!r.ok) {
        var httpErr = new Error("http " + r.status);
        httpErr.httpStatus = r.status;
        throw httpErr;
      }
      return r.json();
    })
    .then(function(d) {
      if (d.error) {
        showErr("The server could not start a terminal session.", { hint: String(d.error) });
        return;
      }
      sid = d.session_id;
      sessionToken = d.session_id;
      connectWS();
    })
    .catch(function(e) {
      if (e && e.httpStatus === 401) notAuthenticated();
      else describeFetchError(e);
    });
}

function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  if (!sid) {
    showErr("No terminal session was created, so the connection was never attempted.", { hint: "Press Retry to request a new session." });
    return;
  }
  var p = location.protocol === "https:" ? "wss:" : "ws:";
  wsOpened = false;
  ws = new WebSocket(p + "//" + location.host + "/ws/terminal/" + encodeURIComponent(sid));
  connectTimer = setTimeout(function() {
    if (ws && ws.readyState !== WebSocket.OPEN) {
      try { ws.close(); } catch(e) {}
      showErr("Timed out waiting for the terminal to start.", {
        hint: "The WebSocket handshake never completed. Behind a reverse proxy, confirm it forwards Connection and Upgrade headers for /ws/."
      });
    }
  }, 22000);
  ws.onopen = function() {
    clearTimeout(connectTimer);
    wsOpened = true;
    reconnectAttempted = false;
    setDot("on");
    showConnecting();
    var c = document.getElementById("cOv");
    var e = document.getElementById("eOv");
    if (c) c.style.display = "none";
    if (e) e.style.display = "none";
    if (term) {
      ws.send(JSON.stringify({type:"resize",rows:term.rows,cols:term.cols}));
    }
  };
  ws.onmessage = function(e) {
    try {
      var m = JSON.parse(e.data);
      if (m.type === "output") queueWrite(m.data);
      else if (m.type === "error") queueWrite("\r\n\x1b[31m" + m.data + "\x1b[0m\r\n");
    } catch(x){}
  };
  ws.onclose = function(ev) {
    clearTimeout(connectTimer);
    flushBuffer();
    setDot("off");
    var code = ev && ev.code;
    var reason = ev && ev.reason;

    if (code === CLOSE_AUTH_REQUIRED) { notAuthenticated(); return; }
    if (code === CLOSE_SESSION_MISSING) {
      // The session expired or was reaped (idle/lifetime limits, server
      // restart). Create a fresh one exactly once instead of failing.
      if (!reconnectAttempted) {
        reconnectAttempted = true;
        sid = null;
        term && term.write("\r\n\x1b[33m[session expired - starting a new one]\x1b[0m\r\n");
        startSess();
        return;
      }
      showErr("That terminal session no longer exists on the server.", { hint: "Press Retry to start a new session." });
      return;
    }
    if (!wsOpened) {
      showErr("Terminal connection failed" + (code ? " (code " + code + ")" : "") + ".", {
        hint: reason ? String(reason) : "The WebSocket handshake was rejected before it opened. If this app is served through a reverse proxy, confirm it forwards Upgrade headers for /ws/."
      });
    } else if (term) {
      term.write("\r\n\x1b[33m[Disconnected" + (code ? " (code " + code + ")" : "") + "]\x1b[0m\r\n");
    }
  };
  ws.onerror = function(){
    if (!wsOpened) setDot("off");
  };
}

function send(d) {
  if (ws && ws.readyState === 1)
    ws.send(JSON.stringify({type:"input",data:d}));
}

function doKey(s) {
  var f = s;
  if (cm && s.length === 1) { f = String.fromCharCode(s.charCodeAt(0) & 0x1f); tC(); }
  if (am) { f = "\x1b" + s; tA(); }
  send(f);
  if (term) term.focus();
}

function doArr(c) {
  var s = "\x1b[" + c;
  if (am) { s = "\x1b\x1b[" + c; tA(); }
  send(s);
  if (term) term.focus();
}

function tC() { cm = !cm; document.getElementById("cB").classList.toggle("act", cm); }
function tA() { am = !am; document.getElementById("aB").classList.toggle("act", am); }
function doClear() { if (term) term.clear(); }

function doReconnect() {
  if (writeRafId) { cancelAnimationFrame(writeRafId); writeRafId = null; }
  writeBuffer = [];
  bufferByteLen = 0;
  reconnectAttempted = false;
  if (ws) {
    ws.onclose = null;
    ws.onerror = null;
    ws.onmessage = null;
    try { ws.close(); } catch(e) {}
    ws = null;
  }
  if (sid) {
    fetch("/api/terminal/" + encodeURIComponent(sid), { method: "DELETE", credentials: "same-origin" }).catch(function(){});
    sid = null;
  }
  showConnecting();
  startSess();
}

window.addEventListener("load", function() {
  started = true;
  // A failure while setting up the terminal must not prevent the session
  // attempt: initTerm() returns false and shows its own error, and we simply
  // skip the socket instead of hanging on the spinner.
  var ready = false;
  try {
    ready = initTerm();
  } catch (e) {
    showErr("Terminal UI failed to initialise: " + e.message, { hint: "Reload the page. If it persists, check the browser console for details." });
  }
  syncViewport();
  if (ready) startSess();
});

// Installed PWAs suspend JS and drop sockets when they go to the background,
// then restore the page (sometimes from bfcache) with a dead connection.
document.addEventListener("visibilitychange", function() {
  if (!started || document.visibilityState !== "visible") return;
  syncViewport();
  if (!ws || ws.readyState === 2 || ws.readyState === 3) doReconnect();
});

window.addEventListener("pageshow", function(ev) {
  if (started && ev.persisted) doReconnect();
});

window.addEventListener("offline", function() {
  if (started) showErr("You are offline, so the terminal cannot connect.", { hint: "Reconnect to the network and press Retry." });
});

window.addEventListener("beforeunload", function() {
  if (ws) {
    try { ws.close(); } catch(e) {}
  }
  if (sid) {
    navigator.sendBeacon("/api/terminal/" + encodeURIComponent(sid));
  }
});
