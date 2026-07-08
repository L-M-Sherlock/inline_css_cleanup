# Repository Guidelines

## Project Structure & Module Organization

- `__init__.py` contains the add‑on logic (menu action, CSS extraction/dedup, note updates).
- `manifest.json` and `config.json` define the Anki add‑on metadata and defaults.
- `README.md` and `config.md` are end‑user docs.
- `package.sh` builds the `.ankiaddon` bundle.

There are no tests or additional assets in this repository.

## Anki Database Safety

- Never directly modify `collection.anki2`, `collection.media.db2`, or related
  Anki profile databases while Anki is running.
- Before any external script or SQLite command writes to an Anki database, first
  ask the user to close Anki and wait for confirmation.
- Prefer implementing data changes through the add-on using Anki/aqt collection
  APIs. Direct database writes are only acceptable for explicit repair tasks
  after Anki has been closed and a backup has been created.
- Read-only inspection is acceptable, but do not use it as a reason to proceed
  with writes while Anki is open.

## Build, Test, and Development Commands

- `./package.sh`
  - Builds `inline-css-cleanup.ankiaddon` in the current folder.
- `./package.sh <addon_dir> <out_dir>`
  - Build from a different directory or place the output elsewhere.

There is no automated test suite. Manual testing is expected in Anki.

## Coding Style & Naming Conventions

- Python code uses 4‑space indentation.
- Prefer clear, short function names; avoid abbreviations.
- Keep user‑facing strings concise and explicit (dialogs, tooltips).

## Testing Guidelines

- No formal tests. Validate by installing the `.ankiaddon` and running:
  - **Tools → Inline CSS Cleanup**
- Verify:
  - Inline `<style>` blocks are removed.
  - Styling contains the marker block and merged CSS.
  - Reruns are idempotent.

## Commit & Pull Request Guidelines

- Current history only has an initial commit; no strict convention is established.
- Use short, imperative summaries (e.g., `Fix CSS merge on rerun`).
- Run `uv run ruff format .` before each commit.
- PRs should include:
  - A summary of behavior changes.
  - Any manual test steps and results.
  - UI screenshots if dialog text or workflow changes.

## Configuration Tips

- Default config lives in `config.json`.
- Users can override in Anki via **Tools → Add‑ons → Inline CSS Cleanup → Config**.
- Avoid renaming `css_marker_start`/`css_marker_end` without a migration plan.
