#!/usr/bin/env python3
"""Build the ProteoSphere Model Studio single-file simulation.

Reads:
    gui/model_studio_web_v2/index.html            (template)
    gui/model_studio_web_v2/vendor/**             (vendor JS + fonts)
    gui/model_studio_web_v2/data.js, *.jsx, *.css (app code)
    simulation/mock-api.js                        (this directory)

Writes:
    ProteoSphereDemo_Simulation.html              (repo root, ~5.9 MB, self-contained)

Usage:
    cd path/to/proteosphere-model-studio
    python simulation/build_simulation.py

The output is a single .html file at the repo root. Double-click it;
the entire Model Studio runs in the browser with no backend.  Every
/api/v2/* call is intercepted by simulation/mock-api.js (inlined at
build time).
"""
from __future__ import annotations

import base64
import html
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUI_DIR = ROOT / "gui" / "model_studio_web_v2"
SIM_DIR = ROOT / "simulation"
OUT_HTML = ROOT / "ProteoSphereDemo_Simulation.html"

INDEX_HTML = GUI_DIR / "index.html"
GEIST_CSS  = GUI_DIR / "vendor" / "fonts" / "geist.css"


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def read_bytes(p: Path) -> bytes:
    return p.read_bytes()


def inline_geist_css(css_text: str, fonts_dir: Path) -> str:
    """Replace url(<file>.ttf) with data: URIs."""
    def repl(match: re.Match) -> str:
        font_rel = match.group(1).strip().strip("'\"")
        font_path = fonts_dir / font_rel
        if not font_path.exists():
            print(f"  ! missing font: {font_path}", file=sys.stderr)
            return match.group(0)
        b64 = base64.b64encode(read_bytes(font_path)).decode("ascii")
        return f"url(data:font/ttf;base64,{b64}) format('truetype')"
    return re.sub(r"url\(([^)]+)\)\s+format\('truetype'\)", repl, css_text)


def escape_for_script(text: str) -> str:
    """Make a JS payload safe to embed inside <script>...</script>.

    The only sequence the parser actually cares about inside a non-XHTML
    <script> body is </script>; we split that with a string boundary.
    """
    return text.replace("</script>", "<\\/script>")


def build() -> None:
    if not INDEX_HTML.exists():
        print(f"FATAL: {INDEX_HTML} not found", file=sys.stderr)
        sys.exit(2)
    html_text = read_text(INDEX_HTML)

    # 1) Inline Geist font CSS with base64 TTFs.
    geist_css = read_text(GEIST_CSS)
    geist_css_inlined = inline_geist_css(geist_css, GEIST_CSS.parent)
    html_text = re.sub(
        r'<link[^>]*href="vendor/fonts/geist\.css"[^>]*/?>',
        f"<style id=\"inline-geist\">{geist_css_inlined}</style>",
        html_text,
    )

    # 2) Inline styles.css.
    styles_css = read_text(GUI_DIR / "styles.css")
    html_text = re.sub(
        r'<link[^>]*href="styles\.css"[^>]*/?>',
        f"<style id=\"inline-styles\">{styles_css}</style>",
        html_text,
    )

    # 3) Inline every <script src="...">  — both vanilla and Babel-marked.
    #    Vanilla:    <script src="vendor/react.development.js"></script>
    #    Babel JSX:  <script type="text/babel" src="app.jsx"></script>
    def inline_script(match: re.Match) -> str:
        attrs = match.group(1)
        src_match = re.search(r'src="([^"]+)"', attrs)
        if not src_match:
            return match.group(0)
        src = src_match.group(1)
        path = GUI_DIR / src
        if not path.exists():
            print(f"  ! missing script: {path}", file=sys.stderr)
            return match.group(0)
        body = read_text(path)
        body = escape_for_script(body)
        # Preserve the type="text/babel" attribute (and any other attrs)
        # but strip the src.
        attrs_no_src = re.sub(r'\s+src="[^"]*"', "", attrs).strip()
        type_attr = f" {attrs_no_src}" if attrs_no_src else ""
        return f"<script{type_attr}>\n{body}\n</script>"

    html_text = re.sub(
        r'<script([^>]*\s+src="[^"]+"[^>]*)>\s*</script>',
        inline_script,
        html_text,
    )

    # 4) Insert mock-api.js + simulation banner CSS BEFORE the first
    #    inlined script in <body> so the fetch/EventSource monkey-patch
    #    is installed before React runs.
    mock_js = read_text(SIM_DIR / "mock-api.js")
    mock_js_safe = escape_for_script(mock_js)
    banner_css = (
        "\n  <style id=\"sim-banner-css\">\n"
        "    body { transition: padding-top 0.2s; }\n"
        "    #ps-sim-banner { user-select: none; }\n"
        "  </style>\n"
    )
    mock_block = (
        banner_css
        + "  <script id=\"ps-mock-api\">\n" + mock_js_safe + "\n  </script>\n"
    )
    html_text = html_text.replace("<body data-theme=\"dark\" data-density=\"comfortable\">",
                                  "<body data-theme=\"dark\" data-density=\"comfortable\">\n" + mock_block,
                                  1)

    # 5) Write the bundle.
    OUT_HTML.write_text(html_text, encoding="utf-8")
    size_mb = OUT_HTML.stat().st_size / (1024 ** 2)
    print(f"  wrote {OUT_HTML}  ({size_mb:.2f} MB)")


if __name__ == "__main__":
    build()
