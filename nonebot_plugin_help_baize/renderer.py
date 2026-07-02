import base64
import io
import os
import json
from collections import OrderedDict
from typing import Dict, List, Sequence, Tuple

from PIL import Image, ImageChops, ImageDraw, ImageFont

from .config import CONFIG, PLUGIN_DIR
from .models import HelpEntry, HelpQueryResult
from .webui_store import get_module_config, DEFAULT_COLORS


_json_path = os.path.join(os.path.dirname(__file__), '_HEADER_SYMBOL_BASE64.json')
with open(_json_path, 'r', encoding='utf-8-sig') as _f:
    _HEADER_SYMBOL_BASE64 = json.load(_f)['_HEADER_SYMBOL_BASE64']
_HEADER_SYMBOL_CACHE: Dict[int, Image.Image] = {}


PAGE_WIDTH = 1280  # 整张帮助图宽度
MAX_PAGE_HEIGHT = 3000  # 单页帮助图最大高度
CONTENT_TOP = 256  # 正文区域起始 Y 坐标
PAGE_BOTTOM_PADDING = 100  # 页面底部留白
CARD_LEFT = 76  # 大卡片左边距
CARD_RIGHT = PAGE_WIDTH - 76  # 大卡片右边界

GRID_COLUMNS = 4  # 每行小气泡数量
GRID_GAP_X = 14  # 小气泡横向间距
GRID_GAP_Y = 14  # 小气泡纵向间距
GRID_TILE_RADIUS = 16  # 小气泡圆角

MAX_TRIGGER_LINES = 2  # 触发词最大行数
MAX_DESC_LINES = 2  # 小气泡描述最大行数
TILE_TEXT_LEFT = 18  # 小气泡文字左边距
TILE_DESC_LINE_HEIGHT = 24  # 描述行间距
TILE_TRIGGER_DESC_GAP = 10  # 触发词标签和描述间距
TILE_BOTTOM_PADDING = 24  # 小气泡底部留白
CARD_DESC_TOP_GAP = 14  # 网格和大卡片底部描述间距
CARD_BOTTOM_PADDING = 28  # 大卡片底部留白
MIN_TILE_HEIGHT = 138  # 小气泡最小高度

BADGE_LEFT = 14  # 触发词标签左边距
BADGE_TOP = 12  # 触发词标签顶部边距
BADGE_PADDING_X = 14  # 触发词标签左右内边距
BADGE_PADDING_Y = 8  # 触发词标签上下内边距
BADGE_RADIUS = 12  # 触发词标签圆角
BADGE_TEXT_LINE_HEIGHT = 28  # 标签内触发词行高
BADGE_TEXT_TOP_ADJUST = 2  # 标签文字微调，避免视觉偏下
BG_OVERLAY_ALPHA = 0  # 背景图上的白色蒙层透明度，0 表示不再额外盖白
OUTER_CARD_ALPHA = 24  # 最外层大卡片透明度
SECTION_CARD_ALPHA = 48  # 每个插件/模块卡片透明度
TILE_CARD_ALPHA = 44  # 小气泡轻微托底
DESC_STRIP_ALPHA = 108  # 模块底部整体描述的承托条透明度
TILE_DESC_STRIP_ALPHA = 124  # 小气泡内部描述的承托条透明度


def _pick_font(size: int, bold: bool = False):
    candidates: List[str] = []
    for item in CONFIG.font_candidates:
        if os.path.isabs(item):
            candidates.append(item)
        else:
            candidates.append(str(PLUGIN_DIR / item))
            candidates.append(str(PLUGIN_DIR.parent / item))

    if bold:
        prioritized = [path for path in candidates if "bd" in path.lower() or "bold" in path.lower()]
        candidates = prioritized + [path for path in candidates if path not in prioritized]

    seen = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        if not os.path.exists(path):
            continue
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _build_fonts() -> Dict[str, object]:
    return {
        "title": _pick_font(54, bold=True),
        "subtitle": _pick_font(24),
        "section": _pick_font(28, bold=True),
        "name": _pick_font(30, bold=True),
        "code": _pick_font(24, bold=True),
        "tiny": _pick_font(20),
    }


def _wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> List[str]:
    lines: List[str] = []
    for paragraph in text.splitlines() or [""]:
        current = ""
        for ch in paragraph:
            trial = current + ch
            width = draw.textbbox((0, 0), trial, font=font)[2]
            if current and width > max_width:
                lines.append(current)
                current = ch
            else:
                current = trial
        lines.append(current or "")
    return lines


