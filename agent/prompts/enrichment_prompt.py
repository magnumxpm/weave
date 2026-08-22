ENRICHMENT_PROMPT = """
Enrich the supplied action items for exactly one owner. For each item, call
search_related_context with a focused query based on its description. Keep an empty matches list
when nothing is found. Never add, remove, duplicate, reword, or transfer an action item, and keep
the owner email exactly as supplied. Return OwnerItemList.
""".strip()
