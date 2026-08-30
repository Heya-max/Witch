import json
import logging
import sys

import structlog


def configure_logging(level: str = "INFO") -> None:
    """Configure Python's stdlib logging to emit structured JSON lines.

    The app logs with the stdlib ``logging`` API, but consumers want machine-
    readable output (Docker/`docker compose logs`). A ``ProcessorFormatter`` is
    installed on the root handler so every stdlib record — including those
    emitted by third-party libraries — is rendered as a single-line JSON object
    with ISO timestamps. The structlog API remains optional/unused by the app.
    """
    shared_processors = [
        structlog.processors.TimeStamper(fmt="iso"),
    ]

    # Render the record as one JSON line. `log_level`, `event`, `logger` and
    # any extra keys survive; exceptions are captured in `exc_info`.
    renderer = structlog.processors.JSONRenderer(serializer=json.dumps)

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer,
        foreign_pre_chain=[
            structlog.stdlib.add_log_level,
            *shared_processors,
            structlog.processors.format_exc_info,
        ],
    )

    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())

    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
