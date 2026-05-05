# Copyright: Ankitects Pty Ltd and contributors
# License: GNU AGPL, version 3 or later; http://www.gnu.org/licenses/agpl.html

from __future__ import annotations

import html as html_module
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
from aqt.qt import (
    QAction,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextBrowser,
    QTimer,
    QVBoxLayout,
    QWidget,
)
from aqt.utils import askUser, showInfo, showWarning

EXTRACTED_CSS_FILENAME = "_extracted_css.css"
IMPORT_STYLE_SNIPPET = f'<style>@import url("{EXTRACTED_CSS_FILENAME}");</style>'
LEGACY_MARKER_START = "/* Inline CSS Cleanup: BEGIN */"
LEGACY_MARKER_END = "/* Inline CSS Cleanup: END */"

STYLE_BLOCK_RE = re.compile(r"<style[^>]*>(.*?)</style>", re.IGNORECASE | re.DOTALL)
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
    missing_decks: list[str]


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
    config.setdefault("decks", [])
    config.setdefault("note_types", ["Lapis"])
    config.setdefault("fields", ["Glossary", "MainDefinition"])
    config.setdefault("fields_by_note_type", {})
    config.setdefault("confirm_before_run", True)
    config.setdefault("extract_inline_styles", False)
    config.setdefault("inline_style_min_length", 80)
    config.setdefault("inline_style_min_ratio", 0.05)
    return config


def _string_list(value) -> list[str]:
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = []
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _field_names_from_model(model: dict) -> list[str]:
    return [
        str(field.get("name", "")).strip()
        for field in model.get("flds", [])
        if str(field.get("name", "")).strip()
    ]


def _all_note_type_models(col) -> list[tuple[str, dict]]:
    try:
        models = col.models.all()
    except Exception:
        return []

    pairs = []
    for model in models:
        name = str(model.get("name", "")).strip()
        if name:
            pairs.append((name, model))
    return sorted(pairs, key=lambda item: item[0].lower())


def _deck_pair_from_item(item) -> tuple[str, int] | None:
    if isinstance(item, dict):
        name = item.get("name")
        deck_id = item.get("id")
    else:
        name = getattr(item, "name", None)
        deck_id = getattr(item, "id", None)
    if name is None or deck_id is None:
        return None
    name = str(name).strip()
    if not name:
        return None
    return name, int(deck_id)


def _all_deck_names_and_ids(col) -> list[tuple[str, int]]:
    decks = []
    try:
        decks = list(col.decks.all_names_and_ids())
    except Exception:
        try:
            decks = list(col.decks.all())
        except Exception:
            decks = list(getattr(col.decks, "decks", {}).values())

    pairs = []
    seen_ids = set()
    for item in decks:
        pair = _deck_pair_from_item(item)
        if not pair:
            continue
        name, deck_id = pair
        if deck_id in seen_ids:
            continue
        seen_ids.add(deck_id)
        pairs.append((name, deck_id))
    return sorted(pairs, key=lambda item: item[0].lower())


def _configured_fields_by_note_type(config: dict) -> dict[str, list[str]]:
    configured = config.get("fields_by_note_type", {})
    if not isinstance(configured, dict):
        return {}
    return {
        str(note_type).strip(): _string_list(fields)
        for note_type, fields in configured.items()
        if str(note_type).strip()
    }


def _fields_for_model(config: dict, model_name: str) -> list[str]:
    fields_by_note_type = _configured_fields_by_note_type(config)
    fields = fields_by_note_type.get(model_name)
    if fields:
        return fields
    return _string_list(config.get("fields", []))


def _matching_deck_ids(
    col, configured_decks: list[str]
) -> tuple[set[int] | None, list[str]]:
    if not configured_decks:
        return None, []

    all_decks = _all_deck_names_and_ids(col)
    deck_ids: set[int] = set()
    missing_decks: list[str] = []
    for configured_name in configured_decks:
        matched = {
            deck_id
            for name, deck_id in all_decks
            if name == configured_name or name.startswith(f"{configured_name}::")
        }
        if matched:
            deck_ids.update(matched)
        else:
            missing_decks.append(configured_name)
    return deck_ids, missing_decks


