ENRICHMENT_PROMPT = """
Enrich the supplied action items for exactly one owner. For each item, call
search_related_context with a focused query based on its description.

The tool ranks candidates by word overlap, not by meaning, so judge every candidate yourself.
Keep only those about the same work, the same request, or the same topic as the item. Sharing a
common verb such as "email", "send", or "follow up" is not a relationship, and neither is sharing
a participant. When nothing genuinely relates, keep an empty matches list: empty is the expected
answer, not a failure.

Never invent a match, and never alter one you were given. Never add, remove, duplicate, reword,
or transfer an action item, and keep the owner email exactly as supplied. Return OwnerItemList.
""".strip()
