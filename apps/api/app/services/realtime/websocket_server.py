"""WebSocket server for real-time safety alert broadcasting."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from dataclasses import dataclass, field

from fastapi import WebSocket

# SECURITY (H-19): Import token validation and project access utilities
# so the manager can enforce authentication independently of callers.
from app.utils.security import decode_access_token

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HEARTBEAT_INTERVAL: float = 30.0  # seconds between pings
PONG_TIMEOUT: float = 10.0  # seconds to wait for a pong response
MESSAGE_QUEUE_MAXSIZE: int = 100  # bounded queue per project
RECONNECT_TOKEN_TTL: float = 300.0  # seconds a reconnect token stays valid
MAX_MESSAGE_SIZE: int = 4096  # Max bytes per message
MAX_MESSAGES_PER_SECOND: int = 10  # Rate limit per client
MAX_CONNECTIONS_PER_PROJECT: int = 50  # Connection limit per project


# ---------------------------------------------------------------------------
# Per-connection bookkeeping
# ---------------------------------------------------------------------------
@dataclass
class _ClientState:
    """Internal state tracked for every connected WebSocket."""

    websocket: WebSocket
    project_id: str
    # SECURITY [L-16]: Track user_id so reconnect tokens are bound to the
    # authenticated user and cannot be reused by a different user.
    user_id: str = ""
    last_pong: float = field(default_factory=time.monotonic)
    heartbeat_task: asyncio.Task | None = None
    reconnect_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))


# ---------------------------------------------------------------------------
# Per-project message queue entry
# ---------------------------------------------------------------------------
@dataclass
class _QueuedMessage:
    """A message stored in the project queue for replay on reconnect."""

    timestamp: float
    message: dict
    sequence: int


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------
class SafetyWebSocketManager:
    """Manage WebSocket connections for safety alert streaming."""

    def __init__(self):
        # project_id -> list of connected websockets  (original structure kept)
        self.connections: dict[str, list[WebSocket]] = {}

        # websocket id(ws) -> _ClientState
        self._client_states: dict[int, _ClientState] = {}

        # project_id -> bounded queue of recent messages
        self._project_queues: dict[str, asyncio.Queue[_QueuedMessage]] = {}

        # project_id -> monotonically increasing sequence counter
        self._project_sequences: dict[str, int] = {}

        # SECURITY [L-16]: reconnect_token -> (project_id, last_seen_sequence, issued_at, user_id)
        self._reconnect_tokens: dict[str, tuple[str, int, float, str]] = {}

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------
    async def connect(
        self,
        websocket: WebSocket,
        project_id: str,
        token: str | None = None,
        reconnect_token: str | None = None,
        already_accepted: bool = False,
    ) -> bool:
        """Accept and register a WebSocket connection.

        SECURITY (H-19): A valid JWT token is **required**. The token is
        validated here regardless of any pre-validation by the caller.
        The user's org_id from the token is checked against the project
        to prevent cross-tenant access.

        If *reconnect_token* is provided and valid, missed messages are
        replayed to the client immediately after acceptance.
        """
        # SECURITY (H-19): Reject connections without a valid JWT token.
        if not token:
            logger.warning(
                "WebSocket connection rejected: no token provided for project %s", project_id
            )
            if not already_accepted:
                await websocket.close(code=4001, reason="Authentication required")
            else:
                await websocket.close(code=4001, reason="Authentication required")
            return False

        payload = decode_access_token(token)
        if not payload or "sub" not in payload:
            logger.warning(
                "WebSocket connection rejected: invalid token for project %s", project_id
            )
            if not already_accepted:
                await websocket.close(code=4001, reason="Invalid token")
            else:
                await websocket.close(code=4001, reason="Invalid token")
            return False

        # SECURITY (H-19): Validate that user has access to the requested project.
        user_org_id = payload.get("org_id")
        if user_org_id:
            try:
                from sqlalchemy import select

                from app.database import async_session
                from app.models.project import Project

                async with async_session() as db:
                    result = await db.execute(
                        select(Project).where(
                            Project.id == project_id,
                            Project.org_id == user_org_id,
                        )
                    )
                    if result.scalar_one_or_none() is None:
                        logger.warning(
                            "WebSocket connection rejected: user org %s has no access to project %s",
                            user_org_id,
                            project_id,
                        )
                        if not already_accepted:
                            await websocket.close(code=4003, reason="Access denied")
                        else:
                            await websocket.close(code=4003, reason="Access denied")
                        return False
            except Exception as exc:
                logger.error("WebSocket project access check failed: %s", exc)
                if not already_accepted:
                    await websocket.close(code=4003, reason="Access check failed")
                else:
                    await websocket.close(code=4003, reason="Access check failed")
                return False

        # Enforce per-project connection limit
        current_count = len(self.connections.get(project_id, []))
        if current_count >= MAX_CONNECTIONS_PER_PROJECT:
            await websocket.close(code=4029, reason="Too many connections")
            return False

        if not already_accepted:
            await websocket.accept()
        self.connections.setdefault(project_id, []).append(websocket)

        # Build per-client state and start heartbeat
        # SECURITY [L-16]: Bind user_id to client state for reconnect token validation.
        _user_id = payload.get("sub", "") if payload else ""
        state = _ClientState(websocket=websocket, project_id=project_id, user_id=_user_id)
        self._client_states[id(websocket)] = state
        state.heartbeat_task = asyncio.create_task(self._heartbeat_loop(websocket, project_id))

        logger.info("WebSocket connected for project %s", project_id)

        # Send the reconnect token to the client so it can use it later
        await websocket.send_json(
            {
                "type": "reconnect_token",
                "token": state.reconnect_token,
            }
        )

        # If client is reconnecting, replay missed messages
        if reconnect_token:
            await self._replay_missed(websocket, reconnect_token)

        return True

    def disconnect(self, websocket: WebSocket, project_id: str):
        """Remove a WebSocket and persist its reconnect token for later use."""
        conns = self.connections.get(project_id, [])
        if websocket in conns:
            conns.remove(websocket)

        state = self._client_states.pop(id(websocket), None)
        if state is not None:
            # Cancel the heartbeat task
            if state.heartbeat_task and not state.heartbeat_task.done():
                state.heartbeat_task.cancel()

            # Persist reconnect token so the client can resume later
            # SECURITY [L-16]: Include user_id so tokens are user-bound.
            current_seq = self._project_sequences.get(project_id, 0)
            self._reconnect_tokens[state.reconnect_token] = (
                project_id,
                current_seq,
                time.monotonic(),
                state.user_id,
            )
            logger.info(
                "WebSocket disconnected for project %s - reconnect token preserved (seq=%d)",
                project_id,
                current_seq,
            )

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------
    async def _heartbeat_loop(self, websocket: WebSocket, project_id: str):
        """Send periodic pings and disconnect unresponsive clients."""
        try:
            while True:
                await asyncio.sleep(HEARTBEAT_INTERVAL)
                try:
                    await websocket.send_json({"type": "ping"})
                except Exception:
                    # Connection already broken
                    self.disconnect(websocket, project_id)
                    return

                # Wait for pong
                state = self._client_states.get(id(websocket))
                if state is None:
                    return

                pong_deadline = time.monotonic() + PONG_TIMEOUT
                state.last_pong = 0  # reset so we can detect a fresh pong

                while time.monotonic() < pong_deadline:
                    if state.last_pong > 0:
                        break
                    await asyncio.sleep(0.5)

                if state.last_pong == 0:
                    logger.warning(
                        "Client did not pong in time for project %s - disconnecting",
                        project_id,
                    )
                    with contextlib.suppress(Exception):
                        await websocket.close(code=4002, reason="Pong timeout")
                    self.disconnect(websocket, project_id)
                    return
        except asyncio.CancelledError:
            return

    def handle_pong(self, websocket: WebSocket):
        """Record that the client responded with a pong.

        Call this from the WebSocket receive loop when a
        ``{"type": "pong"}`` message arrives.
        """
        state = self._client_states.get(id(websocket))
        if state is not None:
            state.last_pong = time.monotonic()

    # ------------------------------------------------------------------
    # Message queuing
    # ------------------------------------------------------------------
    def _ensure_queue(self, project_id: str) -> asyncio.Queue[_QueuedMessage]:
        if project_id not in self._project_queues:
            self._project_queues[project_id] = asyncio.Queue(maxsize=MESSAGE_QUEUE_MAXSIZE)
            self._project_sequences[project_id] = 0
        return self._project_queues[project_id]

    def _enqueue(self, project_id: str, message: dict) -> int:
        """Enqueue *message* for the project and return its sequence number.

        If the queue is full the oldest message is discarded to make room.
        """
        queue = self._ensure_queue(project_id)
        self._project_sequences[project_id] += 1
        seq = self._project_sequences[project_id]
        entry = _QueuedMessage(timestamp=time.monotonic(), message=message, sequence=seq)
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()  # drop oldest
        queue.put_nowait(entry)
        return seq

    async def _replay_missed(self, websocket: WebSocket, reconnect_token: str):
        """Replay messages the client missed while disconnected."""
        token_data = self._reconnect_tokens.pop(reconnect_token, None)
        if token_data is None:
            logger.debug("Reconnect token not found - no replay")
            return

        # SECURITY [L-16]: Reconnect tokens now include user_id (4th element).
        # Handle both old 3-tuple and new 4-tuple formats gracefully.
        if len(token_data) == 4:
            project_id, last_seq, issued_at, token_user_id = token_data
        else:
            project_id, last_seq, issued_at = token_data
            token_user_id = None

        # Verify the reconnect token belongs to the same project as the
        # current connection to prevent cross-project replay attacks.
        state = self._client_states.get(id(websocket))
        if state is not None and state.project_id != project_id:
            logger.warning(
                "Reconnect token project mismatch: token=%s, connection=%s",
                project_id,
                state.project_id,
            )
            return

        # SECURITY [L-16]: Verify the reconnect token belongs to the same user
        # as the current connection to prevent cross-user token theft.
        if token_user_id and state is not None and state.user_id != token_user_id:
            logger.warning(
                "Reconnect token user mismatch: token_user=%s, connection_user=%s",
                token_user_id,
                state.user_id,
            )
            return

        # Check token TTL
        if time.monotonic() - issued_at > RECONNECT_TOKEN_TTL:
            logger.info("Reconnect token expired - no replay")
            return

        queue = self._project_queues.get(project_id)
        if queue is None:
            return

        # Drain the queue into a list so we can iterate, then re-enqueue
        messages: list[_QueuedMessage] = []
        while not queue.empty():
            try:
                messages.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        replayed = 0
        for entry in messages:
            if entry.sequence > last_seq:
                try:
                    await websocket.send_json(
                        {
                            "type": "replay",
                            "sequence": entry.sequence,
                            "data": entry.message,
                        }
                    )
                    replayed += 1
                except Exception:
                    break

        # Put all messages back into the queue (they may be needed by other
        # reconnecting clients).
        for entry in messages:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(entry)

        if replayed:
            logger.info("Replayed %d missed messages for project %s", replayed, project_id)

    # ------------------------------------------------------------------
    # Broadcasting
    # ------------------------------------------------------------------
    async def broadcast(self, project_id: str, message: dict):
        """Broadcast message to all connected clients for a project."""
        # Enqueue for potential replay on reconnect
        self._enqueue(project_id, message)

        conns = self.connections.get(project_id, [])
        disconnected: list[WebSocket] = []
        for ws in conns:
            try:
                await ws.send_json(message)
            except Exception:
                disconnected.append(ws)
        for ws in disconnected:
            self.disconnect(ws, project_id)

    async def broadcast_alert(self, project_id: str, alert: dict):
        """Broadcast a safety alert to all project subscribers."""
        await self.broadcast(
            project_id,
            {
                "type": "safety_alert",
                "data": alert,
            },
        )

    # ------------------------------------------------------------------
    # Maintenance helpers
    # ------------------------------------------------------------------
    def cleanup_expired_tokens(self):
        """Remove reconnect tokens that have exceeded their TTL.

        Call periodically (e.g. from a background task) to prevent unbounded
        growth of ``_reconnect_tokens``.
        """
        now = time.monotonic()
        expired = [
            tok
            for tok, token_data in self._reconnect_tokens.items()
            # Tokens are stored as 4-tuples: (project_id, seq, issued_at, user_id)
            if now - token_data[2] > RECONNECT_TOKEN_TTL
        ]
        for tok in expired:
            del self._reconnect_tokens[tok]
        if expired:
            logger.debug("Cleaned up %d expired reconnect tokens", len(expired))


# Global instance
ws_manager = SafetyWebSocketManager()
