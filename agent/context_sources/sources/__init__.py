"""Built-in context sources; importing this module registers each one."""

from agent.context_sources.sources.google_docs_source import GoogleDocsSource
from agent.context_sources.sources.google_tasks_source import GoogleTasksSource
from agent.context_sources.sources.prior_meeting_source import PriorMeetingSource

__all__ = ["GoogleDocsSource", "GoogleTasksSource", "PriorMeetingSource"]