def _truncate_lines(lines: List[str], limit: int) -> List[str]:
    if len(lines) <= limit:
        return lines
    trimmed = lines[:limit]
    last = trimmed[-1].rstrip()
    if len(last) >= 2:
        last = last[:-1].rstrip()
    trimmed[-1] = f"{last}..."
    return trimmed


def _group_for_overview(entries: List[HelpEntry]) -> Dict[str, List[HelpEntry]]:
    grouped: Dict[str, List[HelpEntry]] = OrderedDict()
    for entry in entries:
        grouped.setdefault(entry.category, []).append(entry)
    return grouped


def _group_flat(entries: List[HelpEntry]) -> Dict[str, List[HelpEntry]]:
    """平坦模式：每个插件作为独立「模块」显示，不按分类合并。"""
    grouped: Dict[str, List[HelpEntry]] = OrderedDict()
    for entry in entries:
        # key = display_name，但同名插件需去重
        key = entry.display_name
        if key in grouped:
            key = f"{key} ({entry.plugin_id})"
        grouped[key] = [entry]
    return grouped


def _measure_lines(lines: List[str], line_height: int, minimum: int = 0) -> int:
    return max(minimum, len(lines) * line_height)


def _command_pairs(entry: HelpEntry) -> List[Tuple[str, str]]:
    commands = list(entry.commands or [])
    notes = list(entry.notes or [])
    while len(notes) < len(commands):
        notes.append("")
    if not commands:
        return [(entry.display_name, entry.description or "")]
    return list(zip(commands, notes))


def _measure_trigger_badge(
    draw: ImageDraw.ImageDraw,
    trigger: str,
    fonts: Dict[str, object],
    tile_width: int,
) -> Tuple[List[str], int, int]:
    max_text_width = tile_width - BADGE_LEFT * 2 - BADGE_PADDING_X * 2
    trigger_lines = _truncate_lines(_wrap_text(draw, trigger, fonts["code"], max_text_width), MAX_TRIGGER_LINES)
    badge_text_width = 0
    for line in trigger_lines:
        bbox = draw.textbbox((0, 0), line, font=fonts["code"])
        badge_text_width = max(badge_text_width, bbox[2] - bbox[0])
    badge_width = badge_text_width + BADGE_PADDING_X * 2
    badge_height = _measure_lines(trigger_lines, BADGE_TEXT_LINE_HEIGHT, 30) + BADGE_PADDING_Y * 2
    return trigger_lines, badge_width, badge_height


def _measure_command_tile(
    draw: ImageDraw.ImageDraw,
    trigger: str,
    desc: str,
    fonts: Dict[str, object],
    tile_width: int,
) -> int:
    trigger_lines, _, badge_height = _measure_trigger_badge(draw, trigger, fonts, tile_width)
    desc_lines = _truncate_lines(_wrap_text(draw, desc, fonts["tiny"], tile_width - 36), MAX_DESC_LINES)
    desc_h = _measure_lines(desc_lines, TILE_DESC_LINE_HEIGHT, 0 if not desc else 20)
    trigger_h = _measure_lines(trigger_lines, BADGE_TEXT_LINE_HEIGHT, 30)
    content_height = BADGE_TOP + BADGE_PADDING_Y + trigger_h + BADGE_PADDING_Y + TILE_TRIGGER_DESC_GAP + desc_h + TILE_BOTTOM_PADDING
    return max(MIN_TILE_HEIGHT, content_height)


def _measure_command_grid_height(
    draw: ImageDraw.ImageDraw,
    entry: HelpEntry,
    fonts: Dict[str, object],
    grid_width: int,
) -> int:
    pairs = _command_pairs(entry)
    tile_width = (grid_width - (GRID_COLUMNS - 1) * GRID_GAP_X) // GRID_COLUMNS
    row_heights: List[int] = []
    for idx in range(0, len(pairs), GRID_COLUMNS):
        row = pairs[idx : idx + GRID_COLUMNS]
        row_heights.append(
            max(_measure_command_tile(draw, trigger, desc, fonts, tile_width) for trigger, desc in row)
        )
    if not row_heights:
        return 0
    return sum(row_heights) + GRID_GAP_Y * (len(row_heights) - 1)


def _measure_overview_card(draw: ImageDraw.ImageDraw, entry: HelpEntry, fonts: Dict[str, object]) -> int:
    grid_width = CARD_RIGHT - CARD_LEFT - 74
    grid_h = _measure_command_grid_height(draw, entry, fonts, grid_width)
    desc_lines = _truncate_lines(_wrap_text(draw, entry.description or "", fonts["tiny"], PAGE_WIDTH - 220), 3)
    desc_h = _measure_lines(desc_lines, 22, 0)
    return 56 + grid_h + CARD_DESC_TOP_GAP + desc_h + CARD_BOTTOM_PADDING


