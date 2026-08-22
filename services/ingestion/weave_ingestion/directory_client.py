"""Resolve Meet participant user ids to primary emails via the Directory API."""

from __future__ import annotations

from typing import Any


class DirectoryClient:
    def __init__(self, service: Any) -> None:
        self._service = service

    def email_for_user_id(self, user_id: str) -> str:
        user = self._service.users().get(userKey=user_id, viewType="domain_public").execute()
        return user["primaryEmail"]
