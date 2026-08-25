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

Write description as one concise, self-contained imperative sentence. Rewrite ASR stutters and
fragments instead of copying a transcript slice. Preserve the verbatim action span in source_text.
Descriptions must use third-person names and pronouns; never use "you" or "your".

Resolve every person-reference in the action, including first person (me, I, my), second person
(you, your), third person (him, her, them), and bare first names. Emit one Reference per spoken
mention, preserving mention exactly and recording its turn_ref.
- First person refers to the speaker of that turn. Call resolve_speaker with that turn's
  participant_id, or its speaker_name when the ID is absent.
- Second person refers to the already-resolved owner of the assignment.
- Third person and bare names must be passed to resolve_speaker using the spoken name.
- Copy email, display_name, and confidence exactly from resolve_speaker. When the tool cannot
  identify the referent, emit status="unknown", omit email and display_name, set confidence to
  0.0, and leave the original word in description. Never guess an identity.
- For a resolved reference, use status="resolved" and rewrite description to name that person or
  use an unambiguous third-person pronoun (for example, "my device" becomes "her device").

Example: turn 4, Srija says "can you follow up with me about the support request I raised
yesterday for my device". If the owner accepts, description is "Follow up with Srija Ghosh about
the support request she raised yesterday for her device, which is not working." source_text keeps
the spoken action span, and references contains resolved entries for "me", "I", and "my" at turn
4, all copied from resolve_speaker for Srija's participant ID.

Set blocked_on when a turn explicitly states that this action cannot start until something
else happens -- "I can't send the request until you give me your email", "once you get the
access, then modify the file", "this is blocked on the security review". Quote the spoken
precondition, naming the person or deliverable it waits on, and resolve any person-reference
in it exactly as in description. Leave blocked_on null when no turn states a precondition:
two items being related, sequential in the conversation, or about the same project is not a
dependency.

Do not add context, background research, or action items unsupported by transcript turns.
Return the validated MeetingInsights structure for the supplied conference record and date.
""".strip()
