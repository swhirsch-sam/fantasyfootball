#!/usr/bin/env python3
"""Generate a self-contained GitHub Pages site that runs the app in-browser.

This bundles the Streamlit app into a single static HTML file using
`stlite <https://github.com/whitphx/stlite>`_, which runs Streamlit entirely
client-side via WebAssembly (Pyodide). No server is needed — GitHub Pages can
host the result directly.

The Python sources stay the single source of truth; this script *embeds* them
into ``docs/index.html``. Re-run it after changing the app:

    python tools/build_static.py

Then commit ``docs/index.html`` and enable Pages (Settings -> Pages ->
Deploy from a branch -> main -> /docs).
"""

from __future__ import annotations

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS = ROOT / "docs"

# App modules to ship to the browser. requests is intentionally omitted — it is
# imported defensively in data_sources and isn't available in the browser, so
# the live sources fall back to SAMPLE (which is the default anyway).
APP_FILES = ["app.py", "scoring.py", "data_sources.py", "valuation.py"]

# stlite is loaded from jsDelivr (unversioned -> latest, to avoid pinning a
# version that may not exist). It bundles streamlit and its deps (pandas,
# altair), so no extra requirements are needed.
STLITE_JS = "https://cdn.jsdelivr.net/npm/@stlite/browser/build/stlite.js"
STLITE_CSS = "https://cdn.jsdelivr.net/npm/@stlite/browser/build/style.css"

HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Dynasty Auction Tool</title>
  <link rel="stylesheet" href="{css}" />
  <style>
    html, body {{ margin: 0; height: 100%; }}
    #boot {{
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      max-width: 640px; margin: 12vh auto; padding: 0 24px; color: #1f2933;
    }}
    #boot h1 {{ font-size: 1.5rem; }}
    #boot .spin {{
      display: inline-block; width: 18px; height: 18px; margin-right: 8px;
      border: 3px solid #cbd2d9; border-top-color: #3b82f6; border-radius: 50%;
      animation: s 0.9s linear infinite; vertical-align: -3px;
    }}
    @keyframes s {{ to {{ transform: rotate(360deg); }} }}
    #fallback {{ display: none; margin-top: 1.5rem; padding: 12px 16px;
      background: #fff7ed; border: 1px solid #fed7aa; border-radius: 8px;
      font-size: 0.9rem; }}
  </style>
</head>
<body>
  <div id="root">
    <div id="boot">
      <h1>Dynasty Auction Tool</h1>
      <p><span class="spin"></span>Starting Streamlit in your browser…</p>
      <p style="color:#52606d">First load compiles Python via WebAssembly and
      can take 20–60 seconds. It's cached after that.</p>
      <div id="fallback">
        Still loading? Open your browser's developer console for errors, or run
        the app on <a href="https://share.streamlit.io">Streamlit Community
        Cloud</a> / locally with <code>streamlit run app.py</code>.
      </div>
    </div>
  </div>

  <script type="module">
    import {{ mount }} from "{js}";
    mount(
      {{
        requirements: [],
        entrypoint: "app.py",
        files: {{
{files}
        }},
      }},
      document.getElementById("root"),
    );
    // If stlite hasn't replaced the boot screen after a while, surface help.
    setTimeout(function () {{
      var fb = document.getElementById("fallback");
      if (fb && document.body.contains(document.getElementById("boot"))) {{
        fb.style.display = "block";
      }}
    }}, 75000);
  </script>
</body>
</html>
"""


def build() -> pathlib.Path:
    entries = []
    for name in APP_FILES:
        src = (ROOT / name).read_text(encoding="utf-8")
        # json.dumps yields a valid, fully-escaped JS string literal.
        entries.append(f"          {json.dumps(name)}: {json.dumps(src)},")
    files_block = "\n".join(entries)

    html = HTML.format(css=STLITE_CSS, js=STLITE_JS, files=files_block)

    DOCS.mkdir(exist_ok=True)
    out = DOCS / "index.html"
    out.write_text(html, encoding="utf-8")
    (DOCS / ".nojekyll").write_text("", encoding="utf-8")  # serve files as-is
    return out


if __name__ == "__main__":
    out = build()
    kb = out.stat().st_size / 1024
    print(f"wrote {out.relative_to(ROOT)} ({kb:.0f} KB) embedding: "
          + ", ".join(APP_FILES))
