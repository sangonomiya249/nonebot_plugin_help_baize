"""帮助模块管理 API（FastAPI Router）。

挂载到现有 WebUI 的 FastAPI app 上，提供帮助模块/插件的 CRUD 接口。
所有 /api/help/* 端点会被 WebUI 的认证中间件自动保护。
"""

import ast
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import webui_store
from .provider import load_help_entries

router = APIRouter(prefix="/api/help", tags=["help"])

# ==================== 排除列表读写 ====================

def _get_help_init() -> Path:
    """定位 help 插件正在运行的 __init__.py（通过 sys.modules，不受安装位置影响）"""
    import sys
    for _key in ("src.plugins.nonebot_plugin_help_baize", "nonebot_plugin_help_baize"):
        _mod = sys.modules.get(_key)
        if _mod and hasattr(_mod, "__file__") and _mod.__file__:
            return Path(_mod.__file__)
    # 兜底：webui_api 自身所在目录
    return Path(__file__).resolve().parent / "__init__.py"


def _read_excluded_plugins() -> Set[str]:
    """从 __init__.py 读取 EXCLUDE_NAMES。"""
    try:
        _init_path = _get_help_init()
        if not _init_path.is_file():
            return set()
        text = _init_path.read_text(encoding="utf-8")
        for line in text.splitlines():
            m = re.match(r'^EXCLUDE_NAMES\s*=\s*(.+?)(\s*#.*)?$', line)
            if m:
                raw = m.group(1).strip().strip('"').strip("'")
                if raw.startswith("["):
                    try:
                        return set(ast.literal_eval(raw))
                    except Exception:
                        return set(x.strip() for x in raw.strip("[]").split(",") if x.strip())
                else:
                    return set(x.strip() for x in raw.split(",") if x.strip())
    except Exception:
        pass
    return set()


def _write_excluded_plugins(excluded: Set[str]) -> bool:
    """将 EXCLUDE_NAMES 写入 __init__.py。"""
    try:
        _init_path = _get_help_init()
        if not _init_path.is_file():
            return False
        text = _init_path.read_text(encoding="utf-8")
        new_val = repr(sorted(excluded))
        new_line = f"EXCLUDE_NAMES = {new_val}          # 从帮助图中排除的插件名列表"
        replaced = False
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if re.match(r'^EXCLUDE_NAMES\s*=', line):
                lines[i] = new_line
                replaced = True
                break
        if not replaced:
            # 文件中没有 EXCLUDE_NAMES，插入到可编辑配置区域
            for i, line in enumerate(lines):
                if line.startswith("DISPLAY_MODE ="):
                    lines.insert(i + 1, new_line)
                    replaced = True
                    break
        if not replaced:
            return False
        backup = _init_path.with_suffix(".py.bak")
        try:
            shutil.copy2(str(_init_path), str(backup))
        except Exception:
            pass
        _init_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True
    except Exception:
        return False


# ==================== 请求/响应模型 ====================

class ModuleUpdate(BaseModel):
    display_name: Optional[str] = None
    subtitle: Optional[str] = None
    color: Optional[str] = None
    enabled: Optional[bool] = None
    sort_order: Optional[int] = None  # None = 不修改，保留已有值


class PluginOverride(BaseModel):
    display_name: Optional[str] = None
    description: Optional[str] = None
    triggers: Optional[Dict[str, str]] = None  # {trigger_word: custom_description}
    sort_order: Optional[int] = None  # None = 不修改，保留已有值；仅 flat 模式有效
    color: Optional[str] = None  # None = 不修改，保留已有值


# ==================== 辅助函数 ====================

def _build_active_modules() -> List[Dict[str, Any]]:
    """构建活跃模块列表（至少包含 1 个插件的模块）。"""
    entries = load_help_entries(include_excluded=True)
    # 按分类分组
    category_plugins: Dict[str, List[Dict[str, Any]]] = {}
    # 读取当前排除列表
    excluded_set = _read_excluded_plugins()

    for entry in entries:
        cat = entry.category
        if cat not in category_plugins:
            category_plugins[cat] = []
            # 合并插件覆盖中的 triggers / sort_order / color
        override = webui_store.get_plugin_override(entry.plugin_id)
        overridden_triggers = (override or {}).get("triggers", {})
        override_sort = (override or {}).get("sort_order", 0)
        override_color = (override or {}).get("color", "")
        category_plugins[cat].append({
            "plugin_id": entry.plugin_id,
            "display_name": entry.display_name,
            "description": entry.description,
            "commands": entry.commands,
            "notes": entry.notes,
            "category": entry.category,
            "trigger_overrides": overridden_triggers,
            "override_sort_order": override_sort,
            "override_color": override_color,
            "excluded": entry.plugin_id in excluded_set,
        })

    # 合并模块配置
    defaults = webui_store.get_module_defaults()
    overrides = webui_store.load_module_config()

    modules = []
    for cat_name in sorted(category_plugins.keys(), key=lambda n: (
            {**defaults.get(n, {}), **overrides.get(n, {})}.get("sort_order", 0),
            n,
        )):
        cfg = dict(defaults.get(cat_name, {}))
        cfg.update(overrides.get(cat_name, {}))
        modules.append({
            "name": cat_name,
            "display_name": cfg.get("display_name", cat_name),
            "subtitle": cfg.get("subtitle", ""),
            "color": cfg.get("color", "#6366f1"),
            "enabled": cfg.get("enabled", True),
            "sort_order": cfg.get("sort_order", 0),
            "plugin_count": len(category_plugins[cat_name]),
            "plugins": category_plugins[cat_name],
        })

    return modules


# ==================== API 端点 ====================

