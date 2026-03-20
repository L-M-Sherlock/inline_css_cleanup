# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from anki.collection import OpChanges
from aqt import mw
from aqt.operations import CollectionOp
from aqt.qt import QAction
from aqt.utils import askUser, showInfo

EXTRACTED_CSS_FILENAME = "_extracted_css.css"
IMPORT_STYLE_SNIPPET = f'<style>@import url("{EXTRACTED_CSS_FILENAME}");</style>'
LEGACY_MARKER_START = "/* Inline CSS Cleanup: BEGIN */"
LEGACY_MARKER_END = "/* Inline CSS Cleanup: END */"

STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
STYLE_ATTR_RE = re.compile(r'\sstyle="([^"]*)"', re.IGNORECASE)
CLASS_ATTR_RE = re.compile(r'\bclass="([^"]*)"', re.IGNORECASE)
HTML_TAG_RE = re.compile(r"<[^>]+>")
TAG_RE = re.compile(r"<[^>]+>")
MEDIA_TAG_RE = re.compile(
    r"<(img|audio|video|svg|iframe|canvas|object|embed)\b", re.IGNORECASE
)
IMPORT_RE = re.compile(
    rf"@import\s+(?:url\(\s*)?[\"']?{re.escape(EXTRACTED_CSS_FILENAME)}[\"']?\s*\)?\s*;?",
    re.IGNORECASE,
)
LEGACY_BLOCK_RE = re.compile(
    re.escape(LEGACY_MARKER_START) + r"(.*?)" + re.escape(LEGACY_MARKER_END),
    re.DOTALL,
)


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
    missing_note_types: list[str]


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
    config.setdefault("confirm_before_run", True)
    config.setdefault("extract_inline_styles", False)
    config.setdefault("inline_style_min_length", 80)
    config.setdefault("inline_style_min_ratio", 0.005)
    return config


def _media_css_path(col) -> Path:
    return Path(col.media.dir()) / EXTRACTED_CSS_FILENAME


def _user_files_dir() -> Path:
    return Path(mw.addonManager.addonsFolder(__name__)) / "user_files"


def _user_css_path() -> Path:
    return _user_files_dir() / "extracted_css.css"


def _legacy_css_from_model(model: dict) -> str:
    css = model.get("css", "")
    match = LEGACY_BLOCK_RE.search(css)
    if not match:
        return ""
    return match.group(1).strip()


def _has_renderable_html_content(text: str) -> bool:
    if "<" not in text:
        return False
    if MEDIA_TAG_RE.search(text):
        return True
    stripped = HTML_TAG_RE.sub("", text)
    stripped = stripped.replace("&nbsp;", " ").replace("\u00a0", " ").strip()
    return bool(stripped)


def _normalize_inline_style(style: str) -> str:
    style = style.strip()
    if not style:
        return ""
    style = re.sub(r"\s*;\s*", ";", style)
    style = re.sub(r"\s*:\s*", ":", style)
    style = re.sub(r"\s+", " ", style)
    return style


def _inline_style_class(style: str) -> str:
    digest = hashlib.sha1(style.encode("utf-8")).hexdigest()[:10]
    return f"inline-style-{digest}"


def _inline_style_rule(style: str, class_name: str) -> str:
    parts = [part.strip() for part in style.strip().split(";") if part.strip()]
    important_parts: list[str] = []
    for part in parts:
        if ":" not in part:
            important_parts.append(part)
            continue
        if re.search(r"!important\\b", part, re.IGNORECASE):
            important_parts.append(part)
        else:
            important_parts.append(f"{part} !important")
    body = ";".join(important_parts)
    if body and not body.endswith(";"):
        body += ";"
    return f".{class_name} {{ {body} }}"


def _collect_inline_style_counts(
    col, nids, fields: list[str]
) -> tuple[Counter, int, int]:
    counts: Counter[str] = Counter()
    total = 0
    notes_with_styles = 0
    for nid in nids:
        note = col.get_note(nid)
        seen: set[str] = set()
        for fname in fields:
            if fname not in note:
                continue
            text = note[fname]
            if 'style="' not in text.lower():
                continue
            for raw in STYLE_ATTR_RE.findall(text):
                norm = _normalize_inline_style(raw)
                if not norm:
                    continue
                total += 1
                seen.add(norm)
        if seen:
            notes_with_styles += 1
            for style in seen:
                counts[style] += 1
    return counts, total, notes_with_styles


