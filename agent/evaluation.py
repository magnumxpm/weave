"""Deterministic local source used only by the ADK leakage eval set."""

from functools import lru_cache

from google.adk.agents import LlmAgent
from weave_common import ContextMatch, MatchType

from agent.agents.enrichment import create_enrichment_agent
from agent.context_sources.base import AuthMode, ContextSource, SearchPrincipal


class EvalContextSource(ContextSource):
    name = "eval_prior_meetings"
    auth_mode = AuthMode.USER_CONTEXT

    def search(self, query: str, principal: SearchPrincipal, limit: int = 5) -> list[ContextMatch]:
        del query, limit
        marker = "ALPHA-OWNER-CONTEXT" if principal.email == "a@example.com" else "BETA-CONTEXT"
        return [
            ContextMatch(
                source_name=self.name,
                match_type=MatchType.EXISTING_PRIOR_ITEM,
                title=marker,
                snippet=marker,
            )
        ]


@lru_cache(maxsize=1)
def get_enrichment_eval_agent() -> LlmAgent:
    return create_enrichment_agent([EvalContextSource()])
