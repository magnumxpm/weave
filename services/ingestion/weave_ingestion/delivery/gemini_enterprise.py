"""Explicit placeholder for an unverified Gemini Enterprise delivery API."""

from weave_common import EnrichedOwnerBundle

from weave_ingestion.delivery.base import Deliverer, MeetingHeader
from weave_ingestion.firestore_client import OnboardedUser


class GeminiEnterpriseDeliverer(Deliverer):
    def deliver(
        self,
        owner_email: str,
        bundle: EnrichedOwnerBundle,
        target: OnboardedUser | None = None,
        meeting: MeetingHeader | None = None,
    ) -> str:
        del owner_email, bundle, target, meeting
        raise NotImplementedError("GE Inbox delivery unverified — see build plan §F")
