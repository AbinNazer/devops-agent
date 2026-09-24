import json
import logging
import asyncio
import time

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException
from starlette.websockets import WebSocketState

from .manager import get_terminal_manager, MAX_SESSIONS
from app.auth import valid_session

logger = logging.getLogger("terminal")

router = APIRouter()

# Close codes the browser can actually see. Anything sent by closing before
# accept() is a handshake failure, which the WebSocket API reports to
# JavaScript as a generic code 1006 — the client cannot tell "not signed in"
# from "wrong URL" from "proxy ate the upgrade". Accepting first and then
# closing with these codes is what makes the failure diagnosable.
CLOSE_AUTH_REQUIRED = 4401
CLOSE_SESSION_MISSING = 4004
CLOSE_SESSION_FAILED = 1011
CLOSE_SERVER_BUSY = 4429


def _authenticated(request_or_ws) -> bool:
    return valid_session(request_or_ws.cookies.get("jarvis_session"))


@router.post("/api/terminal/session")
async def create_authenticated_terminal(request: Request, rows: int = 24, cols: int = 80):
    if not _authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    mgr = get_terminal_manager()
    try:
        session = await mgr.create_session(rows=rows, cols=cols)
        return {"session_id": session.id, "status": "connected"}
    except RuntimeError as e:
        # Capacity problem — distinguishable from a transport failure.
        logger.warning("terminal_create_rejected: %s", e)
        raise HTTPException(status_code=429, detail=str(e))
    except Exception as e:
        logger.error("terminal_create_error: %s", e)
        return {"error": str(e)[:200]}


@router.get("/api/terminal/sessions")
async def list_sessions(request: Request):
    if not _authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    mgr = get_terminal_manager()
    return {"active": mgr.active_count, "max": MAX_SESSIONS}


async def _close_terminal(request: Request, session_id: str) -> dict:
    """Close a session.

    Reachable as DELETE (explicit) and POST (``navigator.sendBeacon`` can only
    send POST, and the terminal page uses it on unload). Without the POST route
    the beacon hit a 405 and every abandoned terminal leaked a session slot
    until the five-session limit made new connections fail.
    """
    if not _authenticated(request):
        raise HTTPException(status_code=401, detail="Authentication required")
    mgr = get_terminal_manager()
    if mgr.close_session(session_id):
        return {"success": True}
    return {"success": False, "error": "Session not found"}


@router.delete("/api/terminal/{session_id}")
async def close_terminal(request: Request, session_id: str):
    return await _close_terminal(request, session_id)


@router.post("/api/terminal/{session_id}")
async def close_terminal_beacon(request: Request, session_id: str):
    return await _close_terminal(request, session_id)


async def _reject(websocket: WebSocket, code: int, message: str, log_reason: str) -> None:
    """Accept, explain, then close so the browser receives the real code."""
    await websocket.accept()
    try:
        await websocket.send_text(json.dumps({"type": "error", "data": message, "code": code}))
    except Exception:
        pass
    await websocket.close(code=code, reason=message[:120])
    logger.warning("terminal_ws_rejected reason=%s code=%d", log_reason, code)


@router.websocket("/ws/terminal/{session_id}")
async def terminal_ws(websocket: WebSocket, session_id: str):
    if not _authenticated(websocket):
        # No cookie at all is the normal state inside a freshly installed
        # home-screen app: PWA storage is separate from the browser tab, so the
        # user has to sign in again inside the app.
        await _reject(
            websocket,
            CLOSE_AUTH_REQUIRED,
            "Authentication required - sign in again in this app.",
            "unauthenticated",
        )
        return
    mgr = get_terminal_manager()
    session = mgr.get_session(session_id)

    if not session:
        await _reject(
            websocket,
            CLOSE_SESSION_MISSING,
            "Terminal session not found - it may have expired.",
            "session_missing",
        )
        return

    await websocket.accept()
    logger.info("terminal_ws_connected session=%s", session_id)

    # The session is connected in the background so the HTTP create request
    # returns immediately. Wait here only for the SSH/PTY to become usable.
    deadline = time.monotonic() + 20
    while getattr(session, "state", None).value == "connecting" and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    if getattr(session, "state", None).value != "connected":
        await websocket.send_text(json.dumps({"type": "error", "data": "Terminal connection failed"}))
        await websocket.close(code=CLOSE_SESSION_FAILED, reason="Terminal connection failed")
        mgr.close_session(session_id)
        return


    async def read_ssh_output():
        while websocket.client_state == WebSocketState.CONNECTED:
            try:
                # read_output() blocks on asyncio.Queue — no sleep needed
                output = await session.read_output()
                if output is None:
                    # EOF / session closed
                    break
                # PTYs often deliver a burst as many small chunks. Drain the
                # already-buffered chunks into one frame to reduce websocket
                # overhead without adding a deliberate delay to interactive input.
                chunks = [output]
                while len(chunks) < 32:
                    try:
                        extra = session._queue.get_nowait() if session._queue is not None else None
                    except asyncio.QueueEmpty:
                        break
                    if extra is None:
                        break
                    if isinstance(extra, bytes):
                        extra = extra.decode("utf-8", errors="replace")
                    chunks.append(extra)
                await websocket.send_text(json.dumps({"type": "output", "data": "".join(chunks)}))
            except asyncio.CancelledError:
                break
            except Exception:
                break


    output_task = asyncio.create_task(read_ssh_output())
    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=25)
            except asyncio.TimeoutError:
                # Keep mobile browser/proxy connections alive while the shell
                # is idle. The client can answer with its normal ping path.
                await websocket.send_text(json.dumps({"type": "ping"}))
                continue
            msg = json.loads(data)
            msg_type = msg.get("type")

            if msg_type == "input":
                await session.write_input(msg.get("data", ""))
            elif msg_type == "resize":
                rows = msg.get("rows", 24)
                cols = msg.get("cols", 80)
                session.resize(rows, cols)
            elif msg_type == "ping":
                await websocket.send_text(json.dumps({"type": "pong"}))

    except WebSocketDisconnect:
        logger.info("terminal_ws_disconnected session=%s", session_id)
    except Exception as e:
        logger.error("terminal_ws_error session=%s error=%s", session_id, e)
    finally:
        output_task.cancel()
        try:
            await output_task
        except asyncio.CancelledError:
            pass
        session.close()
        mgr.close_session(session_id)
