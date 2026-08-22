"""Card delivery to logs: the smoke-test surface before Chat is verified."""

from __future__ import annotations

import json
import logging

from weave_common import EnrichedOwnerBundle

from weave_ingestion.delivery.base import Deliverer, build_card
from weave_ingestion.firestore_client import OnboardedUser

logger = logging.getLogger(__name__)


class LogDeliverer(Deliverer):
    def deliver(
        self,
        owner_email: str,
        bundle: EnrichedOwnerBundle,
        target: OnboardedUser | None = None,
    ) -> str:
        del target
        card = build_card(bundle)
        logger.info("card rendered", extra={"owner_email": owner_email, "card": json.dumps(card)})
        return f"log:{owner_email}"