def _measure_detail_card(draw: ImageDraw.ImageDraw, entry: HelpEntry, fonts: Dict[str, object]) -> int:
    grid_width = CARD_RIGHT - CARD_LEFT - 74
    grid_h = _measure_command_grid_height(draw, entry, fonts, grid_width)
    desc_lines = _truncate_lines(_wrap_text(draw, entry.description or "", fonts["tiny"], PAGE_WIDTH - 220), 4)
    desc_h = _measure_lines(desc_lines, 22, 0)
    return 56 + grid_h + CARD_DESC_TOP_GAP + desc_h + CARD_BOTTOM_PADDING


def _page_title_text(result: HelpQueryResult, page_index: int, total_pages: int) -> str:
    if total_pages <= 1:
        return result.title
    return f"{result.title}  {page_index}/{total_pages}"


BG_PATH = PLUGIN_DIR / "data" / "background.png"


def _has_background() -> bool:
    return BG_PATH.exists()


def _bg_mode() -> bool:
    return _has_background()


def _card_bg(default_hex: str = "#ffffff", alpha: int = 230) -> str | Tuple[int, int, int, int] | None:
    """有背景图时可返回半透明色；alpha<=0 时不填充，只保留描边。"""
    if _bg_mode():
        if alpha <= 0:
            return None
        r, g, b = _hex_to_rgb(default_hex)
        return (r, g, b, alpha)
    return default_hex


def _draw_roundrect(
    draw: ImageDraw.ImageDraw,
    box: Tuple[int, int, int, int],
    radius: int,
    fill=None,
    outline=None,
    width: int = 1,
) -> None:
    """在 RGBA 背景上正确预合成半透明圆角矩形，避免导出后看起来发白。"""
    image = getattr(draw, "_image", None)
    if image is not None and hasattr(image, "mode") and image.mode == "RGBA":
        needs_composite = (
            isinstance(fill, tuple) and len(fill) == 4 and fill[3] < 255
        ) or (
            isinstance(outline, tuple) and len(outline) == 4 and outline[3] < 255
        )
        if needs_composite:
            overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            overlay_draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
            image.alpha_composite(overlay)
            return
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _get_header_symbol(size: int = 108) -> Image.Image | None:
    cached = _HEADER_SYMBOL_CACHE.get(size)
    if cached is not None:
        return cached.copy()
    if not _HEADER_SYMBOL_BASE64:
        return None
    try:
        raw = base64.b64decode(_HEADER_SYMBOL_BASE64)
        icon = Image.open(io.BytesIO(raw)).convert("RGBA")
        side = min(icon.width, icon.height)
        left = (icon.width - side) // 2
        top = (icon.height - side) // 2
        icon = icon.crop((left, top, left + side, top + side)).resize((size, size), Image.LANCZOS)
        rounded_mask = Image.new("L", (size, size), 0)
        ImageDraw.Draw(rounded_mask).rounded_rectangle((0, 0, size, size), radius=26, fill=255)
        alpha = icon.getchannel("A")
        icon.putalpha(ImageChops.multiply(alpha, rounded_mask))
        _HEADER_SYMBOL_CACHE[size] = icon.copy()
        return icon
    except Exception:
        return None


