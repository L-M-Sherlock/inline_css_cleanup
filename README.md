# Inline CSS Cleanup

Remove inline `<style>…</style>` blocks from selected note fields and move the CSS
into `collection.media/_extracted_css.css`, with selector-level deduplication.

This is useful when large HTML fields (e.g., Yomitan/JP mining glossaries)
embed repeated CSS, bloating `collection.anki2` and pushing you over AnkiWeb
size limits.

## What It Does

- Strips inline `<style>…</style>` blocks from configured fields
- Extracts CSS and **deduplicates by selector** (first occurrence wins)
- Writes the CSS to `collection.media/_extracted_css.css`
- Inserts a small `<style>@import ...</style>` into the field to load the CSS
- Optionally extracts repeated `style="..."` attributes into CSS classes

## Install

Option A — AnkiWeb code:

1. In Anki: **Tools → Add-ons → Get Add-ons…**
2. Enter the code `465508076`
3. Restart Anki

Option B — GitHub Release:

1. Download `inline-css-cleanup.ankiaddon` from the latest
   [GitHub Release](https://github.com/L-M-Sherlock/inline_css_cleanup/releases/latest)
2. In Anki: **Tools → Add-ons → Install from file…**
3. Select the downloaded `.ankiaddon` file and restart Anki

Option C — Build from source:

1. Download the source code
2. Run `./package.sh` to build `inline-css-cleanup.ankiaddon`
3. In Anki: **Tools → Add-ons → Install from file…**
4. Select the `.ankiaddon` file and restart Anki

## Release

GitHub Actions builds the add-on on pull requests, pushes to `main`, manual
runs, and tags matching `v*`.

To publish a release:

1. Create and push a tag such as `v0.2.0`
2. The workflow builds `dist/inline-css-cleanup.ankiaddon`
3. The tag run creates or updates the GitHub Release asset automatically

## Usage

1. **Tools → Inline CSS Cleanup**
2. Choose whether to process all decks or select specific decks
3. Add the target note types in **Target note types**
4. Pick a note type in **Fields for** and select the fields to clean
5. Adjust inline-style extraction options if needed
6. Click **Run Cleanup**
7. Review the summary in the window
8. Run **Check Database** to shrink the collection file after cleanup

Use **Save Defaults** to persist the current deck, note type, field, and cleanup
options to the add-on config.

## Example Screenshot

![Example](assets/example.png)

## Configuration

See `config.md` for detailed configuration.

Quick defaults (from `config.json`):

```json
{
  "decks": [],
  "note_types": ["Lapis"],
  "fields": ["Glossary", "MainDefinition"],
  "fields_by_note_type": {
    "Lapis": ["Glossary", "MainDefinition"]
  },
  "confirm_before_run": true,
  "extract_inline_styles": false,
  "inline_style_min_length": 80,
  "inline_style_min_ratio": 0.05
}
```

## Notes & Safety

- The add-on does not modify template Styling (useful if your Styling is auto-generated).
- Re-running is safe and idempotent: it will not duplicate imports or rules.
- Consider backing up your collection before the first run.
- Extracted inline styles are emitted with `!important` to better preserve appearance.
- Inline style extraction thresholds are based on the number of notes containing inline styles.
- Extracted CSS is stored in `collection.media/_extracted_css.css` and also mirrored to `user_files/extracted_css.css` (easier to find).

## License

AGPL-3.0-or-later
