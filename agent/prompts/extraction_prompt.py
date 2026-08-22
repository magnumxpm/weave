EXTRACTION_PROMPT = """
Extract candidate action items from the supplied meeting transcript.

For every proposed owner, call resolve_speaker with the participant ID when available, otherwise
the spoken name. Copy its email and confidence exactly. Never invent an email or confidence.
For each spoken deadline call infer_deadline with the exact phrase and meeting date; if it returns
no value, set deadline to null. Preserve the spoken phrase in deadline_source_text.

Record commitment_turn_ref for the turn where work is proposed and resolution_turn_ref for the
turn that resolves it. Status meanings are strict:
- accepted: explicit acceptance such as "yes, I'll do it"; include its resolution turn.
- declined: explicit refusal such as "no, I can't take that".
- deferred: explicit postponement such as "let's revisit this next sprint".
- reassigned: responsibility moves to a new owner who explicitly accepts it.
- unresolved: an assignment receives no explicit response; silence is never acceptance.

Do not add context, background research, or action items unsupported by transcript turns.
Return the validated MeetingInsights structure for the supplied conference record and date.
""".strip()
