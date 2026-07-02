import ast
import re
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from .config import CONFIG, PLUGINS_ROOTS, resolve_category
from .models import HelpEntry


# ═══════════════════════════════════════════════════════════════
#  Regex patterns for extracting trigger words from matchers
# ═══════════════════════════════════════════════════════════════

# Standard single-string matchers:
#   var = on_xxx("trigger", ...)  or  var = on_xxx('trigger', ...)
# Covers: on_command, on_fullmatch, on_startswith, on_endswith, on_shell_command
_STD_MATCHER_RE = re.compile(
    r'^\s*\w+\s*=\s*on_(command|fullmatch|startswith|endswith|shell_command)\s*\(\s*'
    r'(?:r)?(["\'])(.+?)\2'
)

# on_keyword start detection: var = on_keyword({...  or  on_keyword([...  or  on_keyword((...
_KEYWORD_START_RE = re.compile(
    r'^\s*\w+\s*=\s*on_keyword\s*\(\s*'
)

# on_regex: var = on_regex(r"...", ...)
_REGEX_RE = re.compile(
    r'^\s*\w+\s*=\s*on_regex\s*\(\s*(?:r)?(["\'])(.+?)\1'
)

# Extract all string literals (non-greedy inside quotes)
_STR_EXTRACT_RE = re.compile(r'(?:r)?(["\'])(.+?)\1')

# Aliases block in on_command: aliases={"a1", "a2", ...}
_ALIASES_RE = re.compile(r'aliases\s*=\s*\{([^}]*)\}')

# Inline command tuple pattern: ("/xxx", "action") — used by on_message plugins
_INLINE_CMD_RE = re.compile(
    r'^\s*\(\s*(["\'])(/.+?)\1\s*,\s*(["\'])(.+?)\3\s*\)'
)

# on_alconna / Alconna patterns:  var = on_alconna(Alconna("trigger", ...))
_ALCONNA_VAR_RE = re.compile(r'^\s*(\w+)\s*=\s*on_alconna\s*\(')
_ALCONNA_CALL_RE = re.compile(r'Alconna\s*\(')
# Line that starts with a quoted string (after stripping whitespace):
#     "词云",
_ALCONNA_TRIGGER_LINE_RE = re.compile(r'^\s*(["\'])(.+?)\1')

# Legacy: old on_command regex (fallback)
COMMAND_RE = re.compile(r"on_command\(\s*['\"]([^'\"]+)['\"]")
SLASH_COMMAND_RE = re.compile(r"/([^\s/]+)")
EXAMPLE_HINTS = ("示例", "例如", "比如")
NOTE_HINTS = (
    "说明",
    "用法",
    "文生图",
    "改图",
    "需要",
    "支持",
    "提示",
    "可以",
    "默认",
)
USAGE_LABEL_PREFIXES = ("- ", "•", "* ")


def _safe_read_text(path: Path) -> str:
    for encoding in ("utf-8", "utf-8-sig", "gb18030", "latin1"):
        try:
            return path.read_text(encoding=encoding)
        except Exception:
            continue
    return ""


def _clean_text(value: str) -> str:
    return value.replace("\t", " ").replace("\r\n", "\n").strip()


def _eval_string_node(node: ast.AST) -> str:
    try:
        value = ast.literal_eval(node)
    except Exception:
        return ""
    return value if isinstance(value, str) else ""


def _extract_meta_from_ast(text: str) -> Tuple[str, str, List[str]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return "", "", []

    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(target, ast.Name) and target.id == "__plugin_meta__" for target in node.targets):
            continue
        call = node.value
        if not isinstance(call, ast.Call):
            continue
        func = call.func
        if not isinstance(func, ast.Name) or func.id != "PluginMetadata":
            continue

        name = ""
        description = ""
        usage = ""
        for keyword in call.keywords:
            if keyword.arg == "name":
                name = _eval_string_node(keyword.value)
            elif keyword.arg == "description":
                description = _eval_string_node(keyword.value)
            elif keyword.arg == "usage":
                usage = _eval_string_node(keyword.value)

        usage_lines = [line.strip() for line in _clean_text(usage).splitlines() if line.strip()]
        return _clean_text(name), _clean_text(description), usage_lines

    return "", "", []


