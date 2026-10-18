import collections
import datetime
import logging
from typing import List


class RingBufferLogHandler(logging.Handler):
    """
    In-memory ring buffer log handler that captures recent log messages
    for real-time diagnostic reporting and debugging in containerized setups.
    """

    def __init__(self, capacity: int = 500):
        super().__init__()
        self.capacity = capacity
        self.buffer = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.buffer.append(msg)
        except Exception:
            self.handleError(record)

    def get_logs(self, limit: int = 150) -> List[str]:
        return list(self.buffer)[-limit:]

    def get_text(self, limit: int = 300) -> str:
        logs = self.get_logs(limit=limit)
        return "\n".join(logs) if logs else "(No logs recorded yet)"


# Global ring buffer instance
log_buffer = RingBufferLogHandler(capacity=500)
log_buffer.setFormatter(
    logging.Formatter(
        "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
)

# Attach to root logger so all subsystem logs are captured
root_logger = logging.getLogger()
root_logger.addHandler(log_buffer)
if root_logger.level == logging.NOTSET:
    root_logger.setLevel(logging.INFO)
