"""
OpenDB Live Observability & Forensic Tracing System
§1–§52 of Master Directive: Glass-Box Architecture

Hierarchical IDs:
- run_id: Global run execution identifier (RUN-YYYYMMDD-HHMMSS-XXXXXX)
- correlation_id: Correlation across async boundaries
- agent_id: 'AGENT-01' (Lead Discovery) or 'AGENT-02' (Deep Lead Observation) or 'SYSTEM'
- task_id: Identifier of the individual unit of work
- parent_task_id: Parent task ID if spawned from another task
- lead_id: Domain or entity ID being processed
- checkpoint_id: CP-01 through CP-30 logical checkpoint
- event_id: Unique identifier for each log / telemetry event
- parent_event_id: Parent event ID for nested operations

Transports:
- Terminal: Human-readable, timestamped, colorized, aligned
- Rotating Files: logs/opendb.log, logs/opendb_error.log, logs/agent.log, logs/search.log, logs/crawler.log, logs/database.log
- Live Memory Event Bus: For dashboard activity & failure streams (no fake progress)
"""

import os
import sys
import time
import json
import uuid
import logging
import re
import traceback
from datetime import datetime, timezone
from contextvars import ContextVar
from typing import Optional, Dict, Any, List
from logging.handlers import RotatingFileHandler
from enum import Enum

# ─── Checkpoint Definitions ──────────────────────────────────────────────────

class Checkpoint(str, Enum):
    CP01_RUN_INIT = "CP-01"
    CP02_AGENT_INIT = "CP-02"
    CP03_KEYWORD_GEN = "CP-03"
    CP04_SEARCH_EXECUTION = "CP-04"
    CP05_SEARCH_RESULTS = "CP-05"
    CP06_URL_EXTRACTION = "CP-06"
    CP07_DOMAIN_FILTER = "CP-07"
    CP08_LEAD_VALIDATION = "CP-08"
    CP09_DEDUPLICATION = "CP-09"
    CP10_LEAD_CREATION = "CP-10"
    CP11_AGENT1_COMPLETION = "CP-11"

    CP12_AGENT2_INIT = "CP-12"
    CP13_LEAD_ANALYSIS = "CP-13"
    CP14_LEAD_CLASSIFICATION = "CP-14"
    CP15_DEEP_RESEARCH = "CP-15"
    CP16_DOMAIN_VERIFICATION = "CP-16"
    CP17_CRAWL_SCHEDULING = "CP-17"
    CP18_CRAWL_EXECUTION = "CP-18"
    CP19_PAGE_DISCOVERY = "CP-19"
    CP20_CONTENT_EXTRACTION = "CP-20"
    CP21_COMPANY_DATA_EXTRACTION = "CP-21"
    CP22_EVIDENCE_COLLECTION = "CP-22"
    CP23_VALIDATION_GATE = "CP-23"
    CP24_VERIFICATION_GATE = "CP-24"
    CP25_DATABASE_PERSISTENCE = "CP-25"
    CP26_OBJECT_STORAGE = "CP-26"
    CP27_QUEUE_PROCESSING = "CP-27"
    CP28_FRONTEND_EVENT = "CP-28"
    CP29_RUN_COMPLETION = "CP-29"
    CP30_FAILURE_RECOVERY = "CP-30"


# ─── Context Variables for Intra-Process Async / Thread Propagation ───────────

_current_run_id: ContextVar[Optional[str]] = ContextVar("current_run_id", default=None)
_current_correlation_id: ContextVar[Optional[str]] = ContextVar("current_correlation_id", default=None)
_current_agent_id: ContextVar[Optional[str]] = ContextVar("current_agent_id", default="SYSTEM")
_current_task_id: ContextVar[Optional[str]] = ContextVar("current_task_id", default=None)
_current_parent_task_id: ContextVar[Optional[str]] = ContextVar("current_parent_task_id", default=None)
_current_lead_id: ContextVar[Optional[str]] = ContextVar("current_lead_id", default=None)
_current_checkpoint: ContextVar[Optional[str]] = ContextVar("current_checkpoint", default=None)
_current_parent_event_id: ContextVar[Optional[str]] = ContextVar("current_parent_event_id", default=None)


