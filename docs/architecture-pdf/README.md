# The architecture PDF

`docs/Weave_Architecture.pdf` is built from this directory. It is a designed document
rather than a print of the markdown: the same architecture, laid out for reading on paper
or in a viewer, with the diagrams drawn to the page rather than scaled to fit it.

```bash
make pdf        # rebuild the PDF from the sources here
make diagrams   # also re-render the mermaid sources (needs npx + network)
```

Both targets run through `uv run --with pillow --with pymupdf`, so there is nothing to
install first beyond `uv` and Google Chrome.

## What is here

| File | Role |
|---|---|
| `body.html` · `cover.html` | The document. Content lives here, not in the build script. |
| `style.css` | Print stylesheet: A4 portrait, named landscape pages, figure size caps. |
| `illustrations.py` | The four hand-laid figures — system map, capability boundary, context framework, urgency ladder — generated so every box lands on a grid and every connector meets an edge at a right angle. |
| `diagrams/*.mmd` | The five mermaid sources (sequence and flow diagrams). |
| `mermaid-theme.json` | Brand palette and spacing for mermaid, so its output matches the hand-laid figures. |
| `build.py` | Inlines the CSS, figures and logo, prints cover and body through headless Chrome, merges them and stamps the page numbers. |
| `build/` | Generated. Safe to delete; `make pdf` refills it. |

## Sizing, and why it is enforced in two places

A figure that overflows its page is the failure mode this document is most exposed to, so
the constraint is stated twice. `style.css` caps every figure in millimetres
(`figure svg { max-height: 205mm }`, plus a per-figure width class), and `build.py` strips
the `width`, `height` and `max-width` that mermaid stamps onto its own SVG root — without
that strip, the figure keeps its own idea of how big it is and ignores the page.

The two sequence diagrams are wide enough that shrinking them to the portrait text column
would drop their labels below legibility, so they are printed on named landscape pages
(`@page land`) as full plates, with the surrounding prose on the portrait page before each.

To check a change, rebuild and confirm the content box of every page still sits inside its
margins:

```python
import pymupdf
doc = pymupdf.open("../Weave_Architecture.pdf")
for page in doc:                      # every page but the cover should report ~20mm/16mm
    box = None
    for drawing in page.get_drawings():
        box = drawing["rect"] if box is None else box | drawing["rect"]
    print(page.number + 1, box, page.rect)
```

The cover is the one page that deliberately paints outside the text box: its decorative
arcs are full-bleed and clipped by the page edge.
