"""帮助模块配置持久化存储。

数据文件:
    data/help_modules.json       — 模块级自定义（display_name / subtitle / color / enabled）
    data/help_plugin_overrides.json — 插件级覆盖（display_name / description）
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

# 数据目录解析（兼容 pip 安装和本地安装两种部署方式）
# 设计原则：数据文件持久化在项目根目录下，不随 pip 重装/升级丢失
#
# 优先级：
#   1. CWD/data/（项目根 data 目录，bot.py 会 chdir 到项目根）—— 无条件优先
#   2. 本地 webui 的 data/（与 webui 共享，旧版兼容）
#   3. help 自身 data/（兜底，确保总能工作）
#
# 迁移策略：如果 CWD/data/ 为空但旧位置有数据文件，自动迁移
_THIS_DIR = Path(__file__).resolve().parent
_CWD_DATA = Path.cwd() / "data"
_LOCAL_WEBUI_DATA = _THIS_DIR.parent / "nonebot_plugin_webui_baize" / "data"
_OWN_DATA = _THIS_DIR / "data"

# 确保 CWD/data/ 存在并作为首选
try:
    _CWD_DATA.mkdir(parents=True, exist_ok=True)
    _DATA_DIR = _CWD_DATA
except OSError:
    # CWD 不可写（如只读文件系统）→ 回退到旧逻辑
    if _LOCAL_WEBUI_DATA.is_dir():
        _DATA_DIR = _LOCAL_WEBUI_DATA
    else:
        _DATA_DIR = _OWN_DATA

# 自动迁移：如果目标目录为空但旧位置有数据文件，复制过来
def _migrate_data_if_needed():
    """如果 CWD/data/ 中没有 help 配置但旧位置中存在，自动迁移。"""
    if _DATA_DIR == _CWD_DATA:
        _need = []
        for _name in ("help_modules.json", "help_plugin_overrides.json"):
            _new = _CWD_DATA / _name
            if _new.exists():
                continue
            for _old_root in (_LOCAL_WEBUI_DATA, _OWN_DATA):
                _old = _old_root / _name
                if _old.exists():
                    _need.append((_old, _new))
                    break
        if _need:
            import shutil
            for _old_path, _new_path in _need:
                try:
                    shutil.copy2(str(_old_path), str(_new_path))
                except OSError:
                    pass

_migrate_data_if_needed()
_DATA_DIR.mkdir(parents=True, exist_ok=True)

_MODULES_PATH = _DATA_DIR / "help_modules.json"
_OVERRIDES_PATH = _DATA_DIR / "help_plugin_overrides.json"

DEFAULT_COLORS = [
    "#6366f1",  # 靛蓝
    "#10b981",  # 翠绿
    "#f59e0b",  # 琥珀
    "#ef4444",  # 红色
    "#8b5cf6",  # 紫色
    "#06b6d4",  # 青色
    "#ec4899",  # 粉红
    "#14b8a6",  # 青绿
    "#f97316",  # 橙色
    "#84cc16",  # 黄绿
]


# ==================== 模块配置 ====================

def load_module_config() -> Dict[str, Dict[str, Any]]:
    """加载模块配置，返回 {module_name: {display_name, subtitle, color, enabled, sort_order}}。"""
    if not _MODULES_PATH.exists():
        return {}
    try:
        data = json.loads(_MODULES_PATH.read_text(encoding="utf-8"))
        return data.get("modules", {})
    except (json.JSONDecodeError, OSError):
        return {}


def _load_full_data() -> Dict[str, Any]:
    """加载完整的 help_modules.json 数据。"""
    if not _MODULES_PATH.exists():
        return {}
    try:
        return json.loads(_MODULES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_full_data(data: Dict[str, Any]) -> None:
    """保存完整的 help_modules.json 数据。"""
    _MODULES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_module_config(modules: Dict[str, Dict[str, Any]]) -> None:
    """保存模块配置到 JSON 文件（保留 global 配置）。"""
    full = _load_full_data()
    full["modules"] = modules
    _save_full_data(full)


# ==================== 全局配置（display_mode 等） ====================

def load_global_config() -> Dict[str, Any]:
    """加载全局配置，返回 {display_mode: \"auto\"|\"flat\", ...}。"""
    data = _load_full_data()
    return data.get("global", {})


def save_global_config(cfg: Dict[str, Any]) -> None:
    """保存全局配置。"""
    full = _load_full_data()
    full["global"] = cfg
    _save_full_data(full)


def get_display_mode() -> str:
    """获取当前显示模式。

    优先级：WebUI 帮助模块 > 插件配置页 HELP_DISPLAY_MODE > config.py > "auto"
    """
    from .config import CONFIG
    webui_mode = load_global_config().get("display_mode", "")
    if webui_mode in ("auto", "flat"):
        return webui_mode
    # 插件配置页可编辑的常量
    from .config import HELP_DISPLAY_MODE
    if HELP_DISPLAY_MODE in ("auto", "flat"):
        return HELP_DISPLAY_MODE
    return getattr(CONFIG, "display_mode", "auto") or "auto"


def get_page_title() -> str:
    """获取帮助图大标题。WebUI 有保存时优先，否则用 config.py 的 title。"""
    from .config import CONFIG
    return load_global_config().get("page_title", "") or CONFIG.title


def get_page_subtitle() -> str:
    """获取帮助图副标题。WebUI 有保存时优先，否则用 config.py 的 subtitle。"""
    from .config import CONFIG
    return load_global_config().get("page_subtitle", "") or CONFIG.subtitle


def get_module_defaults() -> Dict[str, Dict[str, Any]]:
    """从 category_map.json 生成模块默认值。"""
    from .config import CATEGORY_MAP_PATH

    if not CATEGORY_MAP_PATH.exists():
        return {}

    try:
        data = json.loads(CATEGORY_MAP_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}

    defaults: Dict[str, Dict[str, Any]] = {}
    seen_categories: Dict[str, int] = {}  # name → first index
    idx = 0

    # 精确分类
    for group in data.get("categories", []):
        name = group.get("name", "")
        if not name or name in seen_categories:
            continue
        seen_categories[name] = idx
        defaults[name] = {
            "display_name": name,
            "subtitle": "",
            "color": DEFAULT_COLORS[idx % len(DEFAULT_COLORS)],
            "enabled": True,
            "sort_order": idx,
        }
        idx += 1

    # 正则分类（仅出现在 regex_rules 中的新分类）
    for rule in data.get("regex_rules", []):
        name = rule.get("category", "")
        if not name or name in seen_categories:
            continue
        seen_categories[name] = idx
        defaults[name] = {
            "display_name": name,
            "subtitle": "",
            "color": DEFAULT_COLORS[idx % len(DEFAULT_COLORS)],
            "enabled": True,
            "sort_order": idx,
        }
        idx += 1

    return defaults


def get_module_config(name: str) -> Optional[Dict[str, Any]]:
    """获取单个模块的配置（合并默认值 + 用户覆盖）。"""
    defaults = get_module_defaults()
    overrides = load_module_config()

    if name not in defaults and name not in overrides:
        return None

    base = dict(defaults.get(name, {}))
    base.update(overrides.get(name, {}))
    return base


# ==================== 插件覆盖 ====================

def load_plugin_overrides() -> Dict[str, Dict[str, Any]]:
    """加载插件覆盖，返回 {plugin_id: {display_name, description}}。"""
    if not _OVERRIDES_PATH.exists():
        return {}
    try:
        data = json.loads(_OVERRIDES_PATH.read_text(encoding="utf-8"))
        return data.get("overrides", {})
    except (json.JSONDecodeError, OSError):
        return {}


def save_plugin_overrides(overrides: Dict[str, Dict[str, Any]]) -> None:
    """保存插件覆盖到 JSON 文件。"""
    _OVERRIDES_PATH.write_text(
        json.dumps({"overrides": overrides}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def get_plugin_override(plugin_id: str) -> Optional[Dict[str, Any]]:
    """获取单个插件的覆盖配置。"""
    return load_plugin_overrides().get(plugin_id)
