ENRICHMENT_PROMPT = """
Enrich the supplied action items for exactly one owner. For each item, call
search_related_context with a focused semantic query based on its description.

The tool deliberately returns up to 20 high-recall candidates. Most may be noise. Keep only a
candidate that bears on this action now. Reject completed or superseded work, a different request
that merely shares people or generic words, and old context whose current state is unknowable.
Use occurred_on relative to the supplied current meeting date, and treat similarity score as one
signal rather than proof. An empty matches list is the expected answer when nothing still matters.

For each item, also write:
- title: one imperative line, no trailing detail, at most 160 characters;
- details: one to three sentences, at most 700 characters.

When kept context exists, details should explain only the prior facts that the reader needs, what
remains outstanding, and what to do. With no kept context, plainly restate the action and add no new
facts. Never state anything absent from the item or a kept match.

If any item reference has status "unknown", do not use that entity, its guessed name, or a pronoun
for it in the title. Rewrite around the entity or omit that clause. A short honest title is better
than a precise-sounding wrong one.

Never invent or alter a match. Never add, remove, duplicate, reword, or transfer the embedded
ActionItem, and keep the owner email exactly as supplied. Return OwnerItemList with matches, title,
and details on each EnrichedActionItem.
""".strip()
