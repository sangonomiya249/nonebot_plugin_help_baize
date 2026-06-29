# nonebot_plugin_help_baize

自动扫描全部插件并生成精美分类帮助图片，支持 Web UI 在线管理分类与排序。

### 预览

<img src="https://raw.githubusercontent.com/sangonomiya249/nonebot_plugin_help_baize/main/screenshots/help-overview.png" width="600" alt="帮助概览">

### 安装

```bash
pip install git+https://github.com/sangonomiya249/nonebot_plugin_help_baize.git
```

> **Web UI 管理功能**需要同时安装 [nonebot_plugin_webui_baize](https://github.com/sangonomiya249/nonebot_plugin_webui_baize)，否则只能通过 `/帮助` 指令使用基础帮助功能。


### Web UI 管理（分类 / 排序 / 覆盖）

在 Web UI「帮助模块」中可在线管理：

- 模块级：自定义分类显示名、副标题、颜色、排序、启用/禁用
- 插件级：覆盖显示名、描述，自定义单个指令的描述文字
- 全局：切换自动分类 / 平坦模式，自定义页面标题

<img src="https://raw.githubusercontent.com/sangonomiya249/nonebot_plugin_help_baize/main/screenshots/webui-help-modules.png" width="600" alt="WebUI 帮助模块管理">


### 自定义背景图

发送 `/修改帮助背景图` 并附带图片即可将帮助卡片背景替换为自定图片。

`/重置帮助背景图` 恢复默认。


## 📋 指令

| 指令 | 权限 | 说明 |
| ---- | ---- | ---- |
| `/帮助` | 所有人 | 查看自动分类帮助 |
| `/帮助 关键词` | 所有人 | 搜索特定功能 |
| `/修改帮助背景图` | 超管 | 设置自定义背景（需附带图片） |
| `/重置帮助背景图` | 超管 | 恢复默认背景 |

## ⚙️ 配置项

### 可选

| 配置项 | 默认值 | 说明 |
| ------ | ------ | ---- |
| `HELP_DISPLAY_MODE` | `"auto"` | 显示模式：`"auto"` 自动分类 / `"flat"` 平坦单插件 |

> 以上配置可在 Web UI「插件管理」→「帮助中心」→「编辑配置」中直接修改，重启后生效。

## 📦 依赖

- Python >= 3.9
- nonebot2 >= 2.2.0
- Pillow >= 9.0（图片渲染）
- httpx >= 0.20（下载背景图）
- fastapi + pydantic（Web UI 管理接口）
- nonebot-adapter-onebot >= 2.0

## 📁 目录结构

```
nonebot_plugin_help_baize/
├── __init__.py          # 插件入口、指令注册
├── config.py            # 配置常量、分类解析、PLUGINS_ROOT
├── models.py            # 数据模型 HelpEntry / HelpQueryResult
├── provider.py          # 插件扫描、触发词提取、AST 解析
├── registry.py          # 帮助查询入口（搜索/分类/单插件）
├── renderer.py          # Pillow 图片渲染引擎
├── webui_api.py         # FastAPI Router（挂载到 Web UI）
├── webui_store.py       # 配置持久化（help_modules.json）
├── category_map.json    # 分类映射表（精确 + 正则）
├── data/                # 运行时数据（自动创建）
│   ├── background.png   # 自定义背景图
│   ├── help_modules.json
│   └── help_plugin_overrides.json
└── screenshots/         # README 预览图
```

## 📄 许可证

MIT
