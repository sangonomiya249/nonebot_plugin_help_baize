import ast
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple

from nonebot import logger

CATEGORY_MAP_PATH = Path(__file__).resolve().parent / "category_map.json"

# ═══════════════════ .env 覆盖支持 ═══════════════════
_ENV_PREFIX= 'HELP_',
_env_values: dict = {}
_root = Path.cwd()


def _load_env_values():
    _env_values.clear()

    def _read_env_file(_env_file: Path):
        try:
            if _env_file.exists():
                for _line in _env_file.read_text(encoding="utf-8").splitlines():
                    _line = _line.strip()
                    if not _line or _line.startswith("#"):
                        continue
                    _m = re.match(r'^([A-Z_][A-Z0-9_]*)\s*=\s*(.+)$', _line)
                    if _m:
                        _env_values[_m.group(1)] = _m.group(2).strip().strip('"').strip("'")
        except Exception:
            pass

    _read_env_file(_root / ".env")
    _environment = os.environ.get("ENVIRONMENT") or _env_values.get("ENVIRONMENT")
    if _environment:
        _read_env_file(_root / f".env.{_environment}")


_load_env_values()


def _env(key: str, default):
    """优先 .env (HELP_XXX)，否则默认值"""
    _pfx = _ENV_PREFIX[0] if isinstance(_ENV_PREFIX, tuple) else _ENV_PREFIX
    val = _env_values.get(f"{_pfx}{key}")
    if val is not None and val.strip():
        if isinstance(default, bool):
            return val.lower() in ("true", "1", "yes")
        if isinstance(default, int):
            return int(val)
        if isinstance(default, float):
            return float(val)
        if isinstance(default, list):
            return [x.strip() for x in val.split(",") if x.strip()]
        return val
    return default


def _load_category_data(json_path: Path) -> Tuple[Dict[str, str], List[Tuple[re.Pattern, str]]]:
    """从 category_map.json 加载分类数据。

    返回:
        (plugin_id → category 精确映射, [(编译后的正则, 分类名), ...])
    """
    exact: Dict[str, str] = {}
    regex_list: List[Tuple[re.Pattern, str]] = []

    if not json_path.exists():
        return exact, regex_list

    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return exact, regex_list

    # 精确匹配
    for group in data.get("categories", []):
        category_name = group.get("name", "")
        if not category_name:
            continue
        for plugin_id in group.get("plugins", []):
            exact[plugin_id] = category_name

    # 正则匹配（按顺序编译，先匹配先生效）
    for rule in data.get("regex_rules", []):
        pattern = rule.get("pattern", "")
        category = rule.get("category", "")
        if not pattern or not category:
            continue
        try:
            compiled = re.compile(pattern, re.IGNORECASE)
            regex_list.append((compiled, category))
        except re.error:
            continue

    return exact, regex_list


def _build_category_map() -> Dict[str, str]:
    """仅返回精确映射（供 dataclass default_factory 使用）。"""
    exact, _ = _load_category_data(CATEGORY_MAP_PATH)
    return exact


# ═══════════════════ 数据模型 ═══════════════════

@dataclass
class HelpPluginConfig:
    command: str = "帮助"
    aliases: Set[str] = field(default_factory=lambda: {"help", "菜单", "指令帮助"})
    title: str = "流萤Help"
    subtitle: str = "输入 /帮助 关键词 可筛选具体功能"
    display_mode: str = "auto"
    max_overview_commands: int = 3
    include_hidden_dirs: Set[str] = field(default_factory=lambda: {"gpt_image_draw"})
    exclude_names: List[str] = field(default_factory=lambda: ["__pycache__"])
    category_map: Dict[str, str] = field(default_factory=_build_category_map)
    font_candidates: List[str] = field(
        default_factory=lambda: [
            "msyhbd.ttf", "msyh.ttf", "msyhbd.ttc", "msyh.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
            "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            "/usr/share/fonts/truetype/msttcorefonts/Arial.ttf",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/simhei.ttf",
        ]
    )


# 向后兼容：webui_store.py 引用此常量
HELP_DISPLAY_MODE = "auto"

# ═══════════════════ 配置实例 ═══════════════════
# 空构造函数 → WebUI 通过 dataclass 字段解析（可读）
# .env 覆盖在 CONFIG 创建后立即应用（运行时生效）
CONFIG = HelpPluginConfig()


