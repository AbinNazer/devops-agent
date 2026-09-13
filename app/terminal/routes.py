import json
import logging
import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from .manager import get_terminal_manager

logger = logging.getLogger("terminal")

router = APIRouter()


@router.post("/api/terminal/session")
async def create_terminal(rows: int = 24, cols: int = 80):
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
    mgr = get_terminal_manager()
    session = mgr.get_session(session_id)

    if not session:
        await websocket.close(code=4004, reason="Session not found")
        return

    await websocket.accept()
    logger.info("terminal_ws_connected session=%s", session_id)

    async def read_ssh_output():
        while session.is_alive() and websocket.client_state == WebSocketState.CONNECTED:
            try:
                output = await asyncio.wait_for(session.read_output(), timeout=0.1)
                if output:
                    await websocket.send_text(json.dumps({"type": "output", "data": output}))
            except asyncio.TimeoutError:
                pass
            except Exception:
                break
            await asyncio.sleep(0.01)

    output_task = asyncio.create_task(read_ssh_output())

    try:
        while websocket.client_state == WebSocketState.CONNECTED:
            data = await websocket.receive_text()
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