def _extract_commands(text: str) -> List[str]:
    commands: List[str] = []
    for cmd in COMMAND_RE.findall(text):
        cmd = cmd.strip()
        if cmd and cmd not in commands:
            commands.append(cmd)
    return commands


def _extract_commands_from_usage(usage_lines: List[str]) -> List[str]:
    commands: List[str] = []
    for line in usage_lines:
        for cmd in SLASH_COMMAND_RE.findall(line):
            command = cmd.strip()
            if command and command not in commands:
                commands.append(command)
    return commands


# ═══════════════════════════════════════════════════════════════
#  New: matcher-first trigger extraction + metadata cross-reference
# ═══════════════════════════════════════════════════════════════

def _simplify_regex_trigger(pattern: str) -> Optional[str]:
    """Try to extract a human-readable trigger word from a regex pattern.

    Returns None if the pattern is too complex for a simple trigger word.
    """
    cleaned = pattern.strip()
    # Strip common anchors
    if cleaned.startswith('^'):
        cleaned = cleaned[1:]
    if cleaned.endswith('$'):
        cleaned = cleaned[:-1]
    # Simple word/Chinese pattern — usable as trigger
    if re.match(r'^[\w一-鿿]+$', cleaned) and len(cleaned) <= 20:
        return cleaned
    # Contains regex metacharacters — too complex
    if any(c in cleaned for c in '()[]{}.*+?|\\'):
        return None
    return cleaned if 0 < len(cleaned) <= 20 else None


def _extract_all_matcher_triggers(text: str) -> List[Tuple[str, str]]:
    """Extract all trigger words from matcher registrations in plugin source code.

    Handles:
      - on_command / on_fullmatch / on_startswith / on_endswith / on_shell_command
      - on_keyword (set / list / tuple of trigger words)
      - on_regex (simplified to a readable trigger when possible)
      - on_command aliases
      - Inline command tuples: ("/xxx", "action")
      - on_alconna: Alconna("trigger", Option(...), Args[...], ...)

    Returns a list of (trigger_word, matcher_type), deduplicated in discovery order.
    """
    triggers: List[Tuple[str, str]] = []
    lines = text.splitlines()

    for lineno, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith('#'):
            continue

        # ── 1) Standard matchers ──
        #    var = on_xxx("trigger", ...)  /  var = on_xxx('trigger', ...)
        m = _STD_MATCHER_RE.match(stripped)
        if m:
            matcher_kind, _, trigger = m.groups()
            matcher_type = f'on_{matcher_kind}'
            triggers.append((trigger, matcher_type))

            # Extract aliases from on_command(..., aliases={"a1","a2"}, ...)
            alias_m = _ALIASES_RE.search(line)
            if alias_m:
                for am in _STR_EXTRACT_RE.finditer(alias_m.group(1)):
                    alias = am.group(2)
                    if alias and alias != trigger:
                        triggers.append((alias, f'on_{matcher_kind}_alias'))
            continue

        # ── 2) on_keyword ──
        #    var = on_keyword({"w1","w2"}, ...) / on_keyword(["w1"], ...)
        if _KEYWORD_START_RE.match(stripped):
            idx = line.find('on_keyword(')
            if idx != -1:
                # Extract all quoted strings from the on_keyword( call onward
                segment = line[idx:]
                for sm in _STR_EXTRACT_RE.finditer(segment):
                    kw = sm.group(2)
                    if kw:
                        triggers.append((kw, 'on_keyword'))
            continue

        # ── 3) on_regex ──
        #    var = on_regex(r"...", ...)
        m = _REGEX_RE.match(stripped)
        if m:
            _, pattern = m.groups()
            simplified = _simplify_regex_trigger(pattern)
            if simplified:
                triggers.append((simplified, 'on_regex'))
            continue

        # ── 4) Inline command tuples ──
        #    ("/xxx", "action") — used by on_message plugins (e.g. gif.py)
        m = _INLINE_CMD_RE.match(stripped)
        if m:
            trigger = m.group(2)
            if trigger:
                triggers.append((trigger, 'inline'))
            continue

        # ── 5) on_alconna ──
        #    var = on_alconna(Alconna("trigger", Option(...), Args[...], ...))
        #    Cross-line: Alconna(...) may start on the next line.
        m = _ALCONNA_VAR_RE.match(stripped)
        if m:
            var_name = m.group(1)
            found_alconna = False
            for offset in range(20):
                idx = lineno + offset
                if idx >= len(lines):
                    break
                sl = lines[idx].strip()
                if sl.startswith('#'):
                    continue
                if not found_alconna:
                    if _ALCONNA_CALL_RE.search(sl):
                        found_alconna = True
                        # Check same line for inline trigger: Alconna("trigger", ...)
                        alconna_idx = sl.find('Alconna(')
                        after_alconna = sl[alconna_idx + 8:]
                        qm = _STR_EXTRACT_RE.search(after_alconna)
                        if qm:
                            triggers.append((qm.group(2), 'on_alconna'))
                            break
                    continue
                # After Alconna( found on a previous line, look for first quoted string
                qm = _ALCONNA_TRIGGER_LINE_RE.match(sl)
                if qm:
                    triggers.append((qm.group(2), 'on_alconna'))
                    break
            continue

    # Deduplicate while preserving discovery order
    seen: set = set()
    result: List[Tuple[str, str]] = []
    for trigger, mtype in triggers:
        if trigger not in seen:
            seen.add(trigger)
            result.append((trigger, mtype))
    return result