def _note_ids_for_model(col, model_id: int, deck_ids: set[int] | None) -> list[int]:
    if deck_ids is None:
        return col.db.list("select id from notes where mid=?", model_id)
    if not deck_ids:
        return []

    ordered_deck_ids = sorted(deck_ids)
    placeholders = ",".join("?" for _ in ordered_deck_ids)
    return col.db.list(
        f"""
        select distinct n.id
        from notes n
        join cards c on c.nid = n.id
        where n.mid = ?
          and (c.did in ({placeholders}) or c.odid in ({placeholders}))
        """,
        model_id,
        *ordered_deck_ids,
        *ordered_deck_ids,
    )


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
    style = html_module.unescape(style)
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
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    in_string: str | None = None
    i = 0
    while i < len(style):
        ch = style[i]
        if in_string:
            if ch == in_string:
                backslashes = 0
                j = i - 1
                while j >= 0 and style[j] == "\\":
                    backslashes += 1
                    j -= 1
                if backslashes % 2 == 0:
                    in_string = None
            current.append(ch)
            i += 1
            continue
        if ch in ("'", '"'):
            in_string = ch
            current.append(ch)
            i += 1
            continue
        if ch == "(":
            depth += 1
            current.append(ch)
            i += 1
            continue
        if ch == ")" and depth > 0:
            depth -= 1
            current.append(ch)
            i += 1
            continue
        if ch == ";" and depth == 0:
            part = "".join(current).strip()
            if part:
                parts.append(part)
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)

    important_parts: list[str] = []
    for part in parts:
        if ":" not in part:
            important_parts.append(part)
            continue
        if re.search(r"!important\b", part, re.IGNORECASE):
            important_parts.append(part)
        else:
            important_parts.append(f"{part} !important")
    body = ";".join(important_parts)
    if body and not body.endswith(";"):
        body += ";"
    return f".{class_name} {{ {body} }}"


