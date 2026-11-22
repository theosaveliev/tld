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
conversion errors, unresolved links, and missing local images.

The initial workspace keeps articles together in `pages/`; detected
categories are retained as visible metadata for a later taxonomy/folder-layout
pass. Re-running the exporter overwrites generated files with the same names
but does not delete files left over from an earlier export.
