"""
app/services/notifier.py
─────────────────────────
WebSocket Connection Manager for Real-Time Dashboard Updates.

WHAT THIS FILE DOES
────────────────────
- Holds active WebSocket connections from the React dashboard
- Broadcasts JSON payloads whenever the backend state changes
  (e.g., new SMS arrives, crisis goes to HITL_REVIEW, crisis confirmed)
"""
import json
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self):
        # We store active WebSockets here
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        logger.info(f"🟢 Client connected to WebSocket. Total active: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
            logger.info(f"🔴 Client disconnected from WebSocket. Total active: {len(self.active_connections)}")

    async def broadcast(self, event_type: str, payload: dict[str, Any]):
        """
        Send a JSON message to all connected React clients.
        
        Args:
            event_type (str): e.g., 'CRISIS_UPDATED', 'NEW_REPORT'
            payload (dict): The data to send
        """
        if not self.active_connections:
            return  # No one listening, skip

        message = {
            "type": event_type,
            "data": payload
        }
        
        message_str = json.dumps(message, default=str) # Handle UUIDs/Datetimes

        for connection in self.active_connections:
            try:
                await connection.send_text(message_str)
            except Exception as e:
                logger.error(f"Failed to send WS message to client: {e}")
                
        # logger.debug(f"📣 Broadcasted '{event_type}' to {len(self.active_connections)} clients")


# Single global instance
notifier = ConnectionManager()
