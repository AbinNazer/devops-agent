import json
import logging
import asyncio
import time

from fastapi import APIRouter, Request, WebSocket, WebSocketDisconnect, HTTPException
from starlette.websockets import WebSocketState

from .manager import get_terminal_manager
from app.auth import valid_session

logger = logging.getLogger("terminal")

router = APIRouter()


@router.post("/api/terminal/session")
async def create_authenticated_terminal(request: Request, rows: int = 24, cols: int = 80):
    if not valid_session(request.cookies.get("jarvis_session")):
        raise HTTPException(status_code=401, detail="Authentication required")
    mgr = get_terminal_manager()
    try:
        session = await mgr.create_session(rows=rows, cols=cols)
        return {"session_id": session.id, "status": "connected"}
    except Exception as e:
        logger.error("terminal_create_error: %s", e)
        return {"error": str(e)[:200]}


@router.get("/api/terminal/sessions")
async def list_sessions():
    mgr = get_terminal_manager()
    return {"active": mgr.active_count, "max": 5}


@router.delete("/api/terminal/{session_id}")
async def close_terminal(session_id: str):
    mgr = get_terminal_manager()
    if mgr.close_session(session_id):
        return {"success": True}
    return {"error": "Session not found"}


@router.websocket("/ws/terminal/{session_id}")
async def terminal_ws(websocket: WebSocket, session_id: str):
    if not valid_session(websocket.cookies.get("jarvis_session")):
        await websocket.close(code=4401, reason="Authentication required")
        return
    mgr = get_terminal_manager()
    session = mgr.get_session(session_id)

    if not session:
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()
    logger.info("terminal_ws_connected session=%s", session_id)


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
