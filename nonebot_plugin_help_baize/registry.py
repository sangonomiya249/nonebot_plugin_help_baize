from .models import HelpQueryResult
from .provider import load_help_entries, search_entries
from .webui_store import get_module_config, get_page_title, get_page_subtitle


def _resolve_module_name(keyword: str) -> str:
    """尝试将关键词解析为模块名（匹配内部分类名或用户自定义显示名）。"""
    kw = keyword.strip()
    if not kw:
        return ""

    # 先精确匹配内部分类名
    defaults = get_module_config(kw)
    if defaults:
        return kw

    # 再遍历所有模块，匹配用户自定义的 display_name
    from .webui_store import get_module_defaults, load_module_config
    all_defaults = get_module_defaults()
    all_overrides = load_module_config()
    for cat_name in set(list(all_defaults.keys()) + list(all_overrides.keys())):
        cfg = all_defaults.get(cat_name, {})
        cfg.update(all_overrides.get(cat_name, {}))
        if cfg.get("display_name", cat_name) == kw:
            return cat_name

    return ""


def build_help_result(keyword: str, module_name: str = "") -> HelpQueryResult:
    entries = load_help_entries()

    # 如果指定了模块名，只显示该模块
    if module_name:
        entries = [e for e in entries if e.category == module_name]
        if not entries:
            return HelpQueryResult(
                title=get_page_title(),
                subtitle=f"模块「{module_name}」下暂无可用功能。",
                entries=[],
                keyword=module_name,
            )
        # 获取模块显示名
        cfg = get_module_config(module_name)
        display = cfg.get("display_name", module_name) if cfg else module_name
        return HelpQueryResult(
            title=f"{get_page_title()} | {display}",
            subtitle=f"共 {len(entries)} 个功能",
            entries=entries,
            keyword=module_name,
        )

    if keyword:
        # 尝试将 keyword 解析为模块名
        found_module = _resolve_module_name(keyword)
        if found_module:
            entries = [e for e in entries if e.category == found_module]
            if entries:
                cfg = get_module_config(found_module)
                display = cfg.get("display_name", found_module) if cfg else found_module
                return HelpQueryResult(
                    title=f"{get_page_title()} | {display}",
                    subtitle=f"共 {len(entries)} 个功能",
                    entries=entries,
                    keyword=found_module,
                )

        # 普通关键词搜索
        matched = search_entries(entries, keyword)
        if not matched:
            return HelpQueryResult(
                title=get_page_title(),
                subtitle=f"没有找到与""{keyword}""相关的功能，试试更短的关键词。",
                entries=[],
                keyword=keyword,
            )
        return HelpQueryResult(
            title=f"{get_page_title()} | {keyword}",
            subtitle=f"共找到 {len(matched)} 个相关功能",
            entries=matched[:8],
            keyword=keyword,
        )

    return HelpQueryResult(
        title=get_page_title(),
        subtitle=get_page_subtitle(),
        entries=entries,
        keyword="",
    )