def _apply_overrides():
    """启动时 / 热重载时应用 .env 和 WebUI 修改的覆盖值"""
    if _env("COMMAND", None) is not None:
        CONFIG.command = _env("COMMAND", "帮助")
    if _env("TITLE", None) is not None:
        CONFIG.title = _env("TITLE", "流萤Help")
    if _env("SUBTITLE", None) is not None:
        CONFIG.subtitle = _env("SUBTITLE", "输入 /帮助 关键词 可筛选具体功能")
    if _env("DISPLAY_MODE", None) is not None:
        CONFIG.display_mode = _env("DISPLAY_MODE", "auto")

    # EXCLUDE_NAMES: .env 和 WebUI 合并（取并集，两边都可以独立控制）
    _env_exclude = _env("EXCLUDE_NAMES", None)
    _env_list = []
    if _env_exclude is not None:
        if isinstance(_env_exclude, str):
            _env_list = [x.strip() for x in _env_exclude.split(",") if x.strip()]
        elif isinstance(_env_exclude, list):
            _env_list = _env_exclude

    # 从 __init__.py 直接读取 WebUI 设置
    _webui_exclude = None
    _init_file = Path(__file__).resolve().parent / "__init__.py"
    if _init_file.is_file():
        try:
            _init_text = _init_file.read_text(encoding="utf-8")
            for _line in _init_text.splitlines():
                _m = re.match(r'^EXCLUDE_NAMES\s*=\s*(.+?)(\s*#.*)?$', _line)
                if _m:
                    _raw = _m.group(1).strip().strip('"').strip("'")
                    if _raw.startswith("["):
                        try:
                            _webui_exclude = ast.literal_eval(_raw)
                        except Exception:
                            _webui_exclude = [x.strip() for x in _raw.strip("[]").split(",") if x.strip()]
                    else:
                        _webui_exclude = [x.strip() for x in _raw.split(",") if x.strip()]
                    break
        except Exception:
            pass
    _webui_list = _webui_exclude if isinstance(_webui_exclude, list) else []

    # 合并取并集
    _merged = set(_env_list) | set(_webui_list)
    if _merged:
        CONFIG.exclude_names = sorted(_merged)
    logger.info(f"[帮助中心] exclude_names = {CONFIG.exclude_names} "
                f"(env={_env_list}, webui={_webui_list})")


_apply_overrides()

PLUGIN_DIR = Path(__file__).resolve().parent

# ═══════════════════ 插件目录列表 ═══════════════════
_LOCAL_ROOT = PLUGIN_DIR.parent
_CWD_ROOTS: list = []
for _cand in (Path.cwd() / "src" / "plugins", Path.cwd() / "plugins"):
    if _cand.is_dir():
        _CWD_ROOTS.append(_cand)

PLUGINS_ROOTS: list = []
if not str(_LOCAL_ROOT.resolve()).endswith(("site-packages", "dist-packages")):
    PLUGINS_ROOTS.append(_LOCAL_ROOT)
for _r in _CWD_ROOTS:
    if _r.resolve() not in [p.resolve() for p in PLUGINS_ROOTS]:
        PLUGINS_ROOTS.append(_r)
if not PLUGINS_ROOTS:
    PLUGINS_ROOTS = [Path.cwd()]

_EXACT_MAP, _CATEGORY_REGEX_RULES = _load_category_data(CATEGORY_MAP_PATH)


# ═══════════════════ 热重载 ═══════════════════

def reload_config():
    """运行时重新读取 .env，刷新 CONFIG（供 WebUI 热重载）"""
    global _env_values
    _load_env_values()
    CONFIG.__dict__.clear()
    CONFIG.__dict__.update(HelpPluginConfig().__dict__)
    if _env("COMMAND", None) is not None:
        CONFIG.command = _env("COMMAND", "帮助")
    if _env("TITLE", None) is not None:
        CONFIG.title = _env("TITLE", "流萤Help")
    if _env("SUBTITLE", None) is not None:
        CONFIG.subtitle = _env("SUBTITLE", "输入 /帮助 关键词 可筛选具体功能")
    if _env("DISPLAY_MODE", None) is not None:
        CONFIG.display_mode = _env("DISPLAY_MODE", "auto")
    _env_exclude = _env("EXCLUDE_NAMES", None)
    _env_list = []
    if _env_exclude is not None:
        if isinstance(_env_exclude, str):
            _env_list = [x.strip() for x in _env_exclude.split(",") if x.strip()]
        elif isinstance(_env_exclude, list):
            _env_list = _env_exclude
    _init_file = Path(__file__).resolve().parent / "__init__.py"
    _webui_list = []
    if _init_file.is_file():
        try:
            for _line in _init_file.read_text(encoding="utf-8").splitlines():
                _m = re.match(r'^EXCLUDE_NAMES\s*=\s*(.+?)(\s*#.*)?$', _line)
                if _m:
                    _raw = _m.group(1).strip().strip('"').strip("'")
                    if _raw.startswith("["):
                        try: _webui_list = ast.literal_eval(_raw)
                        except Exception: pass
                    else:
                        _webui_list = [x.strip() for x in _raw.split(",") if x.strip()]
                    break
        except Exception:
            pass
    _merged = set(_env_list) | set(_webui_list)
    if _merged:
        CONFIG.exclude_names = sorted(_merged)
    from .provider import invalidate_help_cache
    invalidate_help_cache()
    logger.info("[帮助中心] CONFIG 已重新加载，缓存已刷新")


# ═══════════════════ 分类解析 ═══════════════════

def resolve_category(plugin_id: str) -> str:
    """解析插件分类：先精确匹配，再正则兜底，最后返回 "其他"。"""
    if plugin_id in _EXACT_MAP:
        return _EXACT_MAP[plugin_id]
    for pattern, category in _CATEGORY_REGEX_RULES:
        if pattern.search(plugin_id):
            return category
    return "其他"
