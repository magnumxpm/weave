"""Hand-laid brand illustrations for the architecture PDF.

Geometry is computed rather than hand-written so every box lands on the same
grid and every connector meets an edge at a right angle. Each figure declares
its own viewBox in CSS pixels at roughly the size it is printed, so the text
inside a figure ends up the same size as the text around it.
"""

from __future__ import annotations

INK = "#0C1733"
MUTED = "#5A6E93"
BLUE = "#2573FE"
BLUE_SOFT = "#EDF3FF"
BORDER = "#D8E3F8"
PANEL = "#FAFBFF"
LINE = "#8AA0C6"
AMBER = "#E8A400"
GREEN = "#1E9E62"
FONT = "'Inter','Helvetica Neue',Helvetica,Arial,sans-serif"

HEAD = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
    'width="{w}" height="{h}" font-family="{font}">'
    '<defs>'
    '<marker id="ar" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
    'markerHeight="7" orient="auto-start-reverse">'
    '<path d="M0,1 L9,5 L0,9 z" fill="{line}"/></marker>'
    '<marker id="arb" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
    'markerHeight="7" orient="auto-start-reverse">'
    '<path d="M0,1 L9,5 L0,9 z" fill="{blue}"/></marker>'
    '</defs>'
)


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def panel(x, y, w, h, label, *, fill=PANEL, stroke=BORDER, dash=None, label_x=14):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    out = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" '
        f'stroke="{stroke}"{d}/>'
    )
    if label:
        out += (
            f'<text x="{x + label_x}" y="{y + 19}" font-size="10" font-weight="600" '
            f'letter-spacing="0.9" fill="{MUTED}">{esc(label.upper())}</text>'
        )
    return out


def box(x, y, w, h, lines, *, accent=False, sub=None, fill=None, stroke=None):
    fill = fill or ("#FFFFFF" if not accent else BLUE_SOFT)
    stroke = stroke or (BORDER if not accent else "#AFC8FB")
    width = 2 if accent else 1
    out = (
        f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{width}"/>'
    )
    if isinstance(lines, str):
        lines = [lines]
    total = len(lines) + (1 if sub else 0)
    start = y + h / 2 - (total - 1) * 7 + 4
    for i, line in enumerate(lines):
        weight = 600 if i == 0 else 400
        colour = INK if i == 0 else MUTED
        out += (
            f'<text x="{x + w / 2}" y="{start + i * 14}" text-anchor="middle" '
            f'font-size="11.5" font-weight="{weight}" fill="{colour}">{esc(line)}</text>'
        )
    if sub:
        out += (
            f'<text x="{x + w / 2}" y="{start + len(lines) * 14}" text-anchor="middle" '
            f'font-size="9.5" fill="{BLUE}" font-weight="600" letter-spacing="0.4">'
            f'{esc(sub.upper())}</text>'
        )
    return out


def arrow(points, *, blue=False, dashed=False, label=None, label_at=0.5, label_dy=-6,
          label_anchor="middle", label_pos=None, halo=False, label_rotate=0):
    """One orthogonal connector.

    `halo` paints a white casing under the line first, so a connector drawn
    later crosses earlier ones with a clean gap instead of a collision.
    """
    path = " ".join(f"{'M' if i == 0 else 'L'}{x},{y}" for i, (x, y) in enumerate(points))
    colour = BLUE if blue else LINE
    dash = ' stroke-dasharray="4 4"' if dashed else ""
    marker = "arb" if blue else "ar"
    out = ""
    if halo:
        out += (f'<path d="{path}" fill="none" stroke="#FFFFFF" stroke-width="5" '
                f'stroke-linejoin="round"/>')
    out += (
        f'<path d="{path}" fill="none" stroke="{colour}" stroke-width="1.4"'
        f'{dash} marker-end="url(#{marker})" stroke-linejoin="round"/>'
    )
    if label and label_pos is not None:
        lx, ly = label_pos
        w = len(label) * 4.6 + 8
        anchor_x = {"middle": lx - w / 2, "start": lx - 4, "end": lx - w + 4}[label_anchor]
        group = f'<g transform="rotate({label_rotate} {lx} {ly})">' if label_rotate else "<g>"
        out += (
            group
            + f'<rect x="{anchor_x}" y="{ly - 9}" width="{w}" height="13" rx="3" '
              f'fill="#FFFFFF" opacity="0.94"/>'
              f'<text x="{lx}" y="{ly + 1}" text-anchor="{label_anchor}" font-size="9" '
              f'fill="{MUTED}">{esc(label)}</text></g>'
        )
        return out
    if label:
        # place the label on the longest segment, at label_at along it
        best, blen = None, -1
        for a, b in zip(points, points[1:]):
            length = abs(b[0] - a[0]) + abs(b[1] - a[1])
            if length > blen:
                best, blen = (a, b), length
        (ax, ay), (bx, by) = best
        lx = ax + (bx - ax) * label_at
        ly = ay + (by - ay) * label_at + label_dy
        w = len(label) * 4.6 + 8
        anchor_x = {"middle": lx - w / 2, "start": lx - 4, "end": lx - w + 4}[label_anchor]
        out += (
            f'<rect x="{anchor_x}" y="{ly - 9}" width="{w}" height="13" rx="3" '
            f'fill="#FFFFFF" opacity="0.92"/>'
            f'<text x="{lx}" y="{ly + 1}" text-anchor="{label_anchor}" font-size="9" '
            f'fill="{MUTED}">{esc(label)}</text>'
        )
    return out


