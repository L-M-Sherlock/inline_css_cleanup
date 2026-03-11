# Inline CSS Cleanup — Configuration

This add-on removes inline `<style>...</style>` blocks from selected note fields and
moves the CSS into the note type Styling, with selector-level deduplication.

## Config Location

Edit the JSON config in Anki:

- **Tools → Add-ons → Inline CSS Cleanup → Config**

Or edit the file directly:

- `addons21/inline-css-cleanup/config.json`

## Config Options

### `note_types`
Type: `array of strings`

Which note types to process. Use note type **names** (e.g., `"Lapis"`).

- Example:
  - `"note_types": ["Lapis", "JP Mining Note"]`

If empty, **all** note types will be processed.

### `fields`
Type: `array of strings`

Which fields to strip inline `<style>` blocks from. Field names must match exactly.

- Example:
  - `"fields": ["Glossary", "MainDefinition"]`

### `css_marker_start` / `css_marker_end`
Type: `string`

Markers used to store the extracted CSS inside the note type Styling. The add-on
only edits the CSS inside this marker block.

- Example:
  - `"css_marker_start": "/* Inline CSS Cleanup: BEGIN */"`
  - `"css_marker_end": "/* Inline CSS Cleanup: END */"`

### `confirm_before_run`
Type: `boolean`

Whether to show a confirmation dialog before running.

- Example:
  - `"confirm_before_run": true`

## Behavior Notes

- **Selector-level deduplication**: if the same selector appears multiple times,
  only the **first** occurrence is kept.
- **Merge with existing marker block**: when rerun, the add-on merges new CSS
  with the existing marker block (old rules keep priority).
- **Idempotent**: running again with no new inline CSS will not change Styling.

## Example Config

```json
{
  "note_types": ["Lapis"],
  "fields": ["Glossary", "MainDefinition"],
  "css_marker_start": "/* Inline CSS Cleanup: BEGIN */",
  "css_marker_end": "/* Inline CSS Cleanup: END */",
  "confirm_before_run": true
}
```
