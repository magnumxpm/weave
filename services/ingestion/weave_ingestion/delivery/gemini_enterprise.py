"""Explicit placeholder for an unverified Gemini Enterprise delivery API."""

from weave_common import EnrichedOwnerBundle

from weave_ingestion.delivery.base import Deliverer
from weave_ingestion.firestore_client import OnboardedUser


class GeminiEnterpriseDeliverer(Deliverer):
    def deliver(
        self,
        owner_email: str,
        bundle: EnrichedOwnerBundle,
        target: OnboardedUser | None = None,
    ) -> str:
        del owner_email, bundle, target
        raise NotImplementedError("GE Inbox delivery unverified — see build plan §F")
