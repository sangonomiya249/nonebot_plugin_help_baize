"""帮助模块管理 API（FastAPI Router）。

挂载到现有 WebUI 的 FastAPI app 上，提供帮助模块/插件的 CRUD 接口。
所有 /api/help/* 端点会被 WebUI 的认证中间件自动保护。
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import webui_store
from .provider import load_help_entries

router = APIRouter(prefix="/api/help", tags=["help"])


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
    entries = load_help_entries()
    # 按分类分组
    category_plugins: Dict[str, List[Dict[str, Any]]] = {}
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
        })

    # 合并模块配置
    defaults = webui_store.get_module_defaults()
    overrides = webui_store.load_module_config()

    modules = []
    for cat_name in sorted(category_plugins.keys(), key=lambda n: (
            {**defaults.get(n, {}), **overrides.get(n, {})}.get("sort_order", 0),
            n,
        )):
        # 跳过"其他"分类（通常是无分类插件的兜底）
        if cat_name == "其他":
            continue
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
    return {"success": True, "plugin_id": plugin_id}


@router.post("/reload")
async def reload_help():
    """强制重新扫描所有插件并刷新帮助数据缓存。"""
    # 清除模块级 Python 缓存（让下次 load_help_entries 重新扫描文件系统）
    from . import config as _config
    # 重新加载 category_map.json
    global _EXACT_MAP, _CATEGORY_REGEX_RULES
    exact, regex_rules = _config._load_category_data(_config.CATEGORY_MAP_PATH)
    _config._EXACT_MAP = exact
    _config._CATEGORY_REGEX_RULES = regex_rules
    return {"success": True, "message": "帮助数据已刷新"}
