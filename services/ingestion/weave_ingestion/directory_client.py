"""Resolve Meet participant user ids to primary emails via the Directory API."""

from __future__ import annotations

from typing import Any


class DirectoryClient:
    def __init__(self, service: Any) -> None:
        self._service = service

    def email_for_user_id(self, user_id: str) -> str:
        user = self._service.users().get(userKey=user_id, viewType="domain_public").execute()
        return user["primaryEmail"]

    def user_id_for_email(self, email: str) -> str:
        """Numeric Cloud Identity id for an address.

        Chat only accepts an email in `users/{user}` under end-user auth; the
        app-authenticated calls this service makes need the numeric id.
        """
        user = self._service.users().get(userKey=email, viewType="domain_public").execute()
        return user["id"]