def _draw_header(image: Image.Image, draw: ImageDraw.ImageDraw, title: str, subtitle: str, fonts: Dict[str, object]) -> None:
    """Draw a mint/teal Firefly-inspired title plate."""
    box = (72, 58, PAGE_WIDTH - 72, 214)
    x1, y1, x2, y2 = box
    radius = 30
    width = x2 - x1
    height = y2 - y1

    grad = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    grad_px = grad.load()
    left = (20, 186, 164)
    mid = (139, 230, 206)
    right = (76, 201, 214)
    for x in range(width):
        t = x / max(1, width - 1)
        if t < 0.55:
            k = t / 0.55
            color = tuple(int(left[i] * (1 - k) + mid[i] * k) for i in range(3))
        else:
            k = (t - 0.55) / 0.45
            color = tuple(int(mid[i] * (1 - k) + right[i] * k) for i in range(3))
        for y in range(height):
            shade = 1.0 - 0.16 * (y / max(1, height - 1))
            grad_px[x, y] = tuple(max(0, min(255, int(c * shade))) for c in color) + (238,)

    mask = Image.new("L", (width, height), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, width, height), radius=radius, fill=255)
    header_layer = Image.new("RGBA", image.size, (0, 0, 0, 0))
    header_layer.paste(grad, (x1, y1), mask)
    image.alpha_composite(header_layer)

    # Soft highlights and Firefly-style UI details.
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((x1 + 28, y1 - 56, x1 + 290, y1 + 156), fill=(226, 255, 247, 58))
    glow_draw.ellipse((x2 - 320, y1 - 72, x2 - 40, y1 + 138), fill=(255, 255, 255, 42))
    glow_draw.line((x1 + 32, y1 + 22, x2 - 38, y1 + 22), fill=(236, 255, 249, 64), width=2)
    glow_draw.line((x1 + 44, y2 - 24, x2 - 240, y2 - 24), fill=(7, 112, 113, 52), width=2)
    glow_draw.rounded_rectangle(box, radius=radius, outline=(229, 255, 247, 210), width=3)
    glow_draw.rounded_rectangle((x1 + 10, y1 + 10, x2 - 10, y2 - 10), radius=24, outline=(20, 134, 132, 76), width=2)
    for cx, cy, r, alpha in (
        (x2 - 98, y1 + 48, 9, 122),
        (x2 - 132, y1 + 92, 5, 112),
        (x2 - 70, y1 + 118, 4, 96),
        (x1 + 34, y2 - 36, 4, 90),
    ):
        glow_draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(232, 255, 246, alpha))
    image.alpha_composite(glow)

    symbol = _get_header_symbol(108)
    if symbol is not None:
        sx, sy = x2 - 132, y1 + 24
        badge = Image.new("RGBA", image.size, (0, 0, 0, 0))
        badge_draw = ImageDraw.Draw(badge)
        badge_draw.rounded_rectangle(
            (sx - 8, sy - 8, sx + 116, sy + 116),
            radius=30,
            fill=(223, 255, 248, 72),
            outline=(234, 255, 249, 178),
            width=2,
        )
        badge.alpha_composite(symbol, (sx, sy))
        image.alpha_composite(badge)

    draw.text((108, 92), title, font=fonts["title"], fill=(245, 255, 251, 255))
    draw.text((110, 164), subtitle, font=fonts["subtitle"], fill=(220, 255, 248, 238))


def _render_shell(height: int, title: str, subtitle: str, fonts: Dict[str, object]) -> Tuple[Image.Image, ImageDraw.ImageDraw]:
    # 自定义背景图：使用 RGBA 模式让背景透出
    if _bg_mode():
        try:
            bg_img = Image.open(BG_PATH).convert("RGB")
            bg_ratio = bg_img.width / bg_img.height
            page_ratio = PAGE_WIDTH / height
            if bg_ratio > page_ratio:
                new_h = height
                new_w = int(height * bg_ratio)
            else:
                new_w = PAGE_WIDTH
                new_h = int(PAGE_WIDTH / bg_ratio)
            bg_img = bg_img.resize((new_w, new_h), Image.LANCZOS)
            left = (new_w - PAGE_WIDTH) // 2
            top = (new_h - height) // 2
            bg_img = bg_img.crop((left, top, left + PAGE_WIDTH, top + height))
            image = Image.new("RGBA", (PAGE_WIDTH, height))
            image.paste(bg_img, (0, 0))
            # 半透明白色蒙层让背景柔和，数值越小背景越明显
            overlay = Image.new("RGBA", (PAGE_WIDTH, height), (255, 255, 255, BG_OVERLAY_ALPHA))
            image = Image.alpha_composite(image, overlay)
        except Exception:
            image = Image.new("RGBA", (PAGE_WIDTH, height), (247, 240, 229, 255))
    else:
        image = Image.new("RGBA", (PAGE_WIDTH, height), (247, 240, 229, 255))

    draw = ImageDraw.Draw(image)
    if not _bg_mode():
        draw.ellipse((-140, -120, 420, 360), fill=(243, 201, 141, 255))
        draw.ellipse((880, -80, 1380, 360), fill=(183, 215, 200, 255))
        draw.ellipse((980, height - 360, 1460, height + 120), fill=(216, 226, 246, 255))

    # 自定义背景模式：去掉整块大白底，只保留边框和流萤青绿标题条
    outer_fill = None if _bg_mode() else (255, 250, 243, 255)
    outer_outline = (255, 255, 255, 220) if _bg_mode() else (36, 48, 66, 255)
    _draw_roundrect(
        draw,
        (38, 34, PAGE_WIDTH - 38, height - 34),
        radius=34,
        fill=outer_fill,
        outline=outer_outline,
        width=3,
    )
    _draw_header(image, draw, title, subtitle, fonts)
    return image, draw


def _render_footer(draw: ImageDraw.ImageDraw, height: int, fonts: Dict[str, object]) -> None:
    draw.text(
        (90, height - 62),
        "提示：发送 /帮助 关键词 可查看某个功能的详细用法。",
        font=fonts["tiny"],
        fill="#556070",
    )