def step(n, x, y):
    return (
        f'<circle cx="{x}" cy="{y}" r="9" fill="{BLUE}"/>'
        f'<text x="{x}" y="{y + 3.6}" text-anchor="middle" font-size="10" '
        f'font-weight="700" fill="#FFFFFF">{n}</text>'
    )


# --------------------------------------------------------------------------
# Figure 1 — the system map
# --------------------------------------------------------------------------
def system_map() -> str:
    """Zones read top to bottom; every connector runs on its own lane.

    Chat sits left and Meet centre so the two long returns -- the card going
    back to the DM, and the delegated read going out to Drive -- can each use
    an outside lane without crossing the columns they pass.
    """
    W, H = 660, 620
    s = HEAD.format(w=W, h=H, font=FONT, line=LINE, blue=BLUE)

    s += panel(14, 14, W - 28, 104, "Google Workspace")
    s += panel(14, 146, W - 28, 446, "Google Cloud \u2014 one project", label_x=42)

    s += box(44, 48, 168, 52, ["Google Chat", "one DM: install,", "card, question"])
    s += box(246, 48, 168, 52, ["Google Meet", "transcript", "per-user subscription"])
    s += box(448, 48, 168, 52, ["Drive \u00b7 Docs \u00b7 Tasks", "Directory", "read-only"])

    s += box(44, 184, 168, 46, ["Cloud Run \u00b7 weave-chat", "answers clicks inline"])
    s += box(246, 184, 168, 46, ["Pub/Sub", "meet-artifacts"])
    s += box(448, 184, 168, 46, ["Cloud Run job", "subscription-manager"])
    s += box(44, 252, 168, 34, ["Pub/Sub \u00b7 chat-events"])
    s += box(448, 252, 168, 40, ["Model Armor", "transcripts in, answers out"])

    s += box(160, 318, 340, 60,
             ["Cloud Run \u00b7 weave-ingestion",
              "screen \u00b7 pipeline \u00b7 persist \u00b7 reconcile \u00b7 deliver"],
             accent=True, sub="the only broad delegation")

    s += panel(44, 412, 572, 112,
               "Vertex AI Agent Engine \u2014 no delegation, ever", fill="#FFFFFF")
    s += box(60, 444, 260, 60,
             ["Pipeline engine", "extraction + enrichment", "two phases, one call"])
    s += box(340, 444, 260, 60,
             ["Copilot engine", "12 principal-scoped tools", "read + lifecycle"])

    s += box(160, 552, 340, 30, ["Firestore \u2014 Weave's own derived state"])

    # down the columns
    s += arrow([(128, 100), (128, 184)])
    s += arrow([(128, 230), (128, 252)])
    s += arrow([(128, 286), (128, 348), (160, 348)], blue=True, label="OIDC push",
               label_pos=(128, 322))
    s += arrow([(300, 100), (300, 184)])
    s += arrow([(330, 230), (330, 318)], blue=True, label="OIDC push", label_pos=(330, 280))
    s += arrow([(470, 318), (470, 276), (448, 276)], label="screen", label_pos=(496, 312))

    # into the engines, and back
    s += arrow([(230, 378), (230, 444)], blue=True, label="run_pipeline", label_pos=(230, 414))
    s += arrow([(300, 444), (300, 398), (352, 398), (352, 378)], label="context broker",
               label_pos=(326, 394))
    s += arrow([(430, 378), (430, 444)], blue=True, label="ask, as this principal",
               label_pos=(430, 414))
    s += arrow([(190, 504), (190, 552)])
    s += arrow([(470, 504), (470, 552)])

    # the two long returns, each on an outside lane
    s += arrow([(500, 348), (632, 348), (632, 124), (580, 124), (580, 100)], blue=True,
               halo=True, label="delegated reads, as the owner", label_pos=(628, 246),
               label_rotate=-90)
    s += arrow([(160, 332), (28, 332), (28, 124), (120, 124), (120, 100)], blue=True,
               halo=True, label="card \u00b7 reply", label_pos=(76, 133))
    s += arrow([(532, 184), (532, 140), (380, 140), (380, 100)], halo=True,
               label="per-user subscriptions", label_pos=(456, 136))

    s += (f'<line x1="44" y1="600" x2="120" y2="600" stroke="{BLUE}" stroke-width="1.4"/>'
          f'<text x="128" y="604" font-size="9.5" fill="{MUTED}">authenticated hop (OIDC), '
          f'or a delegated read</text>'
          f'<line x1="404" y1="600" x2="480" y2="600" stroke="{LINE}" stroke-width="1.4"/>'
          f'<text x="488" y="604" font-size="9.5" fill="{MUTED}">internal call</text>')
    return s + "</svg>"


