#!/usr/bin/env python3
"""Build docs/Weave_Architecture.pdf.

    python3 build.py [--diagrams]

`--diagrams` re-renders the mermaid sources through mermaid-cli; without it the
SVGs already in build/ are reused, which is most of the runtime.

The cover and the body are printed separately so the cover can stay free of the
running footer, then merged. Page numbers are stamped afterwards rather than
drawn by CSS, because Chrome implements neither @page margin boxes nor CSS
running elements.
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
BUILD = HERE / "build"
DOCS = HERE.parent
OUT = DOCS / "Weave_Architecture.pdf"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

FIGURES = ["01-system", "02-flow", "03-pipeline", "04-context", "05-graph", "06-chat"]


def run(cmd: list[str], **kw) -> None:
    subprocess.run(cmd, check=True, **kw)


def render_diagrams(only_missing: bool = False) -> None:
    for source in sorted((HERE / "diagrams").glob("*.mmd")):
        target = BUILD / f"{source.stem}.svg"
        if only_missing and target.exists():
            continue
        print(f"  mermaid → {target.name}")
        run(
            [
                "npx",
                "-y",
                "-p",
                "@mermaid-js/mermaid-cli",
                "mmdc",
                "-i",
                str(source),
                "-o",
                str(target),
                "-c",
                str(HERE / "mermaid-theme.json"),
                "-b",
                "transparent",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def render_illustrations() -> None:
    run([sys.executable, str(HERE / "illustrations.py")], cwd=HERE, stdout=subprocess.DEVNULL)


def logo_data_uri() -> str:
    """Trim the logo's whitespace so the cover can place it on a real baseline."""
    from PIL import Image, ImageChops

    image = Image.open(DOCS / "weave_logo.png").convert("RGB")
    background = Image.new("RGB", image.size, (255, 255, 255))
    bbox = (
        ImageChops.difference(image, background)
        .convert("L")
        .point(lambda v: 255 if v > 8 else 0)
        .getbbox()
    )
    cropped = (image.crop(bbox) if bbox else image).convert("RGBA")
    # knock the paper out from behind the mark so it sits on the cover's tint
    pixels = cropped.load()
    for y in range(cropped.height):
        for x in range(cropped.width):
            r, g, bl, _ = pixels[x, y]
            if r > 244 and g > 244 and bl > 244:
                pixels[x, y] = (r, g, bl, 0)
            elif r > 225 and g > 225 and bl > 225:
                pixels[x, y] = (r, g, bl, 110)
    cropped.save(BUILD / "logo.png")
    return "data:image/png;base64," + base64.b64encode((BUILD / "logo.png").read_bytes()).decode()


def inline_svg(name: str) -> str:
    """Return an SVG that scales to whatever width the stylesheet gives it.

    mermaid stamps a pixel width, a pixel height and a max-width style on its
    root element; all three have to go, or the figure keeps its own idea of how
    big it is and ignores the page.
    """
    svg = (BUILD / f"{name}.svg").read_text()
    svg = re.sub(r"<\?xml[^>]*\?>", "", svg).strip()
    head_end = svg.index(">")
    head, rest = svg[:head_end], svg[head_end:]
    view = re.search(r'viewBox="([^"]+)"', head)
    if not view:
        raise SystemExit(f"{name}.svg has no viewBox")
    head = re.sub(r'\s(width|height)="[^"]*"', "", head)
    head = re.sub(r'\sstyle="[^"]*"', "", head)
    head = head.replace("<svg", '<svg preserveAspectRatio="xMidYMid meet"', 1)
    return head + rest


def compose(template: Path, css: str, logo: str) -> str:
    html = template.read_text()
    html = html.replace("{{css}}", css)
    html = html.replace("{{logo}}", logo)
    html = html.replace("{{date}}", dt.date.today().strftime("%B %Y"))
    for match in sorted(set(re.findall(r"\{\{fig:([a-z0-9-]+)\}\}", html))):
        html = html.replace(f"{{{{fig:{match}}}}}", inline_svg(match))
    return html


def print_pdf(html: Path, pdf: Path) -> None:
    run(
        [
            CHROME,
            "--headless",
            "--disable-gpu",
            "--no-pdf-header-footer",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=6000",
            f"--print-to-pdf={pdf}",
            html.as_uri(),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def merge_and_number(cover: Path, body: Path, out: Path) -> None:
    import pymupdf

    doc = pymupdf.open(cover)
    pages = pymupdf.open(body)
    doc.insert_pdf(pages)
    total = doc.page_count

    ink = (0.352, 0.431, 0.576)
    for index, page in enumerate(doc):
        if index == 0:
            continue
        width, height = page.rect.width, page.rect.height
        baseline = height - 26
        page.draw_line(
            pymupdf.Point(56, baseline - 12),
            pymupdf.Point(width - 56, baseline - 12),
            color=(0.914, 0.937, 0.980),
            width=0.6,
        )
        page.insert_text(
            pymupdf.Point(56, baseline),
            "Weave · Architecture",
            fontname="helv",
            fontsize=7.5,
            color=ink,
        )
        label = f"{index + 1} / {total}"
        page.insert_text(
            pymupdf.Point(
                width - 56 - pymupdf.get_text_length(label, fontname="helv", fontsize=7.5), baseline
            ),
            label,
            fontname="helv",
            fontsize=7.5,
            color=ink,
        )

    doc.set_metadata(
        {
            "title": "Weave — Architecture",
            "author": "Weave",
            "subject": "How Weave is put together, and why",
            "keywords": "weave, architecture, google workspace, agent engine",
        }
    )
    doc.save(out, deflate=True, garbage=3)
    doc.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagrams", action="store_true", help="re-render the mermaid sources")
    args = parser.parse_args()

    BUILD.mkdir(exist_ok=True)
    if args.diagrams:
        print("rendering diagrams")
        render_diagrams()
    else:
        # a fresh checkout has no rendered SVGs; fetch just the missing ones
        render_diagrams(only_missing=True)
    render_illustrations()

    css = (HERE / "style.css").read_text()
    logo = logo_data_uri()
    for name in ("cover", "body"):
        rendered = BUILD / f"{name}.rendered.html"
        rendered.write_text(compose(HERE / f"{name}.html", css, logo))
        print_pdf(rendered, BUILD / f"{name}.pdf")
        print(f"  printed {name}.pdf")

    merge_and_number(BUILD / "cover.pdf", BUILD / "body.pdf", OUT)
    size = OUT.stat().st_size / 1024
    print(f"wrote {OUT.relative_to(DOCS.parent)} ({size:.0f} KB)")


if __name__ == "__main__":
    if not Path(CHROME).exists():
        sys.exit("Google Chrome is required to print the PDF")
    if shutil.which("npx") is None:
        print("note: npx not found; --diagrams will fail", file=sys.stderr)
    main()
