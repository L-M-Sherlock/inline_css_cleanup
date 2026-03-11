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

1. Download `inline-css-cleanup.ankiaddon`
2. In Anki: **Tools → Add-ons → Install from file…**
3. Select the `.ankiaddon` file and restart Anki

## Usage

1. **Tools → Inline CSS Cleanup**
2. Confirm the prompt
3. Review the summary dialog

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

## License

AGPL-3.0-or-later