def _select_inline_styles(
    counts: Counter, min_ratio: float, min_length: int, note_total: int
) -> tuple[dict[str, str], int]:
    if not counts:
        return {}, 0
    if min_ratio > 1:
        min_ratio = min_ratio / 100
    min_ratio = max(min_ratio, 0)
    note_total = max(1, note_total)
    min_count = max(1, math.ceil(note_total * min_ratio))
    selected: dict[str, str] = {}
    for style, count in counts.items():
        if count < min_count:
            continue
        if len(style) < min_length:
            continue
        selected[style] = _inline_style_class(style)
    return selected, min_count


def _apply_inline_style_extraction(
    html: str, style_map: dict[str, str]
) -> tuple[str, int, int]:
    if not style_map:
        return html, 0, 0
    if 'style="' not in html.lower():
        return html, 0, 0

    removed_bytes = 0
    extracted = 0

    def repl(match: re.Match) -> str:
        nonlocal removed_bytes, extracted
        tag = match.group(0)
        if tag.startswith("</"):
            return tag
        style_match = STYLE_ATTR_RE.search(tag)
        if not style_match:
            return tag
        raw_style = style_match.group(1)
        norm = _normalize_inline_style(raw_style)
        class_name = style_map.get(norm)
        if not class_name:
            return tag

        new_tag = STYLE_ATTR_RE.sub("", tag, count=1)
        class_match = CLASS_ATTR_RE.search(new_tag)
        if class_match:
            existing = class_match.group(1)
            classes = existing.split()
            if class_name not in classes:
                new_classes = f"{existing} {class_name}"
                new_tag = (
                    new_tag[: class_match.start(1)]
                    + new_classes
                    + new_tag[class_match.end(1) :]
                )
        else:
            if new_tag.endswith("/>"):
                new_tag = new_tag[:-2] + f' class="{class_name}"/>'
            elif new_tag.endswith(">"):
                new_tag = new_tag[:-1] + f' class="{class_name}">'

        removed_bytes += len(tag) - len(new_tag)
        extracted += 1
        return new_tag

    new_html = TAG_RE.sub(repl, html)
    return new_html, removed_bytes, extracted