def _parse_opening_tag(
    tag: str,
) -> tuple[str, list[tuple[str, str | None, str | None]], bool] | None:
    if not tag.startswith("<") or not tag.endswith(">"):
        return None
    if tag.startswith("</"):
        return None
    inner = tag[1:-1].strip()
    if not inner:
        return None
    self_close = False
    if inner.endswith("/"):
        self_close = True
        inner = inner[:-1].rstrip()
    if not inner:
        return None
    i = 0
    n = len(inner)
    while i < n and not inner[i].isspace():
        i += 1
    tag_name = inner[:i]
    attrs_str = inner[i:]
    attrs: list[tuple[str, str | None, str | None]] = []
    i = 0
    n = len(attrs_str)
    while i < n:
        while i < n and attrs_str[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        while i < n and not attrs_str[i].isspace() and attrs_str[i] not in ("=", ">"):
            i += 1
        name = attrs_str[start:i]
        if not name:
            break
        while i < n and attrs_str[i].isspace():
            i += 1
        value: str | None = None
        quote: str | None = None
        if i < n and attrs_str[i] == "=":
            i += 1
            while i < n and attrs_str[i].isspace():
                i += 1
            if i < n and attrs_str[i] in ('"', "'"):
                quote = attrs_str[i]
                i += 1
                val_start = i
                while i < n and attrs_str[i] != quote:
                    i += 1
                value = attrs_str[val_start:i]
                if i < n:
                    i += 1
            else:
                val_start = i
                while i < n and not attrs_str[i].isspace():
                    i += 1
                value = attrs_str[val_start:i]
        attrs.append((name, value, quote))
    return tag_name, attrs, self_close


def _format_opening_tag(
    tag_name: str, attrs: list[tuple[str, str | None, str | None]], self_close: bool
) -> str:
    parts = [f"<{tag_name}"]
    for name, value, quote in attrs:
        if value is None:
            parts.append(f" {name}")
            continue
        if quote is None:
            parts.append(f" {name}={value}")
        else:
            parts.append(f" {name}={quote}{value}{quote}")
    if self_close:
        parts.append(" />")
    else:
        parts.append(">")
    return "".join(parts)


def _iter_inline_style_attrs(html: str) -> Iterable[str]:
    for match in TAG_RE.finditer(html):
        tag = match.group(0)
        parsed = _parse_opening_tag(tag)
        if not parsed:
            continue
        _, attrs, _ = parsed
        for name, value, _ in attrs:
            if name.lower() == "style" and value is not None:
                yield value


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
            if "style=" not in text.lower():
                continue
            for raw in _iter_inline_style_attrs(text):
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
    if "style=" not in html.lower():
        return html, 0, 0

    removed_bytes = 0
    extracted = 0

    def repl(match: re.Match) -> str:
        nonlocal removed_bytes, extracted
        tag = match.group(0)
        parsed = _parse_opening_tag(tag)
        if not parsed:
            return tag
        tag_name, attrs, self_close = parsed
        raw_style = None
        for name, value, _ in attrs:
            if name.lower() == "style" and value is not None:
                raw_style = value
                break
        if raw_style is None:
            return tag
        norm = _normalize_inline_style(raw_style)
        class_name = style_map.get(norm)
        if not class_name:
            return tag

        new_attrs: list[tuple[str, str | None, str | None]] = []
        class_updated = False
        for name, value, quote in attrs:
            if name.lower() == "style":
                continue
            if name.lower() == "class" and value is not None:
                classes = value.split()
                if class_name not in classes:
                    value = f"{value} {class_name}" if value else class_name
                class_updated = True
            new_attrs.append((name, value, quote))
        if not class_updated:
            new_attrs.append(("class", class_name, '"'))

        new_tag = _format_opening_tag(tag_name, new_attrs, self_close)
        removed_bytes += len(tag) - len(new_tag)
        extracted += 1
        return new_tag

    new_html = TAG_RE.sub(repl, html)
    return new_html, removed_bytes, extracted


def _read_text(path: Path) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return ""


def _sync_edited_media_files() -> None:
    media_path = Path(mw.pm.profileFolder(), "collection.media")
    media_path.touch()


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


def _cleanup_model(
    col, model, fields: list[str], config: dict, deck_ids: set[int] | None
):
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

    nids = _note_ids_for_model(col, model["id"], deck_ids)
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
                    if not IMPORT_RE.search(new_value):
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
        _sync_edited_media_files()

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


def _run_cleanup(col, config: dict | None = None) -> CleanupResult:
    config = config or _get_config()
    note_types = _string_list(config.get("note_types", []))
    configured_decks = _string_list(config.get("decks", []))
    configured_fields = _string_list(config.get("fields", []))
    fields_by_note_type = _configured_fields_by_note_type(config)

    if not configured_fields and not any(fields_by_note_type.values()):
        raise Exception("No target fields configured.")

    deck_ids, missing_decks = _matching_deck_ids(col, configured_decks)

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
        fields = _fields_for_model(config, model["name"])
        if not fields:
            continue
        summary, changes = _cleanup_model(col, model, fields, config, deck_ids)
        summaries.append((model["name"], summary))
        all_changes.append(changes)

    return CleanupResult(
        changes=_merge_changes(all_changes),
        summaries=summaries,
        missing_note_types=missing_note_types,
        missing_decks=missing_decks,
    )


def _format_cleanup_summary(result: CleanupResult) -> str:
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

    if result.missing_decks:
        missing = ", ".join(result.missing_decks)
        lines.append(f"Missing decks (skipped): {missing}")

    lines.append("After cleanup, run Tools → Check Database to shrink the collection.")
    return "\n".join(lines).strip()


def _on_cleanup_done(result: CleanupResult) -> None:
    showInfo(_format_cleanup_summary(result), parent=mw)


class CleanupDialog(QDialog):
    def __init__(self) -> None:
        super().__init__(mw)
        self.setWindowTitle("Inline CSS Cleanup")
        self.resize(920, 680)
        self.config = _get_config()
        self.deck_checkboxes: dict[str, QCheckBox] = {}
        self.note_type_names: list[str] = []
        self.selected_note_type_names: list[str] = []
        self.field_checkboxes: dict[str, dict[str, QCheckBox]] = {}
        self.no_fields_label: QLabel | None = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout()

        layout.addWidget(QLabel("Decks"))
        self.all_decks_checkbox = QCheckBox("All decks")
        self.all_decks_checkbox.setChecked(not _string_list(self.config.get("decks")))
        self.all_decks_checkbox.toggled.connect(self._update_deck_controls)
        layout.addWidget(self.all_decks_checkbox)

        deck_scroll = QScrollArea()
        deck_scroll.setWidgetResizable(True)
        deck_scroll.setMinimumHeight(88)
        deck_scroll.setMaximumHeight(150)
        deck_container = QWidget()
        deck_layout = QVBoxLayout()
        deck_layout.setContentsMargins(6, 4, 6, 4)

        configured_decks = set(_string_list(self.config.get("decks")))
        for name, _deck_id in _all_deck_names_and_ids(mw.col):
            checkbox = QCheckBox(name)
            checkbox.setChecked(name in configured_decks)
            deck_layout.addWidget(checkbox)
            self.deck_checkboxes[name] = checkbox
        if not self.deck_checkboxes:
            deck_layout.addWidget(QLabel("No decks found."))
        deck_layout.addStretch()
        deck_container.setLayout(deck_layout)
        deck_scroll.setWidget(deck_container)
        layout.addWidget(deck_scroll)

        layout.addWidget(QLabel("Note Types and Fields"))
        note_type_section = QHBoxLayout()
        note_type_panel = QWidget()
        note_type_panel.setMaximumWidth(300)
        note_type_panel_layout = QVBoxLayout()
        note_type_panel_layout.setContentsMargins(0, 0, 0, 0)
        note_type_panel_layout.addWidget(QLabel("Target note types"))

        note_type_scroll = QScrollArea()
        note_type_scroll.setWidgetResizable(True)
        note_type_scroll.setMinimumHeight(190)
        note_type_container = QWidget()
        self.note_type_list_layout = QVBoxLayout()
        self.note_type_list_layout.setContentsMargins(6, 4, 6, 4)
        note_type_container.setLayout(self.note_type_list_layout)
        note_type_scroll.setWidget(note_type_container)
        note_type_panel_layout.addWidget(note_type_scroll)

        add_note_type_row = QHBoxLayout()
        self.add_note_type_combo = QComboBox()
        self.add_note_type_button = QPushButton("Add")
        self.add_note_type_button.setAutoDefault(False)
        self.add_note_type_button.clicked.connect(self._add_selected_note_type)
        add_note_type_row.addWidget(self.add_note_type_combo, 1)
        add_note_type_row.addWidget(self.add_note_type_button)
        note_type_panel_layout.addLayout(add_note_type_row)

        field_panel = QWidget()
        field_panel_layout = QVBoxLayout()
        field_panel_layout.setContentsMargins(0, 0, 0, 0)
        field_selector_row = QHBoxLayout()
        field_selector_row.addWidget(QLabel("Fields for"))
        self.note_type_combo = QComboBox()
        field_selector_row.addWidget(self.note_type_combo)
        field_selector_row.addStretch()
        field_panel_layout.addLayout(field_selector_row)

        self.fields_scroll = QScrollArea()
        self.fields_scroll.setWidgetResizable(True)
        self.fields_widget = QWidget()
        self.fields_layout = QGridLayout()
        self.fields_layout.setContentsMargins(6, 4, 6, 4)
        self.fields_widget.setLayout(self.fields_layout)
        self.fields_scroll.setWidget(self.fields_widget)
        field_panel_layout.addWidget(self.fields_scroll)
        field_panel.setLayout(field_panel_layout)

        configured_note_types = set(_string_list(self.config.get("note_types")))
        select_all_note_types = not configured_note_types
        fields_by_note_type = _configured_fields_by_note_type(self.config)
        global_fields = set(_string_list(self.config.get("fields", [])))
        model_pairs = _all_note_type_models(mw.col)
        if not model_pairs:
            model_pairs = [
                (name, {"flds": []}) for name in sorted(configured_note_types)
            ]

        for name, model in model_pairs:
            self.note_type_names.append(name)
            if select_all_note_types or name in configured_note_types:
                self.selected_note_type_names.append(name)

            selected_fields = set(fields_by_note_type.get(name) or global_fields)
            field_checkboxes: dict[str, QCheckBox] = {}
            for field_name in _field_names_from_model(model):
                field_checkbox = QCheckBox(field_name)
                field_checkbox.setChecked(field_name in selected_fields)
                field_checkboxes[field_name] = field_checkbox

            self.field_checkboxes[name] = field_checkboxes

        if not self.note_type_names:
            self.selected_note_type_names = []

        note_type_panel.setLayout(note_type_panel_layout)
        note_type_section.addWidget(note_type_panel)
        note_type_section.addWidget(field_panel, 1)
        layout.addLayout(note_type_section)

        self.note_type_combo.currentIndexChanged.connect(self._render_field_controls)
        self._refresh_note_type_views()

        option_row = QHBoxLayout()
        self.extract_inline_styles_checkbox = QCheckBox(
            "Extract repeated inline styles"
        )
        self.extract_inline_styles_checkbox.setChecked(
            bool(self.config.get("extract_inline_styles", False))
        )
        option_row.addWidget(self.extract_inline_styles_checkbox)

        option_row.addWidget(QLabel("Min length"))
        self.inline_style_min_length_spin = QSpinBox()
        self.inline_style_min_length_spin.setRange(0, 100000)
        self.inline_style_min_length_spin.setValue(
            int(self.config.get("inline_style_min_length", 80))
        )
        option_row.addWidget(self.inline_style_min_length_spin)

        option_row.addWidget(QLabel("Min ratio"))
        self.inline_style_min_ratio_spin = QDoubleSpinBox()
        self.inline_style_min_ratio_spin.setRange(0, 100)
        self.inline_style_min_ratio_spin.setDecimals(4)
        self.inline_style_min_ratio_spin.setSingleStep(0.01)
        self.inline_style_min_ratio_spin.setValue(
            float(self.config.get("inline_style_min_ratio", 0.05))
        )
        option_row.addWidget(self.inline_style_min_ratio_spin)

        self.confirm_before_run_checkbox = QCheckBox("Confirm before running")
        self.confirm_before_run_checkbox.setChecked(
            bool(self.config.get("confirm_before_run", True))
        )
        option_row.addWidget(self.confirm_before_run_checkbox)
        option_row.addStretch()
        layout.addLayout(option_row)

        button_row = QHBoxLayout()
        self.run_button = QPushButton("Run Cleanup")
        self.run_button.setDefault(True)
        self.run_button.clicked.connect(self.run_cleanup)
        button_row.addWidget(self.run_button)

        self.save_button = QPushButton("Save Defaults")
        self.save_button.setAutoDefault(False)
        self.save_button.clicked.connect(self.save_defaults)
        button_row.addWidget(self.save_button)
        button_row.addStretch()
        layout.addLayout(button_row)

        self.output = QTextBrowser()
        self.output.setPlainText("")
        layout.addWidget(self.output)

        self.setLayout(layout)
        self._update_deck_controls()
        QTimer.singleShot(0, self.run_button.setFocus)

    def _update_deck_controls(self) -> None:
        enabled = not self.all_decks_checkbox.isChecked()
        for checkbox in self.deck_checkboxes.values():
            checkbox.setEnabled(enabled)

    def _clear_fields_layout(self) -> None:
        while self.fields_layout.count():
            item = self.fields_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

    def _clear_note_type_list_layout(self) -> None:
        while self.note_type_list_layout.count():
            item = self.note_type_list_layout.takeAt(0)
            widget = item.widget()
            if widget:
                widget.setParent(None)

    def _refresh_note_type_list(self) -> None:
        self._clear_note_type_list_layout()
        if not self.selected_note_type_names:
            self.note_type_list_layout.addWidget(QLabel("No target note types."))
        for note_type in self.selected_note_type_names:
            row_widget = QWidget()
            row_layout = QHBoxLayout()
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.addWidget(QLabel(note_type), 1)
            remove_button = QPushButton("Remove")
            remove_button.clicked.connect(
                lambda checked=False, name=note_type: self._remove_note_type(name)
            )
            row_layout.addWidget(remove_button)
            row_widget.setLayout(row_layout)
            self.note_type_list_layout.addWidget(row_widget)
        self.note_type_list_layout.addStretch()

    def _refresh_add_note_type_combo(self) -> None:
        selected = set(self.selected_note_type_names)
        self.add_note_type_combo.blockSignals(True)
        self.add_note_type_combo.clear()
        for note_type in self.note_type_names:
            if note_type not in selected:
                self.add_note_type_combo.addItem(note_type, note_type)
        self.add_note_type_combo.blockSignals(False)
        self.add_note_type_button.setEnabled(self.add_note_type_combo.count() > 0)

    def _refresh_note_type_views(self, preferred_note_type: str | None = None) -> None:
        self._refresh_note_type_list()
        self._refresh_add_note_type_combo()
        self._refresh_note_type_combo(preferred_note_type)

    def _add_selected_note_type(self) -> None:
        note_type = self.add_note_type_combo.currentData()
        if not note_type:
            return
        note_type = str(note_type)
        if note_type not in self.selected_note_type_names:
            self.selected_note_type_names.append(note_type)
        self._refresh_note_type_views(note_type)

    def _remove_note_type(self, note_type: str) -> None:
        self.selected_note_type_names = [
            name for name in self.selected_note_type_names if name != note_type
        ]
        self._refresh_note_type_views()

    def _refresh_note_type_combo(self, preferred_note_type: str | None = None) -> None:
        current_note_type = self.note_type_combo.currentData()
        selected_note_types = self.selected_note_types()
        target_note_type = ""
        if preferred_note_type in selected_note_types:
            target_note_type = preferred_note_type or ""
        elif current_note_type in selected_note_types:
            target_note_type = str(current_note_type)
        elif selected_note_types:
            target_note_type = selected_note_types[0]

        self.note_type_combo.blockSignals(True)
        self.note_type_combo.clear()
        for note_type in selected_note_types:
            self.note_type_combo.addItem(note_type, note_type)

        if target_note_type:
            for index in range(self.note_type_combo.count()):
                if self.note_type_combo.itemData(index) == target_note_type:
                    self.note_type_combo.setCurrentIndex(index)
                    break
        self.note_type_combo.blockSignals(False)
        self._render_field_controls()

    def _render_field_controls(self, _index: int | None = None) -> None:
        self._clear_fields_layout()
        note_type = self.note_type_combo.currentData()
        if not note_type:
            self.fields_layout.addWidget(QLabel("Select at least one note type."), 0, 0)
            return

        enabled = note_type in self.selected_note_type_names
        fields = self.field_checkboxes.get(note_type, {})
        if not fields:
            self.no_fields_label = QLabel("No fields")
            self.fields_layout.addWidget(self.no_fields_label, 0, 0)
            return

        columns = 3
        for index, (field_name, checkbox) in enumerate(fields.items()):
            checkbox.setEnabled(enabled)
            self.fields_layout.addWidget(checkbox, index // columns, index % columns)
        self.fields_layout.setColumnStretch(columns, 1)

    def selected_decks(self) -> list[str]:
        if self.all_decks_checkbox.isChecked():
            return []
        return [
            name
            for name, checkbox in self.deck_checkboxes.items()
            if checkbox.isChecked()
        ]

    def selected_note_types(self) -> list[str]:
        return list(self.selected_note_type_names)

    def selected_fields_by_note_type(self) -> dict[str, list[str]]:
        fields_by_note_type: dict[str, list[str]] = {}
        for note_type in self.selected_note_types():
            fields_by_note_type[note_type] = [
                field_name
                for field_name, checkbox in self.field_checkboxes.get(
                    note_type, {}
                ).items()
                if checkbox.isChecked()
            ]
        return fields_by_note_type

    def selected_config(self) -> dict:
        fields_by_note_type = self.selected_fields_by_note_type()
        fields = []
        seen_fields = set()
        for note_type_fields in fields_by_note_type.values():
            for field_name in note_type_fields:
                if field_name not in seen_fields:
                    seen_fields.add(field_name)
                    fields.append(field_name)

        config = dict(self.config)
        config.update(
            {
                "decks": self.selected_decks(),
                "note_types": self.selected_note_types(),
                "fields": fields,
                "fields_by_note_type": fields_by_note_type,
                "confirm_before_run": self.confirm_before_run_checkbox.isChecked(),
                "extract_inline_styles": (
                    self.extract_inline_styles_checkbox.isChecked()
                ),
                "inline_style_min_length": (self.inline_style_min_length_spin.value()),
                "inline_style_min_ratio": self.inline_style_min_ratio_spin.value(),
            }
        )
        return config

    def validate_selection(self, config: dict) -> None:
        if not self.all_decks_checkbox.isChecked() and not config["decks"]:
            raise ValueError("Select at least one deck, or choose All decks.")
        if not config["note_types"]:
            raise ValueError("Select at least one note type.")
        if not config["fields"]:
            raise ValueError("Select at least one field.")

        missing_fields = [
            note_type
            for note_type, fields in config["fields_by_note_type"].items()
            if not fields
        ]
        if missing_fields:
            names = ", ".join(missing_fields)
            raise ValueError(f"Select at least one field for: {names}")

    def save_defaults(self) -> None:
        try:
            config = self.selected_config()
            self.validate_selection(config)
        except Exception as exc:
            showWarning(str(exc), parent=self)
            return

        mw.addonManager.writeConfig(__name__, config)
        self.config = config
        showInfo("Defaults saved.", parent=self)

    def run_cleanup(self) -> None:
        try:
            config = self.selected_config()
            self.validate_selection(config)
        except Exception as exc:
            showWarning(str(exc), parent=self)
            return

        if config.get("confirm_before_run", True):
            ok = askUser(
                "Inline CSS Cleanup will:\n"
                "• Remove <style>…</style> blocks from the selected fields\n"
                "• Write merged CSS to collection.media/_extracted_css.css\n"
                "• Insert <style>@import ...</style> into fields as needed\n\n"
                "Continue?",
                parent=self,
            )
            if not ok:
                return

        self.output.setPlainText("Running cleanup...")
        self.run_button.setEnabled(False)
        self.save_button.setEnabled(False)
        CollectionOp(parent=self, op=lambda col: _run_cleanup(col, config)).success(
            self.on_cleanup_done
        ).failure(self.on_cleanup_failed).run_in_background()

    def on_cleanup_done(self, result: CleanupResult) -> None:
        self.output.setPlainText(_format_cleanup_summary(result))
        self.run_button.setEnabled(True)
        self.save_button.setEnabled(True)

    def on_cleanup_failed(self, exc: Exception) -> None:
        message = str(exc)
        self.output.setPlainText(message)
        self.run_button.setEnabled(True)
        self.save_button.setEnabled(True)
        showWarning(message, parent=self)


def on_run_cleanup() -> None:
    dialog = CleanupDialog()
    dialog.exec()


def setup() -> None:
    action = QAction("Inline CSS Cleanup", mw)
    action.triggered.connect(on_run_cleanup)
    mw.form.menuTools.addAction(action)


setup()