def _split_overview_pages(
    draw: ImageDraw.ImageDraw,
    fonts: Dict[str, object],
    result: HelpQueryResult,
    grouped: Dict[str, List[HelpEntry]] | None = None,
) -> List[List[Tuple[str, List[HelpEntry]]]]:
    if grouped is None:
        grouped = _group_for_overview(result.entries)
    pages: List[List[Tuple[str, List[HelpEntry]]]] = []
    current_page: List[Tuple[str, List[HelpEntry]]] = []
    current_height = CONTENT_TOP
    max_content_bottom = MAX_PAGE_HEIGHT - PAGE_BOTTOM_PADDING - 24

    for category, items in grouped.items():
        section_entries: List[HelpEntry] = []
        for entry in items:
            card_height = _measure_overview_card(draw, entry, fonts)
            header_cost = 64 if not section_entries else 0
            gap_cost = 18
            needed = header_cost + card_height + gap_cost
            if current_height + needed > max_content_bottom and current_page:
                if section_entries:
                    current_page.append((category, section_entries))
                    section_entries = []
                pages.append(current_page)
                current_page = []
                current_height = CONTENT_TOP
                header_cost = 64

            if not section_entries:
                current_height += header_cost
            section_entries.append(entry)
            current_height += card_height + gap_cost

        if section_entries:
            current_page.append((category, section_entries))
            current_height += 12

    if current_page:
        pages.append(current_page)
    return pages or [[]]


def _split_detail_pages(draw: ImageDraw.ImageDraw, fonts: Dict[str, object], result: HelpQueryResult) -> List[List[HelpEntry]]:
    pages: List[List[HelpEntry]] = []
    current_page: List[HelpEntry] = []
    current_height = CONTENT_TOP
    max_content_bottom = MAX_PAGE_HEIGHT - PAGE_BOTTOM_PADDING - 24

    for entry in result.entries:
        card_height = _measure_detail_card(draw, entry, fonts)
        needed = card_height + 26
        if current_height + needed > max_content_bottom and current_page:
            pages.append(current_page)
            current_page = []
            current_height = CONTENT_TOP
        current_page.append(entry)
        current_height += needed

    if current_page:
        pages.append(current_page)
    return pages or [[]]


def _draw_command_grid(
    draw: ImageDraw.ImageDraw,
    left: int,
    top: int,
    width: int,
    entry: HelpEntry,
    fonts: Dict[str, object],
    accent_fg: str,
    accent_bg: str,
) -> int:
    pairs = _command_pairs(entry)
    tile_width = (width - (GRID_COLUMNS - 1) * GRID_GAP_X) // GRID_COLUMNS
    y = top
    for idx in range(0, len(pairs), GRID_COLUMNS):
        row = pairs[idx : idx + GRID_COLUMNS]
        row_height = max(_measure_command_tile(draw, trigger, desc, fonts, tile_width) for trigger, desc in row)
        for col, (trigger, desc) in enumerate(row):
            x = left + col * (tile_width + GRID_GAP_X)
            tile_bottom = y + row_height
            _draw_roundrect(
                draw,
                (x, y, x + tile_width, tile_bottom),
                radius=GRID_TILE_RADIUS,
                fill=_card_bg("#fffdf8", TILE_CARD_ALPHA if _bg_mode() else 0),
                outline=(255, 255, 255, 235) if _bg_mode() else "#d9cfc2",
                width=2 if _bg_mode() else 2,
            )

            trigger_lines, badge_width, badge_height = _measure_trigger_badge(draw, trigger, fonts, tile_width)
            badge_left = x + BADGE_LEFT
            badge_top = y + BADGE_TOP
            badge_right = min(x + tile_width - BADGE_LEFT, badge_left + badge_width)
            badge_bottom = badge_top + badge_height
            draw.rounded_rectangle(
                (badge_left, badge_top, badge_right, badge_bottom),
                radius=BADGE_RADIUS,
                fill=accent_bg,
            )

            text_y = badge_top + BADGE_PADDING_Y - BADGE_TEXT_TOP_ADJUST
            for line in trigger_lines:
                draw.text((badge_left + BADGE_PADDING_X, text_y), line, font=fonts["code"], fill=accent_fg)
                text_y += BADGE_TEXT_LINE_HEIGHT

            desc_lines = _truncate_lines(_wrap_text(draw, desc, fonts["tiny"], tile_width - 36), MAX_DESC_LINES)
            if desc_lines:
                desc_y = badge_bottom + TILE_TRIGGER_DESC_GAP
                desc_bottom_limit = tile_bottom - TILE_BOTTOM_PADDING + 6
                visible_lines = 0
                probe_y = desc_y
                for _line in desc_lines:
                    if probe_y + TILE_DESC_LINE_HEIGHT > desc_bottom_limit:
                        break
                    visible_lines += 1
                    probe_y += TILE_DESC_LINE_HEIGHT
                if _bg_mode() and visible_lines > 0:
                    strip_top = desc_y - 4
                    strip_bottom = desc_y + visible_lines * TILE_DESC_LINE_HEIGHT + 6
                    _draw_roundrect(
                        draw,
                        (x + 10, strip_top, x + tile_width - 10, strip_bottom),
                        radius=10,
                        fill=_card_bg("#fffaf3", TILE_DESC_STRIP_ALPHA),
                    )
                for line in desc_lines:
                    if desc_y + TILE_DESC_LINE_HEIGHT > desc_bottom_limit:
                        break
                    draw.text(
                        (x + TILE_TEXT_LEFT, desc_y),
                        line,
                        font=fonts["tiny"],
                        fill="#000000" if _bg_mode() else "#5f5345",
                    )
                    desc_y += TILE_DESC_LINE_HEIGHT
        y += row_height + GRID_GAP_Y
    return max(0, y - top - GRID_GAP_Y)


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    """将 hex 颜色字符串转为 RGB 三元组。"""
    hex_color = hex_color.lstrip("#")
    if len(hex_color) == 3:
        hex_color = "".join(c * 2 for c in hex_color)
    return tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _lighten_hex(hex_color: str, factor: float = 0.75) -> str:
    """返回 hex 颜色的浅色版本（用于背景色）。"""
    r, g, b = _hex_to_rgb(hex_color)
    r = min(255, int(r + (255 - r) * factor))
    g = min(255, int(g + (255 - g) * factor))
    b = min(255, int(b + (255 - b) * factor))
    return f"#{r:02x}{g:02x}{b:02x}"