# ─── Secret Redaction ─────────────────────────────────────────────────────────

SECRET_PATTERNS = [
    re.compile(r'(password[:=]\s*["\']?)([^"\'\s&]+)(["\']?)', re.IGNORECASE),
    re.compile(r'(secret[:=]\s*["\']?)([^"\'\s&]+)(["\']?)', re.IGNORECASE),
    re.compile(r'(api[_-]?key[:=]\s*["\']?)([^"\'\s&]+)(["\']?)', re.IGNORECASE),
    re.compile(r'(token[:=]\s*["\']?)([^"\'\s&]+)(["\']?)', re.IGNORECASE),
    re.compile(r'(auth[:=]\s*["\']?Bearer\s+)([^"\'\s&]+)(["\']?)', re.IGNORECASE),
    re.compile(r'(postgresql:\/\/[^:]+:)([^@]+)(@)', re.IGNORECASE),
    re.compile(r'(redis:\/\/:)([^@]+)(@)', re.IGNORECASE),
]

def sanitize_secrets(msg: str) -> str:
    if not isinstance(msg, str):
        return str(msg)
    cleaned = msg
    for pattern in SECRET_PATTERNS:
        cleaned = pattern.sub(r'\1***REDACTED***\3', cleaned)
    return cleaned


# ─── Formatter ───────────────────────────────────────────────────────────────

class OpenDBTerminalFormatter(logging.Formatter):
    """
    Format:
    HH:MM:SS.mmm | LEVEL | RUN_ID | AGENT_ID | CHECKPOINT | EVENT | details...
    """
    COLORS = {
        "DEBUG": "\033[36m",    # Cyan
        "INFO": "\033[32m",     # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",    # Red
        "CRITICAL": "\033[41m\033[37m", # Red background
        "RESET": "\033[0m"
    }

    def format(self, record: logging.LogRecord) -> str:
        # Extract trace context from record or contextvars
        run_id = getattr(record, "run_id", None) or _current_run_id.get() or "RUN-NONE"
        agent_id = getattr(record, "agent_id", None) or _current_agent_id.get() or "SYSTEM"
        checkpoint = getattr(record, "checkpoint", None) or _current_checkpoint.get() or "CP-XX"
        event = getattr(record, "event", None) or record.funcName
        task_id = getattr(record, "task_id", None) or _current_task_id.get()
        lead_id = getattr(record, "lead_id", None) or _current_lead_id.get()
        event_id = getattr(record, "event_id", None)

        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        ts_str = dt.strftime("%H:%M:%S.%f")[:-3]

        level = record.levelname
        msg = sanitize_secrets(record.getMessage())

        # Include lead_id or task_id if present
        extra_ctx = []
        if lead_id:
            extra_ctx.append(f"lead={lead_id}")
        if task_id:
            extra_ctx.append(f"task={task_id}")
        if event_id:
            extra_ctx.append(f"evt={event_id[:8]}")
        ctx_str = f" [{', '.join(extra_ctx)}]" if extra_ctx else ""

        # Terminal colorization if terminal supports it
        use_color = hasattr(sys.stderr, "isatty") and sys.stderr.isatty()
        c_start = self.COLORS.get(level, "") if use_color else ""
        c_end = self.COLORS["RESET"] if use_color else ""

        line = f"{ts_str} | {c_start}{level:<7}{c_end} | {run_id} | {agent_id:<8} | {checkpoint:<5} | {event}{ctx_str} | {msg}"

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            line += f"\n{record.exc_text}"
        return line


