# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections import OrderedDict
from typing import Iterable
import re

from anki.collection import OpChanges
from aqt import mw
from aqt.operations import CollectionOp
from aqt.qt import QAction
from aqt.utils import askUser, showInfo

STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)


class SelectorDeduper:
    """Deduplicate CSS by selector at the top level.

    This keeps the first occurrence of each selector and discards later ones.
    """

    def __init__(self) -> None:
        self._seen_selectors: set[str] = set()
        self._seen_statements: set[str] = set()
        self._items: list[tuple[str, str, str]] = []  # (kind, selector/stmt, body)
        self.total_css_bytes = 0
        self.total_rules = 0
        self.unique_rules = 0
        self.total_statements = 0
        self.unique_statements = 0

    def add_css(self, css: str) -> None:
        css = css.strip()
        if not css:
            return
        self.total_css_bytes += len(css)
        self._parse(css)

    def _add_rule(self, selector: str, body: str) -> None:
        selector = selector.strip()
        if not selector:
            return
        self.total_rules += 1
        if selector in self._seen_selectors:
            return
        self._seen_selectors.add(selector)
        self.unique_rules += 1
        self._items.append(("rule", selector, body.strip()))

    def _add_statement(self, stmt: str) -> None:
        stmt = stmt.strip()
        if not stmt:
            return
        self.total_statements += 1
        if stmt in self._seen_statements:
            return
        self._seen_statements.add(stmt)
        self.unique_statements += 1
        self._items.append(("stmt", stmt, ""))

    def _parse(self, css: str) -> None:
        depth = 0
        start = 0
        block_start = 0
        current_selector = ""
        in_comment = False
        in_string: str | None = None

        i = 0
        n = len(css)
        while i < n:
            ch = css[i]
            nxt = css[i + 1] if i + 1 < n else ""

            if in_comment:
                if ch == "*" and nxt == "/":
                    in_comment = False
                    i += 1
                i += 1
                continue

            if in_string:
                if ch == in_string:
                    # check if escaped
                    backslashes = 0
                    j = i - 1
                    while j >= 0 and css[j] == "\\":
                        backslashes += 1
                        j -= 1
                    if backslashes % 2 == 0:
                        in_string = None
                i += 1
                continue

            if ch == "/" and nxt == "*":
                in_comment = True
                i += 2
                continue

            if ch in ('"', "'"):
                in_string = ch
                i += 1
                continue

            if ch == "{":
                if depth == 0:
                    current_selector = css[start:i]
                    block_start = i + 1
                depth += 1
                i += 1
                continue

            if ch == "}":
                if depth > 0:
                    depth -= 1
                    if depth == 0:
                        body = css[block_start:i]
                        self._add_rule(current_selector, body)
                        start = i + 1
                else:
                    start = i + 1
                i += 1
                continue

            if ch == ";" and depth == 0:
                stmt = css[start:i]
                self._add_statement(stmt)
                start = i + 1
                i += 1
                continue

            i += 1

    def render(self) -> str:
        lines: list[str] = []
        for kind, sel_or_stmt, body in self._items:
            if kind == "stmt":
                lines.append(f"{sel_or_stmt};")
            else:
                lines.append(f"{sel_or_stmt} {{ {body} }}")
        return "\n".join(lines)


@dataclass
class CleanupResult:
    changes: OpChanges
    summaries: list[tuple[str, dict]]


def _merge_changes(changes: Iterable[OpChanges]) -> OpChanges:
    out = OpChanges()
    fields = [
        "card",
        "note",
        "deck",
        "tag",
        "notetype",
        "config",
        "deck_config",
        "mtime",
        "browser_table",
        "browser_sidebar",
        "note_text",
        "study_queues",
    ]
    for ch in changes:
        for field in fields:
            if getattr(ch, field, False):
                setattr(out, field, True)
    return out


