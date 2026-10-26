from __future__ import annotations

import base64
import io
import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

import qrcode
from PIL import Image, ImageDraw, ImageFont, ImageOps

Style = Literal["note", "receipt", "quote", "sticker", "task"]

STYLES: tuple[Style, ...] = ("note", "receipt", "quote", "sticker", "task")

# A few symbols that commonly appear in agent-written messages and that some
# bitmap fonts do not have.  Replacing them keeps the output predictable.
SYMBOL_MAP = {
    "✅": "[x]",
    "✔": "[x]",
    "☑": "[x]",
    "❌": "[ ]",
    "✗": "[ ]",
    "⚠": "!",
    "→": "->",
    "←": "<-",
    "↔": "<->",
    "•": "-",
    "…": "...",
    "—": "-",
    "–": "-",
    "−": "-",
    "×": "x",
    "✓": "[x]",
}


@dataclass
class PrintRequest:
    message: str = ""
    title: str | None = None
    style: Style = "note"
    footer: str | None = None
    qr: str | None = None
    image: Image.Image | None = None
    invert_image: bool = False
    width_px: int = 384
    density: int = 1
    feed_dots: int = 80
    label_mode: bool = False
    source: str | None = None


@dataclass
class RenderedJob:
    image: Image.Image
    style: str
    title: str
    width: int
    height: int
    created_at: str
    has_image: bool = False
    has_qr: bool = False
    meta: dict[str, object] = field(default_factory=dict)

    def png_bytes(self) -> bytes:
        out = io.BytesIO()
        self.image.save(out, format="PNG", optimize=True)
        return out.getvalue()

    def save_png(self, path: Path | str) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        self.image.save(target, format="PNG", optimize=True)
        return target


# ---------------------------------------------------------------------------
# Fonts


