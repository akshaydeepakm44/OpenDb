"""
WebSocket Live Pipeline Dashboard Endpoint — §13 of Master Rules

Broadcasts real-time pipeline events to the React frontend:
- URL_DISCOVERED
- URL_REJECTED
- CRAWL_QUEUED
- CRAWL_COMPLETED
- RAW_INGESTED
- VERIFICATION_STARTED
- COMPANY_VERIFIED
- COMPANY_REJECTED
"""
import logging
import asyncio
from typing import List, Dict, Any
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger(__name__)

router = APIRouter()

class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"🔌 WebSocket client connected (Total: {len(self.active_connections)})")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"🔌 WebSocket client disconnected (Total: {len(self.active_connections)})")

    async def broadcast(self, message: Dict[str, Any]):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.debug(f"Failed to send to client: {e}")

ws_manager = ConnectionManager()

@router.websocket("/ws/pipeline")
async def websocket_pipeline_endpoint(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Keep connection alive with ping/pong or receive client heartbeat
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        logger.debug(f"WebSocket connection closed: {e}")
        ws_manager.disconnect(websocket)
