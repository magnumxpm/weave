"""Base contracts for owner-scoped context sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import StrEnum

from pydantic import BaseModel, ConfigDict
from weave_common import ContextMatch


class AuthMode(StrEnum):
    USER_CONTEXT = "user_context"
    SERVICE_ONLY = "service_only"


class SearchPrincipal(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    email: str
    credential_ref: str | None = None


class ContextSource(ABC):
    name: str
    auth_mode: AuthMode

    @abstractmethod
    def search(self, query: str, principal: SearchPrincipal, limit: int = 5) -> list[ContextMatch]:
        """Return results already constrained to the supplied principal."""