# ── Parameter detection ──
# Short noun-like words that appear right after a trigger in usage text
# are likely parameter placeholders, not descriptions.
# e.g. "/发言排行 天数 获取本群N天发言排行" → param="天数", desc="获取本群N天发言排行"
_PARAM_WORD_RE = re.compile(r'^[\w一-鿿]{1,10}$')


def _is_param_word(word: str) -> bool:
    """Check if a word looks like a parameter placeholder (not description text)."""
    if not word or len(word) > 10:
        return False
    if word.isdigit():
        return False
    return bool(_PARAM_WORD_RE.match(word))


def _split_param_from_desc(raw_desc: str) -> Tuple[str, str]:
    """Split a raw description into (param_hint, clean_description).

    If the first word looks like a parameter placeholder,
    it is separated from the actual description.
    e.g. "天数 获取本群N天发言排行" → ("天数", "获取本群N天发言排行")
         "获取本群今日发言排行"       → ("", "获取本群今日发言排行")
    """
    if ' ' not in raw_desc and '\t' not in raw_desc:
        return '', raw_desc

    parts = raw_desc.split(maxsplit=1)
    if len(parts) == 2 and _is_param_word(parts[0]):
        return parts[0], parts[1]
    return '', raw_desc


def _match_triggers_to_meta(
    triggers: List[str],
    meta_name: str,
    meta_description: str,
    usage_lines: List[str],
) -> List[Tuple[str, str]]:
    """Cross-reference discovered trigger words against PluginMetadata content.

    For each trigger word:
      1. Search in usage_lines for a line that STARTS with the trigger
         (optionally prefixed by "/" for on_command style).
      2. If found, detect parameter placeholders (e.g. "天数" in
         "/发言排行 天数 获取...") and separate them from the description.
      3. The trigger word may be suffixed with " <参数>" when a parameter is detected.
      4. If no start-of-line match, fall back to substring → full metadata blob.
      5. If still not found, return the trigger with an empty description
         (standalone bubble without description).

    Returns a list of (trigger_word, description) pairs.
    """
    pairs: List[Tuple[str, str]] = []
    meta_blob_lines: List[str] = [meta_name, meta_description] + list(usage_lines)

    for trigger in triggers:
        desc = ''

        # 在 usage_lines 中搜索触发词，取其后内容作为描述
        for line in usage_lines:
            idx = line.find(trigger)
            if idx < 0:
                # also try without / prefix
                plain = trigger.lstrip("/")
                idx = line.find(plain) if plain != trigger else -1
            if idx >= 0:
                rest = line[idx + len(trigger):].strip()
                # 去掉紧跟的引号/括号/标点
                rest = rest.lstrip('」』）)\】""''，,、。.')
                if rest:
                    desc = rest
                break

        # Separate parameter hint from description
        param, clean_desc = _split_param_from_desc(desc)
        if param:
            trigger = f'{trigger} <{param}>'

        pairs.append((trigger, clean_desc))

    return pairs