def _build_module_colors(categories: List[str], plugin_color_map: Dict[str, str] | None = None) -> Dict[str, Tuple[str, str]]:
    """为每个分类构建 (accent_bg, accent_fg) 颜色对。

    plugin_color_map: {category_key: hex_color} — 平坦模式下每插件的独立颜色覆盖。
    """
    result: Dict[str, Tuple[str, str]] = {}
    for idx, cat in enumerate(categories):
        # 优先使用插件级颜色（平坦模式），其次模块颜色，最后调色板兜底
        if plugin_color_map and cat in plugin_color_map and plugin_color_map[cat]:
            hex_color = plugin_color_map[cat]
        else:
            cfg = get_module_config(cat)
            hex_color = cfg.get("color", DEFAULT_COLORS[idx % len(DEFAULT_COLORS)]) if cfg else DEFAULT_COLORS[idx % len(DEFAULT_COLORS)]
        result[cat] = (_lighten_hex(hex_color), hex_color)
    return result


def _render_overview_page(
    draw: ImageDraw.ImageDraw,
    page_sections: Sequence[Tuple[str, List[HelpEntry]]],
    fonts: Dict[str, object],
    flat_mode: bool = False,
    plugin_color_map: Dict[str, str] | None = None,
) -> None:
    # 收集所有分类名
    categories = [cat for cat, _ in page_sections]
    module_colors = _build_module_colors(categories, plugin_color_map=plugin_color_map)
    y = CONTENT_TOP
    for category, items in page_sections:
        accent_bg, accent_fg = module_colors.get(
            category,
            ("#d9ecff", "#1e5bb8"),  # 兜底蓝色
        )
        # 使用用户自定义的模块显示名（如果有）
        cfg = get_module_config(category)
        category_label = cfg.get("display_name", category) if cfg else category
        draw.rounded_rectangle((CARD_LEFT, y, CARD_LEFT + 250, y + 44), radius=18, fill=accent_bg)
        draw.text((CARD_LEFT + 24, y + 4), category_label, font=fonts["section"], fill=accent_fg)
        y += 64

        for entry in items:
            card_height = _measure_overview_card(draw, entry, fonts)
            if flat_mode:
                # 平坦模式：标题在彩色气泡中，卡片内不重复插件名，更紧凑
                card_height = card_height - 56 + 18
            _draw_roundrect(
                draw,
                (CARD_LEFT, y, CARD_RIGHT, y + card_height),
                radius=22,
                fill=_card_bg("#ffffff", 0),
                outline=(255, 255, 255, 220) if _bg_mode() else "#d8d0c4",
                width=1,
            )
            if flat_mode:
                grid_left = CARD_LEFT + 30
                grid_top = y + 18
            else:
                draw.rounded_rectangle(
                    (CARD_LEFT + 20, y + 14, CARD_LEFT + 28, y + card_height - 14),
                    radius=4,
                    fill=accent_fg,
                )
                draw.text((CARD_LEFT + 46, y + 16), entry.display_name, font=fonts["name"], fill="#1c2430")
                grid_left = CARD_LEFT + 46
                grid_top = y + 56

            grid_width = CARD_RIGHT - CARD_LEFT - 74
            grid_height = _draw_command_grid(draw, grid_left, grid_top, grid_width, entry, fonts, accent_fg, accent_bg)

            desc_left = grid_left if flat_mode else CARD_LEFT + 46
            desc_y = grid_top + grid_height + CARD_DESC_TOP_GAP
            desc_lines = _truncate_lines(_wrap_text(draw, entry.description or "", fonts["tiny"], CARD_RIGHT - CARD_LEFT - 92), 3)
            if _bg_mode() and desc_lines:
                strip_height = len(desc_lines) * 22 + 14
                strip_right = CARD_RIGHT - 28
                _draw_roundrect(
                    draw,
                    (desc_left - 8, desc_y - 6, strip_right, desc_y - 6 + strip_height),
                    radius=10,
                    fill=_card_bg("#fffaf3", DESC_STRIP_ALPHA),
                )
            for line in desc_lines:
                draw.text((desc_left, desc_y), line, font=fonts["tiny"], fill="#000000" if _bg_mode() else "#8a7e6e")
                desc_y += 22

            y += card_height + 14
        y += 12