# --------------------------------------------------------------------------
# Figure 2 — the capability boundary
# --------------------------------------------------------------------------
def capability_boundary() -> str:
    W, H = 660, 244
    s = HEAD.format(w=W, h=H, font=FONT, line=LINE, blue=BLUE)

    s += panel(10, 10, 296, 224, "Phase 1 \u00b7 extraction", fill="#FFFFFF")
    s += panel(354, 10, 296, 224, "Phase 2 \u00b7 enrichment", fill="#FFFFFF")

    left = [
        (True, "Every attendee's words"),
        (True, "resolve_speaker \u00b7 infer_deadline"),
        (False, "Any context tool at all"),
        (False, "Anyone's files, tasks or history"),
    ]
    right = [
        (True, "One owner's items, and only those"),
        (True, "search_related_context, as them"),
        (True, "That owner's own Drive, Tasks, history"),
        (False, "Anybody else's anything"),
    ]
    for x0, rows in ((26, left), (370, right)):
        y = 62
        for present, label in rows:
            mark, colour, bg = ("\u2713", GREEN, "#EAF7F0") if present else ("\u2715", "#C8402F", "#FDEDEA")
            s += f'<circle cx="{x0 + 9}" cy="{y - 4}" r="9" fill="{bg}"/>'
            s += (f'<text x="{x0 + 9}" y="{y}" text-anchor="middle" font-size="10.5" '
                  f'font-weight="700" fill="{colour}">{mark}</text>')
            s += f'<text x="{x0 + 26}" y="{y}" font-size="11" fill="{INK}">{esc(label)}</text>'
            y += 32

    s += (f'<line x1="330" y1="26" x2="330" y2="218" stroke="{BLUE}" stroke-width="1.4" '
          f'stroke-dasharray="5 5"/>')
    s += (f'<g transform="rotate(-90 330 122)">'
          f'<rect x="252" y="113" width="156" height="18" rx="9" fill="#FFFFFF"/>'
          f'<text x="330" y="126" text-anchor="middle" font-size="9.5" font-weight="700" '
          f'letter-spacing="1.1" fill="{BLUE}">A NEW SESSION EACH TIME</text></g>')

    for cx, small, big in (
        (158, "one session for the whole meeting", "sees everything, reaches nothing"),
        (502, "a fresh session for each owner", "reaches one person, sees only them"),
    ):
        s += (f'<text x="{cx}" y="188" text-anchor="middle" font-size="10" fill="{MUTED}">'
              f'{esc(small)}</text>')
        s += (f'<text x="{cx}" y="210" text-anchor="middle" font-size="11" font-weight="700" '
              f'fill="{INK}">{esc(big)}</text>')
    return s + "</svg>"


