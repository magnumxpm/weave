"""Explicit placeholder for an unverified Gemini Enterprise delivery API."""

from weave_common import EnrichedOwnerBundle

from weave_ingestion.delivery.base import Deliverer


class GeminiEnterpriseDeliverer(Deliverer):
    def deliver(self, owner_email: str, bundle: EnrichedOwnerBundle) -> str:
        del owner_email, bundle
        raise NotImplementedError("GE Inbox delivery unverified — see build plan §F")