class OpenDBFileFormatter(logging.Formatter):
    """Clean uncolored text log format for rotating files."""
    def format(self, record: logging.LogRecord) -> str:
        run_id = getattr(record, "run_id", None) or _current_run_id.get() or "RUN-NONE"
        agent_id = getattr(record, "agent_id", None) or _current_agent_id.get() or "SYSTEM"
        checkpoint = getattr(record, "checkpoint", None) or _current_checkpoint.get() or "CP-XX"
        event = getattr(record, "event", None) or record.funcName
        task_id = getattr(record, "task_id", None) or _current_task_id.get() or ""
        lead_id = getattr(record, "lead_id", None) or _current_lead_id.get() or ""
        event_id = getattr(record, "event_id", None) or ""
        parent_event_id = getattr(record, "parent_event_id", None) or _current_parent_event_id.get() or ""

        dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
        ts_str = dt.isoformat()
        msg = sanitize_secrets(record.getMessage())

        line = f"{ts_str} | {record.levelname:<7} | {run_id} | {agent_id} | {checkpoint} | {event} | lead={lead_id} | task={task_id} | evt={event_id} | parent_evt={parent_event_id} | {msg}"

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            line += f"\n{record.exc_text}"
        return line


# ─── Live Telemetry Buffer for Dashboard Operations View ───────────────────────

class LiveTelemetryBuffer:
    """Thread-safe circular ring buffer for real operational events."""
    def __init__(self, max_size: int = 200):
        self.max_size = max_size
        self._events: List[Dict[str, Any]] = []

    def append(self, event: Dict[str, Any]):
        self._events.append(event)
        if len(self._events) > self.max_size:
            self._events.pop(0)

    def get_recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        return list(reversed(self._events[-limit:]))

live_telemetry = LiveTelemetryBuffer(max_size=300)


# ─── Tracer Engine ────────────────────────────────────────────────────────────

