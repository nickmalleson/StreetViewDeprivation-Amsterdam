#!/usr/bin/env python3
"""Strip bulky interactive-map (folium / leaflet) outputs from notebooks.

The folium maps in notebook 1 embed every sample point — and, in one cell,
base64 JPEG panorama thumbnails — inline in their HTML output, which runs to
tens of megabytes and bloats git. This removes those outputs while keeping the
matplotlib image plots we *do* want visible on GitHub.

It is wired into the git pre-commit hook (see `.githooks/pre-commit`, installed
with `make install-hooks`), which runs it over staged notebooks and re-stages
anything it changes. It can also be run by hand:  python tools/strip_map_outputs.py nb.ipynb

Files are rewritten in place only when something is actually removed. Exit status
is always 0 — this is a cleaner, not a linter, and must never block a commit.
Only the Python standard library is used (so the hook works without the conda
env): the notebook JSON is round-tripped with `json.dump(..., indent=1)`, which
reproduces this repo's on-disk formatting byte-for-byte, so the only diff is the
removed output. (We deliberately avoid nbformat, which re-expands single-string
`source` fields into line-lists and would churn every cell.)
"""
import json
import sys

# A text/html output is treated as an interactive map (and dropped) if it looks
# like folium / leaflet, or is implausibly large for an ordinary HTML table.
MAP_MARKERS = ("leaflet", "folium")
HTML_SIZE_LIMIT = 1_000_000  # bytes; backstop for any other giant html blob


def _is_map_html(html: str) -> bool:
    low = html.lower()
    return any(marker in low for marker in MAP_MARKERS) or len(html) > HTML_SIZE_LIMIT


def _strip_cell(cell) -> bool:
    """Drop any map-like outputs from one cell; return True if it changed."""
    outputs = cell.get("outputs")
    if not outputs:
        return False
    kept, changed = [], False
    for out in outputs:
        html = out.get("data", {}).get("text/html")
        if html is not None:
            if isinstance(html, list):
                html = "".join(html)
            if _is_map_html(html):
                changed = True
                continue  # drop the whole output (html + its text/plain repr)
        kept.append(out)
    if changed:
        cell["outputs"] = kept
    return changed


def strip_notebook(path) -> bool:
    """Rewrite `path` without its map outputs; return True if anything changed."""
    with open(path, encoding="utf-8") as fh:
        nb = json.load(fh)

    changed = False
    for cell in nb.get("cells", []):
        if cell.get("cell_type") == "code":
            changed |= _strip_cell(cell)

    if changed:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(nb, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
    return changed


def main(argv) -> int:
    for path in argv:
        if not path.endswith(".ipynb"):
            continue
        try:
            if strip_notebook(path):
                print(f"stripped interactive-map outputs from {path}")
        except (OSError, ValueError) as exc:  # ValueError covers JSON/nbformat
            print(f"warning: could not process {path}: {exc}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