def _read_text(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _merge_css_sources(*sources: str) -> str:
    deduper = SelectorDeduper()
    for css in sources:
        if css.strip():
            deduper.add_css(css)
    return deduper.render()


def _should_add_import(text: str, import_needed: bool, has_import: bool) -> bool:
    return (
        import_needed
        and not has_import
        and text.strip()
        and _has_renderable_html_content(text)
    )


def _should_defer_import(text: str, import_needed: bool, has_import: bool) -> bool:
    return (
        not import_needed
        and not has_import
        and text.strip()
        and _has_renderable_html_content(text)
    )


@dataclass
class FieldProcessResult:
    new_value: str | None
    removed_bytes: int
    css_blocks: list[str]
    style_blocks: int
    pending_import: bool
    import_needed_now: bool


def _process_field(old: str, import_needed: bool) -> FieldProcessResult:
    has_import = bool(IMPORT_RE.search(old))

    if "<style" not in old.lower():
        if _should_add_import(old, import_needed, has_import):
            return FieldProcessResult(
                new_value=IMPORT_STYLE_SNIPPET + old,
                removed_bytes=0,
                css_blocks=[],
                style_blocks=0,
                pending_import=False,
                import_needed_now=import_needed,
            )
        if _should_defer_import(old, import_needed, has_import):
            return FieldProcessResult(
                new_value=None,
                removed_bytes=0,
                css_blocks=[],
                style_blocks=0,
                pending_import=True,
                import_needed_now=import_needed,
            )
        return FieldProcessResult(
            new_value=None,
            removed_bytes=0,
            css_blocks=[],
            style_blocks=0,
            pending_import=False,
            import_needed_now=import_needed,
        )

    blocks = STYLE_BLOCK_RE.findall(old)
    if not blocks:
        if _should_add_import(old, import_needed, has_import):
            return FieldProcessResult(
                new_value=IMPORT_STYLE_SNIPPET + old,
                removed_bytes=0,
                css_blocks=[],
                style_blocks=0,
                pending_import=False,
                import_needed_now=import_needed,
            )
        if _should_defer_import(old, import_needed, has_import):
            return FieldProcessResult(
                new_value=None,
                removed_bytes=0,
                css_blocks=[],
                style_blocks=0,
                pending_import=True,
                import_needed_now=import_needed,
            )
        return FieldProcessResult(
            new_value=None,
            removed_bytes=0,
            css_blocks=[],
            style_blocks=0,
            pending_import=False,
            import_needed_now=import_needed,
        )

    cleaned_blocks: list[str] = []
    for css in blocks:
        css = IMPORT_RE.sub("", css)
        if css.strip():
            cleaned_blocks.append(css)

    if not cleaned_blocks and has_import:
        return FieldProcessResult(
            new_value=None,
            removed_bytes=0,
            css_blocks=[],
            style_blocks=0,
            pending_import=False,
            import_needed_now=True,
        )

    new = STYLE_BLOCK_RE.sub("", old)
    removed = len(old) - len(new)
    if _should_add_import(new, True, has_import):
        new = IMPORT_STYLE_SNIPPET + new
    import_needed_now = True

    return FieldProcessResult(
        new_value=new if new != old else None,
        removed_bytes=removed,
        css_blocks=cleaned_blocks,
        style_blocks=len(blocks),
        pending_import=False,
        import_needed_now=import_needed_now,
    )


def _cleanup_model(col, model, fields: list[str], config: dict):
    deduper = SelectorDeduper()
    updated_notes = 0
    removed_bytes = 0
    inline_style_removed_bytes = 0
    inline_style_extracted = 0
    style_blocks = 0
    notes_to_update = {}
    cancelled = False

    media_css_path = _media_css_path(col)
    user_css_path = _user_css_path()
    existing_media_css = _read_text(media_css_path)
    existing_user_css = _read_text(user_css_path)
    legacy_css = _legacy_css_from_model(model)

    import_needed = bool(
        existing_media_css.strip() or existing_user_css.strip() or legacy_css.strip()
    )
    pending_import: list[tuple[object, str]] = []

    nids = col.db.list("select id from notes where mid=?", model["id"])
    total = len(nids)

    inline_style_total = 0
    inline_style_unique = 0
    inline_style_note_total = 0
    inline_style_min_count = 0
    inline_style_selected = 0
    inline_style_map: dict[str, str] = {}
    if config.get("extract_inline_styles", False):
        (
            inline_style_counts,
            inline_style_total,
            inline_style_note_total,
        ) = _collect_inline_style_counts(col, nids, fields)
        inline_style_unique = len(inline_style_counts)
        inline_style_min_ratio = float(config.get("inline_style_min_ratio", 0))
        inline_style_min_length = int(config.get("inline_style_min_length", 0))
        inline_style_map, inline_style_min_count = _select_inline_styles(
            inline_style_counts,
            inline_style_min_ratio,
            inline_style_min_length,
            inline_style_note_total,
        )
        inline_style_selected = len(inline_style_map)
        if inline_style_map:
            inline_css = "\n".join(
                _inline_style_rule(style, class_name)
                for style, class_name in inline_style_map.items()
            )
            if inline_css.strip():
                deduper.add_css(inline_css)
        import_needed = import_needed or bool(inline_style_map)

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
            result = _process_field(old, import_needed)
            for css in result.css_blocks:
                deduper.add_css(css)
            style_blocks += result.style_blocks
            removed_bytes += result.removed_bytes
            if result.new_value is not None:
                note[fname] = result.new_value
                changed = True
            if result.pending_import:
                pending_import.append((note, fname))
            import_needed = result.import_needed_now or import_needed
            current = note[fname]
            if inline_style_map:
                new_value, removed, extracted = _apply_inline_style_extraction(
                    current, inline_style_map
                )
                if extracted:
                    inline_style_extracted += extracted
                    inline_style_removed_bytes += removed
                    removed_bytes += removed
                    import_needed = True
                    has_import = bool(IMPORT_RE.search(new_value))
                    if _should_add_import(new_value, True, has_import):
                        new_value = IMPORT_STYLE_SNIPPET + new_value
                    note[fname] = new_value
                    changed = True
        if changed:
            notes_to_update[note.id] = note
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

    new_css = deduper.render()
    merged_media_css = _merge_css_sources(
        existing_media_css, existing_user_css, legacy_css, new_css
    )

    if merged_media_css.strip() and pending_import:
        for note, fname in pending_import:
            if fname not in note:
                continue
            old = note[fname]
            if IMPORT_RE.search(old):
                continue
            if _should_add_import(old, True, False):
                note[fname] = IMPORT_STYLE_SNIPPET + old
                notes_to_update[note.id] = note

    media_css_updated = merged_media_css != existing_media_css
    if media_css_updated:
        media_css_path.write_text(merged_media_css, encoding="utf-8")

    user_css_updated = merged_media_css != existing_user_css
    if user_css_updated:
        user_css_path.parent.mkdir(parents=True, exist_ok=True)
        user_css_path.write_text(merged_media_css, encoding="utf-8")

    changes_list: list[OpChanges] = []
    if notes_to_update:
        changes_list.append(col.update_notes(list(notes_to_update.values())))

    summary = {
        "updated_notes": updated_notes,
        "removed_bytes": removed_bytes,
        "style_blocks": style_blocks,
        "inline_style_total": inline_style_total,
        "inline_style_unique": inline_style_unique,
        "inline_style_note_total": inline_style_note_total,
        "inline_style_min_count": inline_style_min_count,
        "inline_style_selected": inline_style_selected,
        "inline_style_extracted": inline_style_extracted,
        "inline_style_removed_bytes": inline_style_removed_bytes,
        "unique_selectors": deduper.unique_rules,
        "total_rules": deduper.total_rules,
        "unique_statements": deduper.unique_statements,
        "total_statements": deduper.total_statements,
        "css_bytes": deduper.total_css_bytes,
        "media_css_updated": media_css_updated,
        "user_css_updated": user_css_updated,
        "cancelled": cancelled,
        "processed": processed,
        "total": total,
    }

    return summary, _merge_changes(changes_list)


def _run_cleanup(col) -> CleanupResult:
    config = _get_config()
    note_types: list[str] = config.get("note_types", [])
    fields: list[str] = config.get("fields", [])

    if not fields:
        raise Exception("No target fields configured.")

    models = []
    missing_note_types: list[str] = []
    if note_types:
        for name in note_types:
            model = col.models.by_name(name)
            if not model:
                missing_note_types.append(name)
                continue
            models.append(model)
    else:
        models = list(col.models.all())

    summaries: list[tuple[str, dict]] = []
    all_changes: list[OpChanges] = []
    for model in models:
        summary, changes = _cleanup_model(col, model, fields, config)
        summaries.append((model["name"], summary))
        all_changes.append(changes)

    return CleanupResult(
        changes=_merge_changes(all_changes),
        summaries=summaries,
        missing_note_types=missing_note_types,
    )


def _on_cleanup_done(result: CleanupResult) -> None:
    def fmt_bytes(n: int) -> str:
        if n >= 1024 * 1024:
            return f"{n / (1024 * 1024):.2f} MB"
        if n >= 1024:
            return f"{n / 1024:.2f} KB"
        return f"{n} B"

    lines: list[str] = []
    if not result.summaries:
        lines.append("No matching note types were processed.")
    for name, s in result.summaries:
        lines.append(f"Note type: {name}")
        if s.get("total"):
            lines.append(f"  Progress: {s.get('processed', 0)}/{s.get('total', 0)}")
        if s.get("cancelled"):
            lines.append("  Cancelled: yes")
        lines.append(f"  Notes updated: {s['updated_notes']}")
        lines.append(f"  Style blocks removed: {s['style_blocks']}")
        lines.append(f"  Bytes removed from fields: {fmt_bytes(s['removed_bytes'])}")
        if s.get("inline_style_total"):
            lines.append(
                "  Inline styles found: "
                f"{s['inline_style_total']} (unique {s['inline_style_unique']})"
            )
            lines.append(f"  Inline style notes: {s['inline_style_note_total']}")
            lines.append(
                "  Inline style rules extracted: "
                f"{s['inline_style_selected']} (min count {s['inline_style_min_count']} notes)"
            )
            lines.append(
                f"  Inline style occurrences extracted: {s['inline_style_extracted']}"
            )
            lines.append(
                "  Inline style bytes removed: "
                f"{fmt_bytes(s['inline_style_removed_bytes'])}"
            )
        lines.append(
            f"  Unique selectors: {s['unique_selectors']} (from {s['total_rules']} rules)"
        )
        if s["total_statements"]:
            lines.append(
                f"  Unique at-rules: {s['unique_statements']} (from {s['total_statements']})"
            )
        lines.append(f"  CSS bytes processed: {fmt_bytes(s['css_bytes'])}")
        lines.append(f"  Media CSS updated: {s['media_css_updated']}")
        lines.append(f"  User CSS updated: {s['user_css_updated']}")
        lines.append("")

    if result.missing_note_types:
        missing = ", ".join(result.missing_note_types)
        lines.append(f"Missing note types (skipped): {missing}")

    lines.append("After cleanup, run Tools → Check Database to shrink the collection.")
    showInfo("\n".join(lines).strip(), parent=mw)


def on_run_cleanup() -> None:
    config = _get_config()
    if config.get("confirm_before_run", True):
        ok = askUser(
            "Inline CSS Cleanup will:\n"
            "• Remove <style>…</style> blocks from the configured fields\n"
            "• Write merged CSS to collection.media/_extracted_css.css\n"
            "• Insert <style>@import ...</style> into fields as needed\n\n"
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