_FONT_CANDIDATES: dict[str, list[str]] = {
    "sans": [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/SFNS.ttf",
        "/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ],
    "sans_bold": [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ],
    "sans_italic": [
        "/System/Library/Fonts/Supplemental/Arial Italic.ttf",
        "C:/Windows/Fonts/ariali.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Italic.ttf",
    ],
    "sans_bolditalic": [
        "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf",
        "C:/Windows/Fonts/arialbi.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-BoldItalic.ttf",
    ],
    "mono": [
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Supplemental/Courier New.ttf",
        "C:/Windows/Fonts/consola.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ],
    "serif": [
        "/System/Library/Fonts/Supplemental/Georgia.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman.ttf",
        "C:/Windows/Fonts/georgia.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
    ],
    "serif_bold": [
        "/System/Library/Fonts/Supplemental/Georgia Bold.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
        "C:/Windows/Fonts/georgiab.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
    ],
    "serif_italic": [
        "/System/Library/Fonts/Supplemental/Georgia Italic.ttf",
        "/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf",
        "C:/Windows/Fonts/georgiai.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    ],
}

_ROLE_FALLBACK = {
    "sans": "sans",
    "sans_bold": "sans",
    "sans_italic": "sans",
    "sans_bolditalic": "sans_bold",
    "mono": "sans",
    "serif": "sans",
    "serif_bold": "sans_bold",
    "serif_italic": "sans_italic",
}


def _first_existing(paths: Iterable[str]) -> str | None:
    for path in paths:
        if Path(path).exists():
            return path
    return None


class FontBook:
    def __init__(self) -> None:
        self._paths = {role: _first_existing(paths) for role, paths in _FONT_CANDIDATES.items()}
        self._cache: dict[tuple[str, int], ImageFont.ImageFont] = {}

    def get(self, role: str, size: int) -> ImageFont.ImageFont:
        size = max(6, int(size))
        key = (role, size)
        if key in self._cache:
            return self._cache[key]

        path = self._paths.get(role) or self._paths.get(_ROLE_FALLBACK.get(role, "sans"))
        font: ImageFont.ImageFont | None = None
        if path:
            try:
                font = ImageFont.truetype(path, size)
            except OSError:
                font = None
        if font is None:
            try:
                font = ImageFont.load_default(size=size)
            except TypeError:  # Pillow < 10.1
                font = ImageFont.load_default()
        self._cache[key] = font
        return font


# ---------------------------------------------------------------------------
# Lightweight markdown


_TOKEN_RE = re.compile(
    r"(\*\*.+?\*\*|__.+?__|`[^`\n]+`|\*[^*\n]+?\*|_[^_\n]+?_)",
    re.DOTALL,
)


def sanitize_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text or "")
    for old, new in SYMBOL_MAP.items():
        text = text.replace(old, new)
    return text.replace("\t", "    ").replace("\r\n", "\n").replace("\r", "\n")


def strip_inline_markup(text: str) -> str:
    """Remove markdown that cannot be represented by a single font run."""
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"<((?:https?|mailto):[^>]+)>", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    text = re.sub(r"\\([\\`*_{}\[\]()#+\-.!])", r"\1", text)
    return text


def parse_inline(text: str) -> list[tuple[str, str]]:
    """Split a paragraph into (text, style) segments."""
    text = strip_inline_markup(text)
    out: list[tuple[str, str]] = []
    pos = 0
    for match in _TOKEN_RE.finditer(text):
        if match.start() > pos:
            out.append((text[pos : match.start()], "regular"))
        token = match.group(0)
        if token.startswith("**") or token.startswith("__"):
            out.append((token[2:-2], "bold"))
        elif token.startswith("`"):
            out.append((token[1:-1], "code"))
        else:
            out.append((token[1:-1], "italic"))
        pos = match.end()
    if pos < len(text):
        out.append((text[pos:], "regular"))
    if not out:
        out.append(("", "regular"))
    # Merge adjacent segments with the same style.
    merged: list[tuple[str, str]] = []
    for segment, style in out:
        if merged and merged[-1][1] == style:
            merged[-1] = (merged[-1][0] + segment, style)
        else:
            merged.append((segment, style))
    return merged


@dataclass
class _Run:
    text: str
    font: ImageFont.ImageFont


def _measure(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont) -> float:
    if not text:
        return 0.0
    try:
        return float(draw.textlength(text, font=font))
    except Exception:
        return float(len(text) * max(1, getattr(font, "size", 8)) * 0.55)


def _ascent(font: ImageFont.ImageFont) -> int:
    """Font-constant ascent, so words do not shift based on their glyphs."""
    try:
        ascent, _descent = font.getmetrics()
        return int(ascent)
    except Exception:
        return int(max(1, getattr(font, "size", 8)) * 0.9)


def _runs_for(
    text: str,
    book: FontBook,
    size: int,
    *,
    base: str = "regular",
    heading: bool = False,
) -> list[_Run]:
    styles = parse_inline(text)
    runs: list[_Run] = []
    for segment, style in styles:
        if not segment:
            continue
        if style == "code":
            role = "mono"
        elif heading:
            role = "sans_bold" if base not in {"serif", "serif_bold"} else "serif_bold"
        elif style == "bold":
            role = "serif_bold" if base.startswith("serif") else "sans_bold"
        elif style == "italic":
            role = "serif_italic" if base.startswith("serif") else "sans_italic"
        elif style == "bolditalic":
            role = "serif_bold" if base.startswith("serif") else "sans_bolditalic"
        else:
            role = base
        runs.append(_Run(segment, book.get(role, size)))
    return runs


def _layout_runs(
    draw: ImageDraw.ImageDraw,
    runs: Sequence[_Run],
    max_width: float,
) -> list[list[_Run]]:
    """Greedy word wrap that preserves per-word fonts."""
    lines: list[list[_Run]] = []
    line: list[_Run] = []
    width = 0.0
    need_space = False

    def flush() -> None:
        nonlocal line, width, need_space
        if line:
            lines.append(line)
        line = []
        width = 0.0
        need_space = False

    for run in runs:
        for token in re.findall(r"\s+|\S+", run.text):
            if token.isspace():
                need_space = True
                continue

            word_w = _measure(draw, token, run.font)
            space_w = _measure(draw, " ", run.font) if (line and need_space) else 0.0

            if word_w <= max_width and width + space_w + word_w <= max_width:
                if space_w:
                    line.append(_Run(" ", run.font))
                    width += space_w
                line.append(_Run(token, run.font))
                width += word_w
                need_space = False
                continue

            if line:
                flush()
                space_w = 0.0

            if word_w <= max_width:
                line.append(_Run(token, run.font))
                width = word_w
                need_space = False
                continue

            # Extremely long token: break it by character.
            for char in token:
                char_w = _measure(draw, char, run.font)
                if line and width + char_w > max_width:
                    flush()
                line.append(_Run(char, run.font))
                width += char_w
            need_space = False

    flush()
    return lines


class Canvas:
    def __init__(self, width: int = 384, margin: int = 18, max_height: int = 8000) -> None:
        self.width = width
        self.margin = margin
        self.max_height = max_height
        self.image = Image.new("L", (width, min(max_height, 2400)), 255)
        self.draw = ImageDraw.Draw(self.image)
        self.y = margin

    @property
    def right(self) -> int:
        return self.width - self.margin

    @property
    def content_width(self) -> int:
        return self.width - 2 * self.margin

    def _ensure(self, extra: int) -> None:
        needed = self.y + max(0, extra) + self.margin
        if needed <= self.image.height:
            return
        new_height = min(self.max_height, max(needed + 400, int(self.image.height * 1.35)))
        if new_height <= self.image.height:
            return
        new_image = Image.new("L", (self.width, new_height), 255)
        new_image.paste(self.image, (0, 0))
        self.image = new_image
        self.draw = ImageDraw.Draw(new_image)

    def gap(self, px: int) -> None:
        self.y += max(0, int(px))

    def rule(self, *, x0: int | None = None, x1: int | None = None, width: int = 1) -> None:
        x0 = self.margin if x0 is None else x0
        x1 = self.right if x1 is None else x1
        self._ensure(width + 2)
        self.draw.line((x0, self.y, x1, self.y), fill=0, width=max(1, width))
        self.y += max(1, width)

    def dashed_rule(self, *, x0: int | None = None, x1: int | None = None, dash: int = 4, gap: int = 3) -> None:
        x0 = self.margin if x0 is None else x0
        x1 = self.right if x1 is None else x1
        self._ensure(2)
        x = x0
        while x < x1:
            self.draw.line((x, self.y, min(x + dash, x1), self.y), fill=0)
            x += dash + gap
        self.y += 1

    def text(
        self,
        x: int,
        text: str,
        font: ImageFont.ImageFont,
        *,
        fill: int = 0,
        anchor: str = "la",
        baseline: int | None = None,
    ) -> None:
        if not text:
            return
        self._ensure(int(getattr(font, "size", 10) * 1.8))
        y = self.y if baseline is None else baseline
        try:
            self.draw.text((x, y), text, font=font, fill=fill, anchor=anchor)
        except (ValueError, TypeError):
            self.draw.text((x, y), text, font=font, fill=fill)

    def paste(self, image: Image.Image, x: int | None = None) -> None:
        x = self.margin if x is None else x
        self._ensure(image.height + 2)
        self.image.paste(image.convert("L"), (x, self.y))
        self.y += image.height

    def crop(self) -> Image.Image:
        bottom = min(self.image.height, max(self.y + self.margin, self.margin * 2))
        return self.image.crop((0, 0, self.width, bottom))


def _draw_runs(
    canvas: Canvas,
    runs: Sequence[_Run],
    *,
    x: int | None = None,
    max_width: int | None = None,
    line_height: int,
    center: bool = False,
    space_after: int = 0,
) -> None:
    x = canvas.margin if x is None else x
    max_width = (canvas.right - x) if max_width is None else max_width
    lines = _layout_runs(canvas.draw, runs, max_width)
    for line in lines:
        while line and line[-1].text.isspace():
            line.pop()
        if not line:
            continue
        total = sum(_measure(canvas.draw, run.text, run.font) for run in line)
        cursor = x + max(0, int((max_width - total) / 2)) if center else x
        # Draw every run on one shared baseline.  Anchoring each word by its
        # own glyph bounds makes words without ascenders sit higher than their
        # neighbours.
        baseline = canvas.y + max(_ascent(run.font) for run in line)
        for run in line:
            canvas.text(cursor, run.text, run.font, anchor="ls", baseline=baseline)
            cursor += _measure(canvas.draw, run.text, run.font)
        canvas.y += line_height
    canvas.y += space_after


def _wrap_plain(
    draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int
) -> list[str]:
    words = text.split()
    if not words:
        return [""]
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or _measure(draw, candidate, font) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    # Split words that are wider than the line.
    out: list[str] = []
    for line in lines:
        if _measure(draw, line, font) <= max_width:
            out.append(line)
            continue
        piece = ""
        for char in line:
            if piece and _measure(draw, piece + char, font) > max_width:
                out.append(piece)
                piece = char
            else:
                piece += char
        if piece:
            out.append(piece)
    return out or [""]


# ---------------------------------------------------------------------------
# Blocks


def _is_rule(line: str) -> bool:
    stripped = line.strip()
    return len(stripped) >= 3 and len(set(stripped)) == 1 and stripped[0] in "-*_"


def _parse_blocks(message: str, *, preserve_lines: bool = False) -> list[tuple[str, object]]:
    lines = message.split("\n")
    blocks: list[tuple[str, object]] = []
    paragraph: list[str] = []
    code: list[str] = []
    in_code = False

    def emit_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            blocks.append(("paragraph", "\n".join(paragraph)))
            paragraph = []

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()

        if stripped.startswith("```"):
            emit_paragraph()
            if in_code:
                blocks.append(("code", "\n".join(code)))
                code = []
                in_code = False
            else:
                in_code = True
            continue

        if in_code:
            code.append(raw)
            continue

        if not stripped:
            emit_paragraph()
            blocks.append(("blank", None))
            continue

        if _is_rule(line) or stripped in {"***", "---"}:
            emit_paragraph()
            blocks.append(("rule", None))
            continue

        heading = re.match(r"^(#{1,3})\s+(.*)$", stripped)
        if heading:
            emit_paragraph()
            blocks.append(("heading", (len(heading.group(1)), heading.group(2))))
            continue

        quote = re.match(r"^>\s?(.*)$", stripped)
        if quote:
            emit_paragraph()
            blocks.append(("quote", quote.group(1)))
            continue

        task = re.match(r"^(?:[-*+]\s+)?\[([ xX])\]\s+(.*)$", stripped)
        if task:
            emit_paragraph()
            blocks.append(("task", (task.group(1).lower() == "x", task.group(2))))
            continue

        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        if bullet:
            emit_paragraph()
            blocks.append(("bullet", bullet.group(1)))
            continue

        numbered = re.match(r"^(\d+)[.)]\s+(.*)$", stripped)
        if numbered:
            emit_paragraph()
            blocks.append(("numbered", (numbered.group(1), numbered.group(2))))
            continue

        if preserve_lines:
            emit_paragraph()
            blocks.append(("line", line))
        else:
            if paragraph and line.endswith("  "):
                emit_paragraph()
            paragraph.append(stripped)

    emit_paragraph()
    if in_code and code:
        blocks.append(("code", "\n".join(code)))
    return blocks


def _draw_heading(canvas: Canvas, level: int, text: str, book: FontBook) -> None:
    size = {1: 27, 2: 22, 3: 18}.get(level, 18)
    line_height = {1: 32, 2: 27, 3: 23}.get(level, 23)
    canvas.gap(5 if canvas.y > canvas.margin else 0)
    _draw_runs(
        canvas,
        _runs_for(text, book, size, heading=True),
        line_height=line_height,
        space_after=4,
    )
    if level == 1:
        canvas.rule(width=2)
        canvas.gap(5)
    else:
        canvas.gap(2)


def _draw_bullet(canvas: Canvas, text: str, book: FontBook, number: str | None = None) -> None:
    marker = f"{number}." if number else "-"
    font = book.get("sans", 16)
    canvas.text(canvas.margin, marker, font)
    _draw_runs(
        canvas,
        _runs_for(text, book, 16),
        x=canvas.margin + 18,
        max_width=canvas.content_width - 18,
        line_height=22,
        space_after=3,
    )


def _draw_task_item(canvas: Canvas, checked: bool, text: str, book: FontBook, size: int = 16) -> None:
    box = 12
    top = canvas.y + 4
    canvas._ensure(box + 6)
    canvas.draw.rectangle(
        (canvas.margin, top, canvas.margin + box, top + box), outline=0, width=1
    )
    if checked:
        canvas.draw.line(
            (canvas.margin + 2, top + 6, canvas.margin + 5, top + 10), fill=0, width=2
        )
        canvas.draw.line(
            (canvas.margin + 5, top + 10, canvas.margin + 10, top + 2), fill=0, width=2
        )
    _draw_runs(
        canvas,
        _runs_for(text, book, size),
        x=canvas.margin + 20,
        max_width=canvas.content_width - 20,
        line_height=size + 8,
        space_after=5,
    )


def _draw_quote(canvas: Canvas, text: str, book: FontBook, size: int = 16) -> None:
    bar_x = canvas.margin
    start_y = canvas.y
    _draw_runs(
        canvas,
        _runs_for(text, book, size, base="serif"),
        x=canvas.margin + 12,
        max_width=canvas.content_width - 12,
        line_height=size + 7,
        space_after=4,
    )
    canvas.draw.line((bar_x, start_y, bar_x, max(start_y, canvas.y - 1)), fill=0, width=2)


def _draw_code(canvas: Canvas, text: str, book: FontBook) -> None:
    canvas.gap(3)
    canvas.dashed_rule()
    canvas.gap(5)
    font = book.get("mono", 13)
    for line in text.split("\n"):
        wrapped = _wrap_plain(canvas.draw, line, font, canvas.content_width - 8) if line else [""]
        for piece in wrapped:
            canvas.text(canvas.margin + 8, piece, font)
            canvas.y += 17
    canvas.gap(4)
    canvas.dashed_rule()
    canvas.gap(6)


def _draw_qr(canvas: Canvas, data: str, book: FontBook) -> None:
    data = data.strip()
    if not data:
        return
    if len(data) > 900:
        raise ValueError("QR data is too long (maximum 900 characters)")
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=4,
        border=2,
    )
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("L")
    max_side = min(190, canvas.content_width)
    if img.width > max_side or img.height > max_side:
        # Nearest-neighbour keeps QR modules crisp.
        ratio = max_side / max(img.width, img.height)
        img = img.resize(
            (max(1, int(img.width * ratio)), max(1, int(img.height * ratio))),
            Image.Resampling.NEAREST,
        )
    canvas.gap(8)
    x = (canvas.width - img.width) // 2
    canvas.paste(img, x)
    caption_font = book.get("mono", 9)
    for line in _wrap_plain(canvas.draw, data, caption_font, canvas.content_width)[:2]:
        w = _measure(canvas.draw, line, caption_font)
        canvas.text(int((canvas.width - w) / 2), line, caption_font)
        canvas.y += 11
    canvas.gap(4)


def _draw_image(canvas: Canvas, image: Image.Image, *, invert: bool = False) -> None:
    prepared = prepare_image(image, canvas.content_width, invert=invert)
    canvas.gap(8)
    canvas.paste(prepared, (canvas.width - prepared.width) // 2)
    canvas.gap(4)


def _draw_footer(canvas: Canvas, request: PrintRequest, book: FontBook, *, compact: bool = False) -> None:
    if request.footer == "":
        return
    custom = (request.footer or "").strip()
    if not custom and compact:
        return
    canvas.gap(7)
    canvas.dashed_rule()
    canvas.gap(4)
    small = book.get("sans", 9)
    if custom:
        for line in custom.split("\n"):
            for wrapped in _wrap_plain(canvas.draw, line, small, canvas.content_width):
                canvas.text(canvas.margin, wrapped, small)
                canvas.y += 11
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    source = f" via {request.source}" if request.source else ""
    footer_line = f"{stamp}{source}"
    w = _measure(canvas.draw, footer_line, small)
    canvas.text(canvas.right - int(w), footer_line, small)
    canvas.y += 11


# ---------------------------------------------------------------------------
# Renderers


def _render_note(canvas: Canvas, request: PrintRequest, book: FontBook, blocks: list[tuple[str, object]]) -> None:
    if request.title:
        _draw_runs(
            canvas,
            _runs_for(request.title, book, 26, heading=True),
            line_height=31,
            space_after=2,
        )
        canvas.rule(width=2)
        canvas.gap(7)
    for kind, value in blocks:
        if kind == "blank":
            canvas.gap(4)
        elif kind == "heading":
            level, text = value  # type: ignore[misc]
            _draw_heading(canvas, int(level), str(text), book)
        elif kind == "rule":
            canvas.gap(5)
            canvas.dashed_rule()
            canvas.gap(7)
        elif kind == "paragraph":
            _draw_runs(
                canvas,
                _runs_for(str(value), book, 16),
                line_height=22,
                space_after=5,
            )
        elif kind == "line":
            _draw_runs(canvas, _runs_for(str(value), book, 16), line_height=22, space_after=3)
        elif kind == "bullet":
            _draw_bullet(canvas, str(value), book)
        elif kind == "numbered":
            number, text = value  # type: ignore[misc]
            _draw_bullet(canvas, str(text), book, number=str(number))
        elif kind == "task":
            checked, text = value  # type: ignore[misc]
            _draw_task_item(canvas, bool(checked), str(text), book)
        elif kind == "quote":
            _draw_quote(canvas, str(value), book)
        elif kind == "code":
            _draw_code(canvas, str(value), book)

    if request.image is not None:
        _draw_image(canvas, request.image, invert=request.invert_image)
    if request.qr:
        _draw_qr(canvas, request.qr, book)
    _draw_footer(canvas, request, book)


def _render_receipt(canvas: Canvas, request: PrintRequest, book: FontBook, blocks: list[tuple[str, object]]) -> None:
    title = (request.title or "RECEIPT").upper()
    _draw_runs(
        canvas,
        _runs_for(title, book, 20, base="mono", heading=True),
        line_height=24,
        center=True,
        space_after=3,
    )
    canvas.gap(3)
    canvas.dashed_rule()
    canvas.gap(6)

    mono = book.get("mono", 14)
    for kind, value in blocks:
        if kind == "blank":
            canvas.gap(5)
        elif kind == "rule":
            canvas.dashed_rule()
            canvas.gap(5)
        elif kind in {"paragraph", "line"}:
            for line in str(value).split("\n"):
                _draw_receipt_line(canvas, line, mono, book)
        elif kind == "heading":
            level, text = value  # type: ignore[misc]
            _draw_runs(
                canvas,
                _runs_for(str(text), book, 17, base="mono", heading=True),
                line_height=21,
                space_after=3,
            )
        elif kind == "bullet":
            _draw_runs(
                canvas,
                _runs_for(f"* {value}", book, 14, base="mono"),
                line_height=19,
                space_after=2,
            )
        elif kind == "numbered":
            number, text = value  # type: ignore[misc]
            _draw_runs(
                canvas,
                _runs_for(f"{number}. {text}", book, 14, base="mono"),
                line_height=19,
                space_after=2,
            )
        elif kind == "task":
            checked, text = value  # type: ignore[misc]
            mark = "[x]" if checked else "[ ]"
            _draw_runs(
                canvas,
                _runs_for(f"{mark} {text}", book, 14, base="mono"),
                line_height=19,
                space_after=2,
            )
        elif kind == "quote":
            _draw_runs(
                canvas,
                _runs_for(f"> {value}", book, 13, base="mono"),
                line_height=18,
                space_after=2,
            )
        elif kind == "code":
            _draw_code(canvas, str(value), book)

    if request.image is not None:
        _draw_image(canvas, request.image, invert=request.invert_image)
    if request.qr:
        _draw_qr(canvas, request.qr, book)
    canvas.gap(4)
    canvas.dashed_rule()
    _draw_footer(canvas, request, book)


def _draw_receipt_line(
    canvas: Canvas, line: str, font: ImageFont.ImageFont, book: FontBook
) -> None:
    """Two-column layout when a line has 2+ spaces between item and amount."""
    match = re.match(r"^(.*?)\s{2,}(\S.*)$", line)
    if match and _measure(canvas.draw, line, font) <= canvas.content_width:
        left, right = match.group(1), match.group(2)
        canvas.text(canvas.margin, left, font)
        rw = _measure(canvas.draw, right, font)
        canvas.text(canvas.right - int(rw), right, font)
        canvas.y += 19
        return
    _draw_runs(
        canvas,
        _runs_for(line, book, 14, base="mono"),
        line_height=19,
        space_after=2,
    )


def _render_quote(canvas: Canvas, request: PrintRequest, book: FontBook, blocks: list[tuple[str, object]]) -> None:
    canvas.gap(2)
    canvas.rule(width=1)
    canvas.gap(14)
    if request.title:
        _draw_runs(
            canvas,
            _runs_for(request.title.upper(), book, 10, heading=True),
            line_height=14,
            center=True,
            space_after=9,
        )
    quote_mark = book.get("serif", 46)
    canvas.text(canvas.margin, "\u201c", quote_mark, anchor="lt")
    canvas.gap(2)

    text_parts: list[str] = []
    for kind, value in blocks:
        if kind in {"paragraph", "line", "quote"} and value:
            text_parts.append(str(value))
        elif kind == "blank":
            text_parts.append("")
        elif kind == "heading":
            _level, text = value  # type: ignore[misc]
            text_parts.append(str(text))
        elif kind == "bullet":
            text_parts.append(f"- {value}")
        elif kind == "task":
            checked, text = value  # type: ignore[misc]
            text_parts.append(f"[{'x' if checked else ' '}] {text}")
        elif kind == "code":
            text_parts.append(str(value))
    quote_text = "\n".join(text_parts).strip()
    _draw_runs(
        canvas,
        _runs_for(quote_text, book, 23, base="serif"),
        line_height=31,
        center=True,
        space_after=10,
    )
    if request.footer:
        _draw_runs(
            canvas,
            _runs_for(request.footer, book, 12, base="serif"),
            line_height=16,
            center=True,
            space_after=6,
        )
    canvas.gap(4)
    canvas.rule(width=1)
    if request.image is not None:
        _draw_image(canvas, request.image, invert=request.invert_image)
    if request.qr:
        _draw_qr(canvas, request.qr, book)


def _render_task(canvas: Canvas, request: PrintRequest, book: FontBook, blocks: list[tuple[str, object]]) -> None:
    title = request.title or "TO DO"
    _draw_runs(
        canvas,
        _runs_for(title, book, 26, heading=True),
        line_height=31,
        space_after=2,
    )
    canvas.rule(width=2)
    stamp = datetime.now().strftime("%a %d %b").upper()
    small = book.get("sans", 9)
    w = _measure(canvas.draw, stamp, small)
    canvas.text(canvas.right - int(w), stamp, small)
    canvas.gap(7)

    any_items = False
    for kind, value in blocks:
        if kind == "blank":
            canvas.gap(4)
        elif kind == "task":
            checked, text = value  # type: ignore[misc]
            _draw_task_item(canvas, bool(checked), str(text), book, size=16)
            any_items = True
        elif kind == "bullet":
            _draw_task_item(canvas, False, str(value), book, size=16)
            any_items = True
        elif kind == "numbered":
            _number, text = value  # type: ignore[misc]
            _draw_task_item(canvas, False, str(text), book, size=16)
            any_items = True
        elif kind in {"paragraph", "line"}:
            for line in str(value).split("\n"):
                _draw_task_item(canvas, False, line, book, size=16)
                any_items = True
        elif kind == "heading":
            _level, text = value  # type: ignore[misc]
            _draw_runs(
                canvas,
                _runs_for(str(text), book, 18, heading=True),
                line_height=23,
                space_after=5,
            )
        elif kind == "rule":
            canvas.dashed_rule()
            canvas.gap(5)
        elif kind == "quote":
            _draw_quote(canvas, str(value), book)
        elif kind == "code":
            _draw_code(canvas, str(value), book)

    if not any_items and request.image is None:
        canvas.gap(2)
        _draw_runs(
            canvas,
            _runs_for("Nothing here yet.", book, 14),
            line_height=20,
            space_after=4,
        )
    if request.image is not None:
        _draw_image(canvas, request.image, invert=request.invert_image)
    if request.qr:
        _draw_qr(canvas, request.qr, book)
    _draw_footer(canvas, request, book)


def _render_sticker(canvas: Canvas, request: PrintRequest, book: FontBook, blocks: list[tuple[str, object]]) -> None:
    # Sticker text is all about impact: flatten blocks and auto-fit the largest
    # font size that still fits a small label.
    parts: list[str] = []
    for kind, value in blocks:
        if kind in {"paragraph", "line", "quote"} and value:
            parts.append(str(value))
        elif kind == "heading":
            _level, text = value  # type: ignore[misc]
            parts.append(str(text))
        elif kind == "bullet":
            parts.append(f"- {value}")
        elif kind == "task":
            checked, text = value  # type: ignore[misc]
            parts.append(f"[{'x' if checked else ' '}] {text}")
        elif kind == "numbered":
            number, text = value  # type: ignore[misc]
            parts.append(f"{number}. {text}")
    message = "\n".join(parts).strip()
    if not message and request.image is None:
        message = request.title or "HELLO"
        request.title = None

    # Border and inner padding.
    border = 8
    inner = 14
    inner_width = canvas.width - 2 * (border + inner)
    max_text_height = 270

    chosen_size = 18
    chosen_lines: list[str] = []
    if message:
        for size in range(64, 17, -2):
            font = book.get("sans_bold", size)
            lines: list[str] = []
            for raw_line in message.split("\n"):
                wrapped = _wrap_plain(canvas.draw, raw_line, font, inner_width)
                lines.extend(wrapped)
            height = len(lines) * int(size * 1.16)
            widest = max((_measure(canvas.draw, line, font) for line in lines), default=0)
            if len(lines) <= 5 and height <= max_text_height and widest <= inner_width:
                chosen_size = size
                chosen_lines = lines
                break
        else:
            font = book.get("sans_bold", 18)
            chosen_lines = _wrap_plain(canvas.draw, message, font, inner_width)

    top = border + inner
    if request.title:
        _draw_runs(
            canvas,
            _runs_for(request.title.upper(), book, 10, heading=True),
            x=border + inner,
            max_width=inner_width,
            line_height=14,
            center=True,
            space_after=5,
        )
        top = canvas.y

    if message:
        font = book.get("sans_bold", chosen_size)
        line_height = int(chosen_size * 1.16)
        canvas.y = max(canvas.y, top)
        for line in chosen_lines:
            w = _measure(canvas.draw, line, font)
            canvas.text(int((canvas.width - w) / 2), line, font)
            canvas.y += line_height
        canvas.gap(2)

    if request.image is not None:
        _draw_image(canvas, request.image, invert=request.invert_image)

    if request.qr:
        _draw_qr(canvas, request.qr, book)

    if request.footer:
        _draw_runs(
            canvas,
            _runs_for(request.footer, book, 9),
            x=border + inner,
            max_width=inner_width,
            line_height=12,
            center=True,
            space_after=2,
        )

    # Draw a rounded border around the finished content.
    bottom = canvas.y + 2
    canvas._ensure(4)
    canvas.draw.rounded_rectangle(
        (border, border, canvas.width - border, bottom),
        radius=12,
        outline=0,
        width=2,
    )
    canvas.y = bottom + border


def prepare_image(image: Image.Image, width: int, *, invert: bool = False) -> Image.Image:
    img = ImageOps.exif_transpose(image)
    if img.mode in {"RGBA", "LA"} or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        background.alpha_composite(rgba)
        img = background
    img = img.convert("L")
    if invert:
        img = ImageOps.invert(img)
    img = ImageOps.autocontrast(img)
    target_w = max(1, width)
    target_h = max(1, int(img.height * target_w / max(1, img.width)))
    img = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
    return img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)


def render(request: PrintRequest) -> RenderedJob:
    style = request.style if request.style in STYLES else "note"
    width = max(128, min(832, int(request.width_px or 384)))
    request.style = style  # type: ignore[assignment]
    request.message = sanitize_text(request.message)
    if request.title:
        request.title = sanitize_text(request.title).strip()
    if request.footer:
        request.footer = sanitize_text(request.footer).strip()

    if not request.message.strip() and request.image is None and not request.qr and not request.title:
        raise ValueError("Nothing to print: provide a message, a title, an image, or a QR code.")

    book = FontBook()
    margin = 22 if style == "quote" else 18
    canvas = Canvas(width=width, margin=margin)
    preserve = style in {"receipt", "task", "sticker"}
    blocks = _parse_blocks(request.message, preserve_lines=preserve)

    if style == "note":
        _render_note(canvas, request, book, blocks)
    elif style == "receipt":
        _render_receipt(canvas, request, book, blocks)
    elif style == "quote":
        _render_quote(canvas, request, book, blocks)
    elif style == "task":
        _render_task(canvas, request, book, blocks)
    else:
        _render_sticker(canvas, request, book, blocks)

    cropped = canvas.crop()
    # Threshold text and line art to pure black/white.  Photos were already
    # dithered by prepare_image(), so their 0/255 values survive unchanged.
    bw = cropped.point(lambda p: 0 if p < 170 else 255).convert("1", dither=Image.Dither.NONE)
    return RenderedJob(
        image=bw,
        style=style,
        title=request.title or "",
        width=bw.width,
        height=bw.height,
        created_at=datetime.now().isoformat(timespec="seconds"),
        has_image=request.image is not None,
        has_qr=bool(request.qr),
        meta={
            "density": request.density,
            "feed_dots": request.feed_dots,
            "label_mode": request.label_mode,
            "source": request.source,
        },
    )


def render_png(request: PrintRequest) -> bytes:
    return render(request).png_bytes()


def image_from_base64(value: str) -> Image.Image:
    if "," in value and value.strip().lower().startswith("data:"):
        value = value.split(",", 1)[1]
    raw = base64.b64decode(value)
    return Image.open(io.BytesIO(raw))


def preview_data_url(image: Image.Image) -> str:
    out = io.BytesIO()
    image.save(out, format="PNG")
    return "data:image/png;base64," + base64.b64encode(out.getvalue()).decode("ascii")