def _render_detail_page(draw: ImageDraw.ImageDraw, entries: Sequence[HelpEntry], fonts: Dict[str, object]) -> None:
    y = CONTENT_TOP
    for entry in entries:
        # 使用该条目所属分类的颜色
        cfg = get_module_config(entry.category)
        hex_color = cfg.get("color", "#1e5bb8") if cfg else "#1e5bb8"
        accent_fg = hex_color
        accent_bg = _lighten_hex(hex_color)

        card_height = _measure_detail_card(draw, entry, fonts)
        _draw_roundrect(
            draw,
            (CARD_LEFT, y, CARD_RIGHT, y + card_height),
            radius=22,
            fill=_card_bg("#ffffff", 0),
            outline=(255, 255, 255, 220) if _bg_mode() else "#d8d0c4",
            width=1,
        )
        draw.rounded_rectangle(
            (CARD_LEFT + 20, y + 14, CARD_LEFT + 28, y + card_height - 14),
            radius=4,
            fill=accent_fg,
        )
        # 使用用户自定义的模块显示名
        cfg = get_module_config(entry.category)
        category_label = cfg.get("display_name", entry.category) if cfg else entry.category
        draw.text((CARD_LEFT + 46, y + 16), f"{entry.display_name}  ·  {category_label}", font=fonts["name"], fill="#1c2430")

        grid_left = CARD_LEFT + 46
        grid_top = y + 56
        grid_width = CARD_RIGHT - CARD_LEFT - 74
        grid_height = _draw_command_grid(draw, grid_left, grid_top, grid_width, entry, fonts, accent_fg, accent_bg)

        desc_y = grid_top + grid_height + CARD_DESC_TOP_GAP
        desc_lines = _truncate_lines(_wrap_text(draw, entry.description or "", fonts["tiny"], CARD_RIGHT - CARD_LEFT - 92), 4)
        if _bg_mode() and desc_lines:
            strip_height = len(desc_lines) * 22 + 14
            desc_left = CARD_LEFT + 46
            strip_right = CARD_RIGHT - 28
            _draw_roundrect(
                draw,
                (desc_left - 8, desc_y - 6, strip_right, desc_y - 6 + strip_height),
                radius=10,
                fill=_card_bg("#fffaf3", DESC_STRIP_ALPHA),
            )
        for line in desc_lines:
            draw.text((CARD_LEFT + 46, desc_y), line, font=fonts["tiny"], fill="#000000" if _bg_mode() else "#8a7e6e")
            desc_y += 22

        y += card_height + 14


def _encode_image(image: Image.Image) -> bytes:
    # 先压平到 RGB，避免 QQ/OneBot 对 PNG alpha 的兼容问题影响最终显示
    if image.mode in ("RGBA", "LA") or ("transparency" in image.info):
        base = Image.new("RGB", image.size, (255, 255, 255))
        alpha = image.getchannel("A") if "A" in image.getbands() else None
        base.paste(image.convert("RGB"), (0, 0), alpha)
        image = base
    elif image.mode != "RGB":
        image = image.convert("RGB")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _compute_detail_page_height(
    measure_draw: ImageDraw.ImageDraw, fonts: Dict[str, object], entries: Sequence[HelpEntry]
) -> int:
    content_height = CONTENT_TOP
    for entry in entries:
        content_height += _measure_detail_card(measure_draw, entry, fonts) + 26
    return max(content_height + PAGE_BOTTOM_PADDING, 520)