def _extract_usage_labels(usage_lines: List[str]) -> List[str]:
    labels: List[str] = []
    for line in usage_lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("/"):
            continue
        for prefix in USAGE_LABEL_PREFIXES:
            if stripped.startswith(prefix):
                label = stripped[len(prefix) :].strip()
                if label and label not in labels:
                    labels.append(label)
                break
    return labels


def _split_usage_sections(usage_lines: List[str]) -> Tuple[List[str], List[str], List[str]]:
    """解析 usage 行。

    新简洁格式：每行 "<触发词> <简短描述>"
      - 以 / 开头 → on_command 类指令
      - 不以 / 开头 → on_message 类触发词
    旧格式（兜底）：按关键词分类到 commands/notes/examples。
    """
    triggers: List[str] = []
    descriptions: List[str] = []

    for raw_line in usage_lines:
        line = raw_line.strip()
        if not line:
            continue

        # 尝试新格式：第一个空格前为触发词，后为描述
        if " " in line:
            parts = line.split(maxsplit=1)
            first_word = parts[0].strip()
            desc = parts[1].strip()
            # 触发词以 / 开头，或为合理长度的中文词（排除常见描述性前缀和虚词）
            if first_word.startswith("/") or (
                len(first_word) <= 12
                and not any(
                    kw in first_word for kw in (
                        "说明", "用法", "示例", "比如", "例如", "支持", "可以", "需要", "提示",
                        "或", "和", "也", "与", "及", "的", "了", "吗", "吧", "呢", "啊",
                        "回复", "发送", "输入", "使用", "然后", "首先", "请", "直接", "可",
                    )
                )
                and not first_word.endswith(("的", "了", "吧", "吗", "呢"))
            ):
                triggers.append(first_word)  # 保留 / 前缀
                descriptions.append(desc)
                continue

        # 兜底：旧格式行以 / 开头
        if line.startswith("/"):
            parts = line.split(maxsplit=1)
            triggers.append(parts[0].strip())
            descriptions.append(parts[1].strip() if len(parts) > 1 else "")
            continue

        # 完全无法识别，跳过
        continue

    return triggers, descriptions, []


def _iter_plugin_sources() -> Iterable[Tuple[str, Path]]:
    """Yield (plugin_id, source_path) for local plugins under ALL PLUGINS_ROOTS."""
    seen: set = set()
    for root in PLUGINS_ROOTS:
        for path in sorted(root.iterdir(), key=lambda item: item.name.lower()):
            if path.name in CONFIG.exclude_names:
                continue
            if path.is_file() and path.suffix == ".py":
                if path.stem not in seen:
                    seen.add(path.stem)
                    yield path.stem, path
                continue
            if path.is_dir():
                init_file = path / "__init__.py"
                if init_file.exists() and path.name not in seen:
                    seen.add(path.name)
                    yield path.name, init_file


# Infrastructure / internal plugins that should never appear in help
_SKIP_EXTERNAL_PLUGINS: set = {
    'nonebot_plugin_apscheduler',
    'nonebot_plugin_gocqhttp',
    'nonebot_plugin_htmlrender',
    'nonebot_plugin_imageutils',
    'nonebot_plugin_localstore',
    'nonebot_plugin_tortoise_orm',
    'nonebot_plugin_alconna',
    'nonebot_plugin_uninfo',
    'nonebot_plugin_saa',
    'nonebot_plugin_waiter',
    'nonebot_plugin_orm',
    'nonebot_plugin_cesaa',
    'nonebot_plugin_chatrecorder',
    'nonebot_plugin_dialectlist',
    'single_session',
    'uniseg',
}