class Tracer:
    """
    OpenDB Centralized Tracing & Glass-Box Observability Engine.
    """
    def __init__(self):
        self.log_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "logs"))
        os.makedirs(self.log_dir, exist_ok=True)
        self.initialized = False
        self.logger = logging.getLogger("opendb")

    def initialize(self, log_level: str = "DEBUG"):
        if self.initialized:
            return
        os.makedirs(self.log_dir, exist_ok=True)

        # Root OpenDB logger
        self.logger.setLevel(getattr(logging, log_level.upper(), logging.DEBUG))
        self.logger.propagate = False
        self.logger.handlers.clear()

        # 1. Console / Terminal Handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(getattr(logging, log_level.upper(), logging.DEBUG))
        console_handler.setFormatter(OpenDBTerminalFormatter())
        self.logger.addHandler(console_handler)

        # 2. Main Rotating File Handler (logs/opendb.log)
        opendb_log_path = os.path.join(self.log_dir, "opendb.log")
        file_handler = RotatingFileHandler(
            opendb_log_path, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(OpenDBFileFormatter())
        self.logger.addHandler(file_handler)

        # 3. Error Rotating File Handler (logs/opendb_error.log)
        error_log_path = os.path.join(self.log_dir, "opendb_error.log")
        err_handler = RotatingFileHandler(
            error_log_path, maxBytes=20 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        err_handler.setLevel(logging.ERROR)
        err_handler.setFormatter(OpenDBFileFormatter())
        self.logger.addHandler(err_handler)

        # 4. Component-specific logs
        self._add_file_handler("agent", "agent.log")
        self._add_file_handler("search", "search.log")
        self._add_file_handler("crawler", "crawler.log")
        self._add_file_handler("database", "database.log")

        self.initialized = True
        self.log_event(
            level="INFO",
            checkpoint=Checkpoint.CP01_RUN_INIT,
            event="SYSTEM_INIT",
            message=f"OpenDB Observability & Tracing Engine Initialized. Log directory: {self.log_dir}"
        )

    def _add_file_handler(self, logger_suffix: str, filename: str):
        sub_logger = logging.getLogger(f"opendb.{logger_suffix}")
        sub_logger.setLevel(logging.DEBUG)
        sub_logger.propagate = True
        path = os.path.join(self.log_dir, filename)
        handler = RotatingFileHandler(path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(OpenDBFileFormatter())
        sub_logger.addHandler(handler)

    # ── Context Management ────────────────────────────────────────────────────

    def get_run_id(self) -> str:
        return _current_run_id.get() or "RUN-NONE"

    def new_run_id(self) -> str:
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        short_u = uuid.uuid4().hex[:6].upper()
        rid = f"RUN-{ts}-{short_u}"
        _current_run_id.set(rid)
        _current_correlation_id.set(f"CORR-{uuid.uuid4().hex[:8].upper()}")
        return rid

    def set_context(
        self,
        run_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
        agent_id: Optional[str] = None,
        task_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
        lead_id: Optional[str] = None,
        checkpoint: Optional[str] = None,
        parent_event_id: Optional[str] = None,
    ):
        if run_id:
            _current_run_id.set(run_id)
        if correlation_id:
            _current_correlation_id.set(correlation_id)
        if agent_id:
            _current_agent_id.set(agent_id)
        if task_id:
            _current_task_id.set(task_id)
        if parent_task_id:
            _current_parent_task_id.set(parent_task_id)
        if lead_id:
            _current_lead_id.set(lead_id)
        if checkpoint:
            _current_checkpoint.set(checkpoint)
        if parent_event_id:
            _current_parent_event_id.set(parent_event_id)

    def get_context_dict(self) -> Dict[str, Any]:
        """Serialize current trace context into dictionary for Celery/Thread boundary crossing."""
        return {
            "run_id": _current_run_id.get(),
            "correlation_id": _current_correlation_id.get(),
            "agent_id": _current_agent_id.get() or "SYSTEM",
            "task_id": _current_task_id.get(),
            "parent_task_id": _current_parent_task_id.get(),
            "lead_id": _current_lead_id.get(),
            "checkpoint": _current_checkpoint.get(),
            "parent_event_id": _current_parent_event_id.get(),
        }

    def restore_context_dict(self, ctx: Optional[Dict[str, Any]]):
        """Restore trace context from serialized dictionary."""
        if not ctx or not isinstance(ctx, dict):
            return
        self.set_context(
            run_id=ctx.get("run_id"),
            correlation_id=ctx.get("correlation_id"),
            agent_id=ctx.get("agent_id"),
            task_id=ctx.get("task_id"),
            parent_task_id=ctx.get("parent_task_id"),
            lead_id=ctx.get("lead_id"),
            checkpoint=ctx.get("checkpoint"),
            parent_event_id=ctx.get("parent_event_id"),
        )

    # ── Event Logging ─────────────────────────────────────────────────────────

    def log_event(
        self,
        level: str,
        checkpoint: Checkpoint,
        event: str,
        message: str,
        agent_id: Optional[str] = None,
        lead_id: Optional[str] = None,
        task_id: Optional[str] = None,
        parent_task_id: Optional[str] = None,
        parent_event_id: Optional[str] = None,
        duration: Optional[float] = None,
        status: Optional[str] = None,
        extra: Optional[Dict[str, Any]] = None,
        exc_info: Any = None,
    ) -> str:
        """
        Emit a structured event across terminal, files, and live buffer.
        Returns the unique event_id.
        """
        event_id = f"EVT-{uuid.uuid4().hex[:8].upper()}"
        active_agent = agent_id or _current_agent_id.get() or "SYSTEM"
        active_run = _current_run_id.get() or "RUN-NONE"
        active_lead = lead_id or _current_lead_id.get()
        active_task = task_id or _current_task_id.get()
        active_parent_event = parent_event_id or _current_parent_event_id.get()

        cp_val = checkpoint.value if isinstance(checkpoint, Checkpoint) else str(checkpoint)

        log_data = {
            "run_id": active_run,
            "agent_id": active_agent,
            "checkpoint": cp_val,
            "event": event,
            "event_id": event_id,
            "parent_event_id": active_parent_event,
            "lead_id": active_lead,
            "task_id": active_task,
        }

        # Build message payload
        msg_parts = [message]
        if duration is not None:
            msg_parts.append(f"duration={duration:.3f}s")
        if status:
            msg_parts.append(f"status={status}")
        if extra:
            sanitized_extra = {k: sanitize_secrets(str(v)) for k, v in extra.items()}
            msg_parts.append(f"details={json.dumps(sanitized_extra)}")

        full_msg = " | ".join(msg_parts)

        # Log via standard library logger
        log_method = getattr(self.logger, level.lower(), self.logger.info)
        log_method(full_msg, extra=log_data, exc_info=exc_info)

        # Append to live telemetry buffer for dashboard stream
        telemetry_item = {
            "event_id": event_id,
            "parent_event_id": active_parent_event,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "run_id": active_run,
            "agent_id": active_agent,
            "checkpoint": cp_val,
            "event": event,
            "lead_id": active_lead,
            "task_id": active_task,
            "message": sanitize_secrets(message),
            "duration": duration,
            "status": status,
            "extra": extra or {}
        }
        live_telemetry.append(telemetry_item)

        return event_id

    # ── Checkpoint Scoped Context Manager ──────────────────────────────────────

    class CheckpointScope:
        def __init__(
            self,
            tracer: "Tracer",
            checkpoint: Checkpoint,
            name: str,
            agent_id: Optional[str] = None,
            lead_id: Optional[str] = None,
            task_id: Optional[str] = None,
            details: Optional[Dict[str, Any]] = None,
        ):
            self.tracer = tracer
            self.checkpoint = checkpoint
            self.name = name
            self.agent_id = agent_id or _current_agent_id.get() or "SYSTEM"
            self.lead_id = lead_id or _current_lead_id.get()
            self.task_id = task_id or _current_task_id.get()
            self.details = details or {}
            self.start_time = 0.0
            self.start_event_id = ""

        def __enter__(self):
            self.start_time = time.time()
            self.start_event_id = self.tracer.log_event(
                level="INFO",
                checkpoint=self.checkpoint,
                event=f"{self.name}_START",
                message=f"Entering {self.checkpoint.value} {self.name}",
                agent_id=self.agent_id,
                lead_id=self.lead_id,
                task_id=self.task_id,
                extra=self.details
            )
            # Push parent event ID
            _current_parent_event_id.set(self.start_event_id)
            _current_checkpoint.set(self.checkpoint.value)
            return self

        def __exit__(self, exc_type, exc_val, exc_tb):
            duration = time.time() - self.start_time
            if exc_type is not None:
                self.tracer.log_event(
                    level="ERROR",
                    checkpoint=self.checkpoint,
                    event=f"{self.name}_FAILED",
                    message=f"Failed {self.checkpoint.value} {self.name}: {exc_val}",
                    agent_id=self.agent_id,
                    lead_id=self.lead_id,
                    task_id=self.task_id,
                    parent_event_id=self.start_event_id,
                    duration=duration,
                    status="ERROR",
                    extra={"error_type": exc_type.__name__, "error": str(exc_val)},
                    exc_info=(exc_type, exc_val, exc_tb)
                )
                # Don't suppress exception
                return False
            else:
                self.tracer.log_event(
                    level="INFO",
                    checkpoint=self.checkpoint,
                    event=f"{self.name}_END",
                    message=f"Completed {self.checkpoint.value} {self.name}",
                    agent_id=self.agent_id,
                    lead_id=self.lead_id,
                    task_id=self.task_id,
                    parent_event_id=self.start_event_id,
                    duration=duration,
                    status="SUCCESS",
                    extra=self.details
                )
                return False

    def checkpoint(
        self,
        checkpoint: Checkpoint,
        name: str,
        agent_id: Optional[str] = None,
        lead_id: Optional[str] = None,
        task_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
    ) -> "CheckpointScope":
        return self.CheckpointScope(
            tracer=self,
            checkpoint=checkpoint,
            name=name,
            agent_id=agent_id,
            lead_id=lead_id,
            task_id=task_id,
            details=details,
        )


tracer = Tracer()
tracer.initialize()
