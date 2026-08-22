"""Google Chat delivery using an injected API client and identity resolver."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from weave_common import EnrichedOwnerBundle

from weave_ingestion.delivery.base import Deliverer, build_card


class ChatDeliverer(Deliverer):
    def __init__(
        self,
        client: Any,
        user_resource_resolver: Callable[[str], str],
    ) -> None:
        self._client = client
        self._user_resource_resolver = user_resource_resolver

    def deliver(self, owner_email: str, bundle: EnrichedOwnerBundle) -> str:
        normalized_owner = owner_email.strip().casefold()
        if normalized_owner != bundle.owner_email.strip().casefold():
            raise ValueError("delivery owner does not match bundle owner")

        user_resource = self._user_resource_resolver(normalized_owner)
        if not user_resource.startswith("users/"):
            raise ValueError("Chat user resource must use users/{id} format")
        space = self._client.spaces().findDirectMessage(name=user_resource).execute()
        response = (
            self._client.spaces()
            .messages()
            .create(parent=space["name"], body={"cardsV2": [build_card(bundle)]})
            .execute()
        )
        return str(response["name"])