def _iter_external_plugin_sources() -> Iterable[Tuple[str, Path]]:
    """Yield (plugin_id, source_path) for plugins loaded from site-packages
    (pip-installed) that are NOT already local plugins under PLUGINS_ROOTS.

    Requires NoneBot2 to be initialized (uses get_loaded_plugins()).
    Gracefully degrades to an empty iterator when NoneBot is not ready.
    """
    # Collect local plugin IDs for deduplication
    local_ids: set = {pid for pid, _ in _iter_plugin_sources()}

    try:
        from nonebot.plugin import get_loaded_plugins
        plugins = get_loaded_plugins()
    except Exception:
        return

    for plugin in plugins:
        name = plugin.name
        short_name = name.rsplit(".", 1)[-1] if "." in name else name

        # Skip duplicates and infrastructure
        if name in local_ids or short_name in local_ids:
            continue
        if name in _SKIP_EXTERNAL_PLUGINS or short_name in _SKIP_EXTERNAL_PLUGINS:
            continue
        # 仅允许 nonebot_plugin_ 或 haruka_bot_ 前缀的外部插件
        if not (short_name.startswith("nonebot_plugin_") or short_name.startswith("haruka_bot_")):
            continue

        # Get source file path from the loaded module
        mod = plugin.module
        if mod is None:
            continue
        mod_file = getattr(mod, '__file__', None)
        if not mod_file:
            continue

        path = Path(mod_file)
        if not path.exists():
            continue

        # Skip if the file lives inside any PLUGINS_ROOTS (already covered)
        _already_covered = False
        for _root in PLUGINS_ROOTS:
            try:
                path.relative_to(_root)
                _already_covered = True
                break
            except ValueError:
                pass
        if _already_covered:
            continue

        yield (name, path)


def _resolve_category(plugin_id: str) -> str:
    return resolve_category(plugin_id)


def _apply_overrides(plugin_id: str, meta_name: str, meta_desc: str, category: str) -> Tuple[str, str, str]:
    """应用 WebUI 用户覆盖（插件级 display_name / description）。

    返回 (最终 display_name, 最终 description, 原始 category)。
    category 保持为内部分类键名，不做替换——显示名由 renderer 查找。
    """
    # 延迟导入避免循环
    from .webui_store import get_plugin_override

    # 插件级：display_name / description 覆盖
    override = get_plugin_override(plugin_id)
    final_name = meta_name
    final_desc = meta_desc
    if override:
        if override.get("display_name"):
            final_name = override["display_name"]
        if override.get("description"):
            final_desc = override["description"]

    return final_name, final_desc, category


def _entry_from_path(plugin_id: str, path: Path) -> Optional[HelpEntry]:
    text = _safe_read_text(path)
    if not text:
        return None

    # ── Phase 1: Extract PluginMetadata ──
    display_name, description, usage_lines = _extract_meta_from_ast(text)

    # ── Phase 2: Discover trigger words from matchers FIRST ──
    # This is the primary source — scan all matcher registrations in source code.
    matcher_triggers = _extract_all_matcher_triggers(text)

    if matcher_triggers:
        # ── Phase 3a: Cross-reference matcher triggers with PluginMetadata ──
        trigger_words = [t for t, _ in matcher_triggers]
        pairs = _match_triggers_to_meta(
            trigger_words, display_name, description, usage_lines,
        )
        triggers = [p[0] for p in pairs]
        trigger_descs = [p[1] for p in pairs]
    else:
        # Phase 3b: 无标准 matcher，只显示插件名和描述（不猜测触发词）
        triggers, trigger_descs = [], []
        if not display_name:
            display_name = plugin_id

    # ── Phase 4: Fill display name / description from metadata ──
    if not display_name:
        display_name = plugin_id

    if not description:
        if trigger_descs:
            description = trigger_descs[0]
        elif usage_lines:
            description = usage_lines[0]

    if not triggers and not usage_lines and not description:
        return None

    # ── Phase 5: Apply category & WebUI overrides ──
    category = _resolve_category(plugin_id)
    display_name, description, category = _apply_overrides(
        plugin_id, display_name, description or "暂无说明", category
    )

    # Apply trigger description overrides from WebUI
    final_notes = list(trigger_descs[:12])
    from .webui_store import get_plugin_override as _get_override
    _ov = _get_override(plugin_id)
    if _ov and _ov.get("triggers"):
        trigger_map = _ov["triggers"]
        for i, cmd in enumerate(triggers[:12]):
            if cmd in trigger_map and trigger_map[cmd]:
                while len(final_notes) <= i:
                    final_notes.append("")
                final_notes[i] = trigger_map[cmd]

    return HelpEntry(
        plugin_id=plugin_id,
        display_name=display_name,
        description=description or "暂无说明",
        usage_lines=usage_lines[:16],
        commands=triggers[:12],
        notes=final_notes,
        examples=[],
        category=category,
        source_path=str(path),
    )


