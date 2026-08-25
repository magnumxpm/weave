"""Google Chat delivery using an injected API client and identity resolver."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from weave_common import EnrichedOwnerBundle

from weave_ingestion.delivery.base import Deliverer, MeetingHeader, build_card
from weave_ingestion.firestore_client import OnboardedUser


class ChatDeliverer(Deliverer):
    def __init__(
        self,
        client: Any,
        user_resource_resolver: Callable[[str], str],
        button_url: str = "",
    ) -> None:
        self._client = client
        self._user_resource_resolver = user_resource_resolver
        self._button_url = button_url

    def deliver(
        self,
        owner_email: str,
        bundle: EnrichedOwnerBundle,
        target: OnboardedUser | None = None,
        meeting: MeetingHeader | None = None,
    ) -> str:
        normalized_owner = owner_email.strip().casefold()
        if normalized_owner != bundle.owner_email.strip().casefold():
            raise ValueError("delivery owner does not match bundle owner")

        if target is not None and target.email.strip().casefold() != normalized_owner:
            raise ValueError("delivery target does not match bundle owner")
        if target is not None and target.dm_space:
            space_name = target.dm_space
        else:
            user_resource = self._user_resource_resolver(normalized_owner)
            if not user_resource.startswith("users/"):
                raise ValueError("Chat user resource must use users/{id} format")
            space = self._client.spaces().findDirectMessage(name=user_resource).execute()
            space_name = space["name"]
        response = (
            self._client.spaces()
            .messages()
            .create(
                parent=space_name,
                body={"cardsV2": [build_card(bundle, meeting, button_url=self._button_url)]},
            )
            .execute()
        )
        return str(response["name"])