def _compute_overview_page_height(
    measure_draw: ImageDraw.ImageDraw,
    fonts: Dict[str, object],
    page_sections: Sequence[Tuple[str, List[HelpEntry]]],
) -> int:
    content_height = CONTENT_TOP
    for _, items in page_sections:
        content_height += 64
        for entry in items:
            content_height += _measure_overview_card(measure_draw, entry, fonts) + 18
        content_height += 12
    return max(content_height + PAGE_BOTTOM_PADDING, 520)


def _normalize_page_height(height: int, step: int = 120) -> int:
    """Keep page heights visually closer without forcing every page to match the tallest one."""
    return ((height + step - 1) // step) * step


# ── 渲染缓存：避免每次 /帮助 重新渲染图片 ──
_render_cache: dict = {}
_render_cache_valid = True


def invalidate_render_cache():
    """使渲染缓存失效（WebUI 保存配置时调用）"""
    global _render_cache, _render_cache_valid
    _render_cache.clear()
    _render_cache_valid = True


def _make_cache_key(result: HelpQueryResult) -> str:
    """生成渲染缓存键（基于结果内容哈希）"""
    ids = ",".join(sorted(e.plugin_id for e in result.entries))
    return f"{result.keyword}|{len(result.entries)}|{ids}"


def render_help_images(result: HelpQueryResult) -> List[bytes]:
    global _render_cache, _render_cache_valid
    cache_key = _make_cache_key(result)
    if _render_cache_valid and cache_key in _render_cache:
        return _render_cache[cache_key]

    measure_img = Image.new("RGB", (PAGE_WIDTH, 200), "#f7f0e5")
    measure_draw = ImageDraw.Draw(measure_img)
    fonts = _build_fonts()

    # 检查显示模式（自动分类 vs 平坦单插件）
    from .webui_store import get_display_mode
    display_mode = get_display_mode()

    images: List[bytes] = []
    if result.keyword:
        pages = _split_detail_pages(measure_draw, fonts, result)
        total_pages = len(pages)
        for index, entries in enumerate(pages, start=1):
            height = _normalize_page_height(_compute_detail_page_height(measure_draw, fonts, entries))
            title = _page_title_text(result, index, total_pages)
            image, draw = _render_shell(height, title, result.subtitle, fonts)
            _render_detail_page(draw, entries, fonts)
            _render_footer(draw, height, fonts)
            images.append(_encode_image(image))
    else:
        # 根据显示模式选择分组方式
        if display_mode == "flat":
            # 平坦模式：按插件 sort_order 重排（忽略模块分组）
            from .webui_store import get_plugin_override as _get_plg_ov
            flat_entries = sorted(
                result.entries,
                key=lambda e: (
                    (_get_plg_ov(e.plugin_id) or {}).get("sort_order", 0),
                    e.display_name.lower(),
                ),
            )
            grouped = _group_flat(flat_entries)
        else:
            grouped = _group_for_overview(result.entries)

        pages = _split_overview_pages(measure_draw, fonts, result, grouped=grouped)
        total_pages = len(pages)
        for index, page_sections in enumerate(pages, start=1):
            height = _normalize_page_height(_compute_overview_page_height(measure_draw, fonts, page_sections))
            title = _page_title_text(result, index, total_pages)
            image, draw = _render_shell(height, title, result.subtitle, fonts)
            # 平坦模式：构建插件级颜色映射
            plugin_colors = None
            if display_mode == "flat":
                from .webui_store import get_plugin_override as _get_plg_ov
                plugin_colors = {}
                for cat, items in grouped.items():
                    for entry in items:
                        ov = _get_plg_ov(entry.plugin_id)
                        if ov and ov.get("color"):
                            plugin_colors[cat] = ov["color"]

            _render_overview_page(draw, page_sections, fonts, flat_mode=(display_mode == "flat"), plugin_color_map=plugin_colors)
            _render_footer(draw, height, fonts)
            images.append(_encode_image(image))
    _render_cache[cache_key] = images
    return images


def render_help_image(result: HelpQueryResult) -> bytes:
    return render_help_images(result)[0]


def render_help_base64(result: HelpQueryResult) -> str:
    return base64.b64encode(render_help_image(result)).decode("utf-8")