@router.get("/config")
async def get_config():
    """获取全局帮助配置（display_mode 等）。"""
    return webui_store.load_global_config()


@router.put("/config")
async def update_config(body: Dict[str, Any]):
    """更新全局帮助配置。"""
    cfg = webui_store.load_global_config()
    cfg.update(body)
    webui_store.save_global_config(cfg)
    from .provider import invalidate_help_cache
    invalidate_help_cache()
    return {"success": True, "config": cfg}


@router.get("/modules")
async def list_modules():
    """获取所有活跃模块列表（按模块分组，含插件概要）。"""
    return {"modules": _build_active_modules()}


@router.get("/modules/{name:path}")
async def get_module(name: str):
    """获取单个模块详情（含完整插件列表）。"""
    all_modules = _build_active_modules()
    for mod in all_modules:
        if mod["name"] == name:
            return mod
    raise HTTPException(status_code=404, detail=f"模块 '{name}' 不存在或无活跃插件")


@router.put("/modules/{name:path}")
async def update_module(name: str, body: ModuleUpdate):
    """更新模块配置（display_name / subtitle / color / enabled / sort_order）。

    仅更新显式传入的字段；未传入（None）的字段保留已有值。
    """
    modules = webui_store.load_module_config()
    if name not in modules:
        modules[name] = {}

    # 只更新非 None 字段，避免默认值覆盖已有配置
    updates = {}
    if body.display_name is not None:
        updates["display_name"] = body.display_name
    if body.subtitle is not None:
        updates["subtitle"] = body.subtitle
    if body.color is not None:
        updates["color"] = body.color
    if body.enabled is not None:
        updates["enabled"] = body.enabled
    if body.sort_order is not None:
        updates["sort_order"] = body.sort_order

    modules[name].update(updates)
    webui_store.save_module_config(modules)
    from .provider import invalidate_help_cache
    invalidate_help_cache()
    return {"success": True, "module": name}


@router.post("/modules/reset")
async def reset_module(body: Dict[str, str]):
    """重置指定模块为默认值。"""
    name = body.get("name", "")
    if not name:
        raise HTTPException(status_code=400, detail="缺少模块名称")
    modules = webui_store.load_module_config()
    modules.pop(name, None)
    webui_store.save_module_config(modules)
    return {"success": True, "module": name, "message": f"已重置 '{name}'"}


@router.get("/plugins/{plugin_id:path}")
async def get_plugin(plugin_id: str):
    """获取单个插件的帮助详情（含用户覆盖）。"""
    entries = load_help_entries()
    override = webui_store.get_plugin_override(plugin_id)

    for entry in entries:
        if entry.plugin_id == plugin_id:
            return {
                "plugin_id": entry.plugin_id,
                "display_name": override.get("display_name", entry.display_name) if override else entry.display_name,
                "description": override.get("description", entry.description) if override else entry.description,
                "category": entry.category,
                "commands": entry.commands,
                "notes": entry.notes,
                "trigger_overrides": (override or {}).get("triggers", {}),
                "usage_lines": entry.usage_lines,
                "source_path": entry.source_path,
                "has_override": override is not None,
            }
    raise HTTPException(status_code=404, detail=f"插件 '{plugin_id}' 不存在")


@router.put("/plugins/{plugin_id:path}")
async def update_plugin(plugin_id: str, body: PluginOverride):
    """更新插件显示覆盖（display_name / description / triggers / sort_order / color）。

    仅更新显式传入的字段；未传入（None）的字段保留已有值。
    """
    overrides = webui_store.load_plugin_overrides()
    current = overrides.get(plugin_id, {})

    if body.display_name is not None:
        current["display_name"] = body.display_name
    if body.description is not None:
        current["description"] = body.description
    if body.sort_order is not None:
        current["sort_order"] = body.sort_order
    if body.color is not None:
        current["color"] = body.color
    if body.triggers is not None:
        # 合并 trigger 覆盖：只保存非空值
        existing_triggers = current.get("triggers", {})
        for k, v in body.triggers.items():
            if v:
                existing_triggers[k] = v
        current["triggers"] = existing_triggers

    overrides[plugin_id] = current
    webui_store.save_plugin_overrides(overrides)
    from .provider import invalidate_help_cache
    invalidate_help_cache()
    return {"success": True, "plugin_id": plugin_id}


@router.post("/plugins/{plugin_id:path}/toggle-exclude")
async def toggle_exclude_plugin(plugin_id: str):
    """切换插件是否从帮助图排除。"""
    excluded = _read_excluded_plugins()
    if plugin_id in excluded:
        excluded.discard(plugin_id)
        action = "已启用"
    else:
        excluded.add(plugin_id)
        action = "已禁用"
    if _write_excluded_plugins(excluded):
        from .config import reload_config
        reload_config()
        return {
            "success": True,
            "plugin_id": plugin_id,
            "excluded": plugin_id in excluded,
            "message": f"「{plugin_id}」{action}，重启 Bot 后生效",
        }
    raise HTTPException(status_code=500, detail="写入 EXCLUDE_NAMES 失败")


@router.post("/reload")
async def reload_help():
    """强制重新扫描所有插件并刷新帮助数据缓存。"""
    from . import config as _config
    from .provider import invalidate_help_cache
    _config.reload_config()
    # 重新加载 category_map.json
    global _EXACT_MAP, _CATEGORY_REGEX_RULES
    exact, regex_rules = _config._load_category_data(_config.CATEGORY_MAP_PATH)
    _config._EXACT_MAP = exact
    _config._CATEGORY_REGEX_RULES = regex_rules
    invalidate_help_cache()
    return {"success": True, "message": "帮助数据已刷新"}
