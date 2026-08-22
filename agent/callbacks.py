"""Low-sensitivity observability callbacks for local agents."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def log_enrichment_scope(callback_context: Any) -> None:
    state = callback_context.state
    logger.info(
        "starting owner enrichment",
        extra={
            "owner_email": state.get("owner_email"),
            "item_count": len(state.get("owner_items", [])),
            "conference_record_id": state.get("conference_record_id"),
        },
    )
    return None
