# The Long Dark Wiki Pipeline

Tools for analyzing a WikiTeam XML dump and exporting its article pages into a
local Markdown workspace for Zettlr.

## Export a Markdown workspace

The exporter uses the title-to-filename mapping produced by `audit-filenames`.
Generate that mapping from the same XML dump before exporting:

```bash
run audit-filenames \
  tld/thelongdark.fandom.com-20261117-current.xml \
  --output reports/quality/filename-audit.json
```

Then export:

```bash
run export-markdown \
  tld/thelongdark.fandom.com-20261117-current.xml \
  --mapping reports/quality/filename-audit.json \
  --output-dir "The Long Dark Wiki" \
  --image-dir tld/images
```

The default output is `The Long Dark Wiki/`:

```text
The Long Dark Wiki/
├── index.md
├── pages/
│   ├── Main Page.md
│   └── ...
├── assets/
│   └── ...
└── conversion-report.json
```

Open the workspace folder in Zettlr and start with `index.md`. Article files
contain plain Markdown without YAML front matter. The wiki title appears as
the page's H1 heading, and detected categories and the source URL are retained
as a readable Markdown metadata line. Article links are
rewritten to the allocated local Markdown filenames, redirects resolve to
their destination articles, and referenced images are copied into `assets/`.
Fandom galleries and image-layout tables are converted to ordinary Markdown
blocks. Infoboxes and headered data tables retain their Markdown table
structure, but images inside table cells are converted to ordinary links
because Zettlr does not render embedded images in tables. Image extensions are
inferred from recognized file signatures when the source filename has none.

Alongside the primary `--image-dir`, the exporter searches a sibling directory
named `<image-dir-name>_mismatch` for images not found in the primary directory;
for the default `tld/images`, this uses `tld/images_mismatch`. Primary-directory
matches take precedence. Templates are expanded with wikitextprocessor; calls
to templates missing from the dump remain visible with their parameter values.

The export checks that the mapping and XML dump contain the same set of
main-namespace article titles. The conversion report records expansion or
conversion errors, unresolved links, and missing local images. Its category counts
come from runtime category extraction during export. The separate category and
template-category reports are static analyses: template-derived memberships are
potential categories, and their uncategorized lists are not the final runtime
category assignment. Every Markdown
file under `pages/` is also scanned for unrendered HTML tags, comments,
declarations, and leaked MediaWiki file or table markup. The export writes
`markup-audit.json` and fails when any unrendered markup remains. Literal code
examples in fenced or inline code are excluded from this audit.


## Check exported Markdown formatting

After exporting the workspace, run the repository's configured Markdown linter:

```bash
poe markdown-lint
```

This task explicitly loads `.mdlint.toml`, which keeps source-derived wiki
wording and intentional table formatting from being reported as style errors.

To rescan an existing workspace without running the exporter again:

```bash
run audit-markdown "The Long Dark Wiki"
```

The audit prints each affected filename and line number and writes the same
findings to `markup-audit.json`.

The initial workspace keeps articles together in `pages/`; detected
categories are retained as visible metadata for a later taxonomy/folder-layout
pass. Re-running the exporter overwrites generated files with the same names
but does not delete files left over from an earlier export.
