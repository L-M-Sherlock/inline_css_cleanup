# Inline CSS Cleanup

Remove inline `<style>…</style>` blocks from selected note fields and move the CSS
into the note type Styling, with selector-level deduplication.

This is useful when large HTML fields (e.g., Yomitan/JP mining glossaries)
embed repeated CSS, bloating `collection.anki2` and pushing you over AnkiWeb
size limits.

## What It Does

- Strips inline `<style>…</style>` blocks from configured fields
- Extracts CSS and **deduplicates by selector** (first occurrence wins)
- Writes the CSS into the note type Styling between markers
- Merges with any existing marker block on reruns

## Install

Option A — AnkiWeb code:

1. In Anki: **Tools → Add-ons → Get Add-ons…**
2. Enter the code `465508076`
3. Restart Anki

Option B — Install from file:

1. Download `inline-css-cleanup.ankiaddon`
2. In Anki: **Tools → Add-ons → Install from file…**
3. Select the `.ankiaddon` file and restart Anki

## Usage

1. **Tools → Inline CSS Cleanup**
2. Confirm the prompt
3. Review the summary dialog
4. Run **Check Database** to shrink the collection file after cleanup

## Example Screenshot

![Example](assets/example.png)

## Configuration

See `config.md` for detailed configuration.

Quick defaults (from `config.json`):

```json
{
  "note_types": ["Lapis"],
  "fields": ["Glossary", "MainDefinition"],
  "css_marker_start": "/* Inline CSS Cleanup: BEGIN */",
  "css_marker_end": "/* Inline CSS Cleanup: END */",
  "confirm_before_run": true
}
```

## Notes & Safety

- The add-on only edits CSS inside the marker block. Your other Styling remains untouched.
- Re-running is safe and idempotent: if no new inline CSS exists, nothing changes.
- Consider backing up your collection before the first run.
- Extracted CSS is stored in `user_files/extracted_css.css` and merged on each run.
- Removing CSS from fields means those fields will lose styling in the **card browser**, because the CSS now lives in the template and only applies during card rendering. If you want styled fields in the browser, install the CSS Injector add-on (`https://ankiweb.net/shared/info/181103283`) and paste the contents of `user_files/extracted_css.css` into its `field.css`. You can find `extracted_css.css` by navigating to the add‑on folder (Tools → Add‑ons → Inline CSS Cleanup → click **View Files**).

## License

AGPL-3.0-or-later