# --------------------------------------------------------------------------
# Figure 3 — the urgency ladder
# --------------------------------------------------------------------------
def urgency_ladder() -> str:
    W, H = 660, 190
    s = HEAD.format(w=W, h=H, font=FONT, line=LINE, blue=BLUE)
    groups = [
        ("overdue", "#C8402F", "past its date"),
        ("due_soon", "#E8760D", "within 3 days"),
        ("blocking", "#E8A400", "others wait on it"),
        ("waiting", "#3E8BD6", "you wait on someone"),
        ("stale", "#6E7DA8", "quiet 14 days"),
        ("active", "#2573FE", "in flight"),
        ("likely_complete", "#1E9E62", "evidence, not proof"),
        ("closed", "#9AA8C4", "a human said so"),
    ]
    x = 14
    w = (W - 28 - 7 * 6) / 8
    for i, (name, colour, note) in enumerate(groups):
        h = 78 - i * 6
        y = 116 - h
        s += (f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="{h}" rx="7" '
              f'fill="{colour}" opacity="0.14"/>')
        s += (f'<rect x="{x:.1f}" y="{y}" width="{w:.1f}" height="4" rx="2" fill="{colour}"/>')
        fs = min(10.0, (w - 6) * 1.85 / max(len(name), 1))
        s += (f'<text x="{x + w / 2:.1f}" y="{y + h / 2 + 4}" text-anchor="middle" '
              f'font-size="{fs:.1f}" font-weight="700" fill="{INK}">{esc(name)}</text>')
        s += (f'<text x="{x + w / 2:.1f}" y="136" text-anchor="middle" font-size="8.5" '
              f'fill="{MUTED}">{esc(note)}</text>')
        x += w + 6
    s += (f'<line x1="14" y1="150" x2="{W - 14}" y2="150" stroke="{BORDER}"/>')
    s += (f'<text x="14" y="170" font-size="10.5" font-weight="600" fill="{INK}">'
          'Declaration order is display order</text>')
    s += (f'<text x="{W - 14}" y="170" text-anchor="end" font-size="10.5" fill="{MUTED}">'
          'attention score ranks within a group</text>')
    return s + "</svg>"


# --------------------------------------------------------------------------
# Figure 4 — the context framework
# --------------------------------------------------------------------------
def context_framework() -> str:
    W, H = 660, 348
    s = HEAD.format(w=W, h=H, font=FONT, line=LINE, blue=BLUE)

    s += box(14, 128, 150, 62, ["search_related_context",
                                "the one context tool"], accent=True)
    s += box(200, 128, 120, 62, ["registry", "search_all()"])

    cards = [
        (356, 14, "prior_meetings", "Firestore \u00b7 action_items"),
        (356, 84, "meeting_summaries", "Firestore \u00b7 summaries"),
        (356, 186, "google_docs", "Drive, via the broker"),
        (356, 256, "google_tasks", "Tasks, via the broker"),
    ]
    for x, y, name, backend in cards:
        s += box(x, y, 150, 54, [name, backend])

    s += box(536, 24, 110, 74, ["visible_to", "array_contains", "in the query"],
             fill="#F2FBF6", stroke="#BEE6D0")
    s += box(536, 216, 110, 74, ["POST", "/context/search", "read as the owner"],
             fill="#F2FBF6", stroke="#BEE6D0")

    s += arrow([(164, 159), (200, 159)])
    for _, y, _, _ in cards:
        s += arrow([(320, 159), (338, 159), (338, y + 27), (356, y + 27)])
    s += arrow([(506, 41), (536, 41)])
    s += arrow([(506, 111), (520, 111), (520, 61), (536, 61)])
    s += arrow([(506, 213), (520, 213), (520, 253), (536, 253)])
    s += arrow([(506, 283), (536, 283)])

    s += (f'<text x="591" y="118" text-anchor="middle" font-size="9.5" fill="{MUTED}">'
          f'the database refuses</text>'
          f'<text x="591" y="130" text-anchor="middle" font-size="9.5" fill="{MUTED}">'
          f'what it cannot show</text>')
    s += (f'<text x="591" y="310" text-anchor="middle" font-size="9.5" fill="{MUTED}">'
          f'ingestion holds the</text>'
          f'<text x="591" y="322" text-anchor="middle" font-size="9.5" fill="{MUTED}">'
          f'delegation, the agent never does</text>')

    s += (f'<rect x="14" y="216" width="306" height="118" rx="10" fill="#FFFFFF" '
          f'stroke="{BORDER}"/>')
    s += (f'<text x="30" y="240" font-size="10" font-weight="600" letter-spacing="0.9" '
          f'fill="{MUTED}">WHAT THE REGISTRY GUARANTEES</text>')
    for i, line in enumerate([
        "One dead source costs only its own results",
        "SERVICE_ONLY sources never serve a user query",
        "An unknown source name fails at build time",
    ]):
        y = 262 + i * 22
        s += f'<circle cx="36" cy="{y - 4}" r="3" fill="{BLUE}"/>'
        s += f'<text x="48" y="{y}" font-size="10.5" fill="{INK}">{esc(line)}</text>'
    return s + "</svg>"


if __name__ == "__main__":
    import pathlib

    out = pathlib.Path("build")
    out.mkdir(exist_ok=True)
    (out / "fig-system.svg").write_text(system_map())
    (out / "fig-boundary.svg").write_text(capability_boundary())
    (out / "fig-urgency.svg").write_text(urgency_ladder())
    (out / "fig-context.svg").write_text(context_framework())
    print("wrote 4 figures")
