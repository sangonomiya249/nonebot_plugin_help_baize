import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Set, Tuple


CATEGORY_MAP_PATH = Path(__file__).resolve().parent / "category_map.json"


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


# 插件配置页可编辑的常量（WebUI 插件管理 → 帮助中心 → 编辑配置）
HELP_DISPLAY_MODE = "auto"  # "auto"=自动分类, "flat"=单插件模式

@dataclass
class HelpPluginConfig:
    command: str = "帮助"
    aliases: Set[str] = field(default_factory=lambda: {"help", "菜单", "指令帮助"})
    title: str = "流萤Help"
    subtitle: str = "输入 /帮助 关键词 可筛选具体功能"
    display_mode: str = "auto"  # "auto"=自动分类, "flat"=单插件模式
    max_overview_commands: int = 3
    include_hidden_dirs: Set[str] = field(default_factory=lambda: {"gpt_image_draw"})
    exclude_names: Set[str] = field(default_factory=lambda: {"__pycache__", "nonebot_plugin_help_baize"})
    category_map: Dict[str, str] = field(
        default_factory=_build_category_map
    )
    font_candidates: List[str] = field(
        default_factory=lambda: [
            "msyhbd.ttf",
            "msyh.ttf",
            "msyhbd.ttc",
            "msyh.ttc",
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


CONFIG = HelpPluginConfig()
PLUGIN_DIR = Path(__file__).resolve().parent

# 插件根目录（兼容 pip 安装和本地安装两种部署方式）
# 当 help 本地安装时 PLUGIN_DIR.parent = src/plugins/ ✓
# 当 help pip  安装时 PLUGIN_DIR.parent = site-packages/ ✗
# 此时回退到 CWD/src/plugins/
_LOCAL_ROOT = PLUGIN_DIR.parent
_CWD_ROOT = Path.cwd() / "src" / "plugins"
if _CWD_ROOT.is_dir():
    PLUGINS_ROOT = _CWD_ROOT
elif not str(_LOCAL_ROOT.resolve()).endswith(("site-packages", "dist-packages")):
    PLUGINS_ROOT = _LOCAL_ROOT
else:
    # pip 安装且 CWD 没有 src/plugins — 兜底用 CWD（bot.py 会 chdir 到项目根）
    PLUGINS_ROOT = Path.cwd()

# 模块加载时预编译正则规则（与 CONFIG 共享同一份 JSON 文件）
_EXACT_MAP, _CATEGORY_REGEX_RULES = _load_category_data(CATEGORY_MAP_PATH)


def resolve_category(plugin_id: str) -> str:
    """解析插件分类：先精确匹配，再正则兜底，最后返回 "其他"。"""
    # 1. 精确匹配
    if plugin_id in _EXACT_MAP:
        return _EXACT_MAP[plugin_id]

    # 2. 正则兜底（按 JSON 中定义的顺序匹配，先匹配先生效）
    for pattern, category in _CATEGORY_REGEX_RULES:
        if pattern.search(plugin_id):
            return category

    # 3. 未知分类
    return "其他"