# ── 缓存：避免每次 /帮助 都重新扫描全部文件 ──
_cache: Dict[str, List[HelpEntry]] = {}
_cache_valid = False


def invalidate_help_cache():
    """使帮助数据缓存失效（WebUI 保存配置或插件变动时调用）"""
    global _cache, _cache_valid
    _cache.clear()
    _cache_valid = False
    try:
        from .renderer import invalidate_render_cache
        invalidate_render_cache()
    except ImportError:
        pass


def load_help_entries(include_excluded: bool = False) -> List[HelpEntry]:
    """Load and parse help entries from BOTH local plugins and pip-installed plugins.

    Args:
        include_excluded: True=返回全部（WebUI 用），False=过滤排除列表（帮助图用）
    """
    import time as _time
    global _cache, _cache_valid

    cache_key = f"all_{include_excluded}"
    if _cache_valid and cache_key in _cache:
        _t = _time.time()
        result = _cache[cache_key]
        from nonebot import logger
        logger.info(f"[帮助中心] 缓存命中: {len(result)} 条目 ({_time.time() - _t:.2f}s)")
        return result

    _t0 = _time.time()

    entries: List[HelpEntry] = []

    # ── Local plugins (under PLUGINS_ROOTS) ──
    for plugin_id, path in _iter_plugin_sources():
        entry = _entry_from_path(plugin_id, path)
        if entry is not None:
            entries.append(entry)

    # ── External plugins (pip-installed, e.g. nonebot_plugin_wordcloud) ──
    for plugin_id, path in _iter_external_plugin_sources():
        entry = _entry_from_path(plugin_id, path)
        if entry is not None:
            entries.append(entry)

    # ── 过滤排除列表（帮助图专用；WebUI 需要看到全部以便重新启用）──
    if not include_excluded:
        _excluded = CONFIG.exclude_names
        entries = [e for e in entries
                   if e.plugin_id not in _excluded
                   and e.plugin_id.rsplit(".", 1)[-1] not in _excluded]

    # 排序
    from .webui_store import get_module_config as _get_mod_cfg
    from .webui_store import get_plugin_override as _get_plg_ov

    def _sort_key(item: HelpEntry):
        cfg = _get_mod_cfg(item.category)
        mod_order = cfg.get("sort_order", 0) if cfg else 0
        ov = _get_plg_ov(item.plugin_id)
        plg_order = ov.get("sort_order", 0) if ov else 0
        return (mod_order, plg_order, item.display_name.lower())

    entries.sort(key=_sort_key)

    _cache[cache_key] = entries
    _cache_valid = True
    from nonebot import logger
    logger.info(f"[帮助中心] 缓存已建立: {len(entries)} 条目 ({_time.time() - _t0:.2f}s)")
    return entries


def search_entries(entries: List[HelpEntry], keyword: str) -> List[HelpEntry]:
    query = keyword.strip().lower()
    if not query:
        return entries

    scored: List[Tuple[int, HelpEntry]] = []
    for entry in entries:
        score = 0
        if query == entry.plugin_id.lower():
            score += 100
        if query in entry.display_name.lower():
            score += 80
        if any(query == cmd.lower() for cmd in entry.commands):
            score += 90
        if any(query in cmd.lower() for cmd in entry.commands):
            score += 60
        if query in entry.search_blob:
            score += 20
        if score > 0:
            scored.append((score, entry))

    scored.sort(key=lambda item: (-item[0], item[1].category, item[1].display_name.lower()))
    return [entry for _, entry in scored]