def _get_config() -> dict:
    config = mw.addonManager.getConfig(__name__) or {}
    # defaults
    config.setdefault("note_types", ["Lapis"])
    config.setdefault("fields", ["Glossary", "MainDefinition"])
    config.setdefault("css_marker_start", "/* Inline CSS Cleanup: BEGIN */")
    config.setdefault("css_marker_end", "/* Inline CSS Cleanup: END */")
    config.setdefault("confirm_before_run", True)
    return config


def _user_files_dir() -> Path:
    return Path(mw.addonManager.addonsFolder(__name__)) / "user_files"


def _user_css_path() -> Path:
    return _user_files_dir() / "extracted_css.css"


def _merge_css(existing: str, new_css: str, marker_start: str, marker_end: str) -> str:
    existing = existing or ""
    start_re = re.escape(marker_start)
    end_re = re.escape(marker_end)
    block_re = re.compile(start_re + r"(.*?)" + end_re, re.DOTALL)

    new_css = new_css.strip()
    if not new_css:
        # If there's no new CSS extracted, keep existing styling unchanged.
        # This makes repeated runs idempotent.
        return existing

    # Merge with existing block (if present) by selector, keeping existing rules first.
    deduper = SelectorDeduper()
    match = block_re.search(existing)
    if match:
        existing_block = match.group(1).strip()
        if existing_block:
            deduper.add_css(existing_block)
    deduper.add_css(new_css)

    merged_css = deduper.render()
    block = f"{marker_start}\n{merged_css}\n{marker_end}"
    if match:
        return block_re.sub(block, existing)
    if existing and not existing.endswith("\n"):
        existing += "\n"
    return existing + block


def _cleanup_model(col, model, fields: list[str], marker_start: str, marker_end: str):
    deduper = SelectorDeduper()
    updated_notes = 0
    removed_bytes = 0
    style_blocks = 0
    notes_to_update = []
    cancelled = False

    nids = col.db.list("select id from notes where mid=?", model["id"])
    total = len(nids)
    if total:
        mw.taskman.run_on_main(
            lambda total=total, name=model["name"]: mw.progress.start(
                label=f"Inline CSS Cleanup — {name}", max=total, immediate=True
            )
        )

    processed = 0
    for nid in nids:
        note = col.get_note(nid)
        changed = False
        for fname in fields:
            if fname not in note:
                continue
            old = note[fname]
            if "<style" not in old.lower():
                continue
            blocks = STYLE_BLOCK_RE.findall(old)
            if not blocks:
                continue
            for css in blocks:
                deduper.add_css(css)
            style_blocks += len(blocks)
            new = STYLE_BLOCK_RE.sub("", old)
            if new != old:
                removed_bytes += len(old) - len(new)
                note[fname] = new
                changed = True
        if changed:
            notes_to_update.append(note)
            updated_notes += 1
        processed += 1
        if processed % 200 == 0 or processed == total:
            mw.taskman.run_on_main(
                lambda count=processed, total=total, name=model["name"]: (
                    mw.progress.update(
                        label=f"Inline CSS Cleanup — {name} ({count}/{total})",
                        value=count,
                        max=total,
                    )
                )
            )
            if mw.progress.want_cancel():
                cancelled = True
                break

    if total:
        mw.taskman.run_on_main(lambda: mw.progress.finish())

    changes_list: list[OpChanges] = []
    if notes_to_update:
        changes_list.append(col.update_notes(notes_to_update))

    new_css = deduper.render()

    # Merge extracted CSS into user_files for persistence across upgrades.
    user_css_path = _user_css_path()
    existing_user_css = ""
    if user_css_path.exists():
        existing_user_css = user_css_path.read_text(encoding="utf-8")

    merged_user_css = existing_user_css
    if new_css.strip():
        user_deduper = SelectorDeduper()
        if existing_user_css.strip():
            user_deduper.add_css(existing_user_css)
        user_deduper.add_css(new_css)
        merged_user_css = user_deduper.render()

    if merged_user_css != existing_user_css:
        user_css_path.parent.mkdir(parents=True, exist_ok=True)
        user_css_path.write_text(merged_user_css, encoding="utf-8")

    existing_css = model.get("css", "")
    merged_css = _merge_css(existing_css, merged_user_css, marker_start, marker_end)
    css_updated = merged_css != existing_css
    if css_updated:
        model["css"] = merged_css
        changes_list.append(col.models.update_dict(model))

    summary = {
        "updated_notes": updated_notes,
        "removed_bytes": removed_bytes,
        "style_blocks": style_blocks,
        "unique_selectors": deduper.unique_rules,
        "total_rules": deduper.total_rules,
        "unique_statements": deduper.unique_statements,
        "total_statements": deduper.total_statements,
        "css_bytes": deduper.total_css_bytes,
        "css_updated": css_updated,
        "cancelled": cancelled,
        "processed": processed,
        "total": total,
    }

    return summary, _merge_changes(changes_list)


