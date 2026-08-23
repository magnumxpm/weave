"""Resolve Meet participant user ids to primary emails via the Directory API."""

from __future__ import annotations

from typing import Any

# `domain_public` resolves only profiles the caller can already see: it needs
# domain contact sharing enabled and otherwise returns the impersonated user
# alone, 403-ing on every colleague. These lookups impersonate `admin_subject`,
# so the admin view is both available and the only one that resolves anyone else.
VIEW_TYPE = "admin_view"


class DirectoryClient:
    def __init__(self, service: Any) -> None:
        self._service = service

    def email_for_user_id(self, user_id: str) -> str:
        user = self._service.users().get(userKey=user_id, viewType=VIEW_TYPE).execute()
        return user["primaryEmail"]

    def user_id_for_email(self, email: str) -> str:
        """Numeric Cloud Identity id for an address.

        Chat only accepts an email in `users/{user}` under end-user auth; the
        app-authenticated calls this service makes need the numeric id.
        """
        user = self._service.users().get(userKey=email, viewType=VIEW_TYPE).execute()
        return user["id"]