def _run_cleanup(col) -> CleanupResult:
    config = _get_config()
    note_types: list[str] = config.get("note_types", [])
    fields: list[str] = config.get("fields", [])
    marker_start: str = config.get("css_marker_start")
    marker_end: str = config.get("css_marker_end")

    if not fields:
        raise Exception("No target fields configured.")

    models = []
    if note_types:
        for name in note_types:
            model = col.models.by_name(name)
            if not model:
                raise Exception(f"Note type not found: {name}")
            models.append(model)
    else:
        models = list(col.models.all())

    summaries: list[tuple[str, dict]] = []
    all_changes: list[OpChanges] = []
    for model in models:
        summary, changes = _cleanup_model(col, model, fields, marker_start, marker_end)
        summaries.append((model["name"], summary))
        all_changes.append(changes)

    return CleanupResult(changes=_merge_changes(all_changes), summaries=summaries)


def _on_cleanup_done(result: CleanupResult) -> None:
    def fmt_bytes(n: int) -> str:
        if n >= 1024 * 1024:
            return f"{n / (1024 * 1024):.2f} MB"
        if n >= 1024:
            return f"{n / 1024:.2f} KB"
        return f"{n} B"

    lines: list[str] = []
    for name, s in result.summaries:
        lines.append(f"Note type: {name}")
        if s.get("total"):
            lines.append(f"  Progress: {s.get('processed', 0)}/{s.get('total', 0)}")
        if s.get("cancelled"):
            lines.append("  Cancelled: yes")
        lines.append(f"  Notes updated: {s['updated_notes']}")
        lines.append(f"  Style blocks removed: {s['style_blocks']}")
        lines.append(f"  Bytes removed from fields: {fmt_bytes(s['removed_bytes'])}")
        lines.append(
            f"  Unique selectors: {s['unique_selectors']} (from {s['total_rules']} rules)"
        )
        if s["total_statements"]:
            lines.append(
                f"  Unique at-rules: {s['unique_statements']} (from {s['total_statements']})"
            )
        lines.append(f"  CSS bytes processed: {fmt_bytes(s['css_bytes'])}")
        lines.append(f"  CSS updated in template: {s['css_updated']}")
        lines.append("")

    lines.append("After cleanup, run Tools → Check Database to shrink the collection.")
    showInfo("\n".join(lines).strip())


def on_run_cleanup() -> None:
    config = _get_config()
    if config.get("confirm_before_run", True):
        ok = askUser(
            "Inline CSS Cleanup will:\n"
            "• Remove <style>…</style> blocks from the configured fields\n"
            "• Deduplicate CSS by selector and write it into the note type Styling\n\n"
            "Continue?"
        )
        if not ok:
            return

    CollectionOp(parent=mw, op=_run_cleanup).success(
        _on_cleanup_done
    ).run_in_background()


def setup() -> None:
    action = QAction("Inline CSS Cleanup", mw)
    action.triggered.connect(on_run_cleanup)
    mw.form.menuTools.addAction(action)


setup()
