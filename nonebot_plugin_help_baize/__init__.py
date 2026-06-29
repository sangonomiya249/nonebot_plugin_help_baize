import asyncio
import base64
import hashlib
import io
from pathlib import Path

import httpx
from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, MessageEvent, MessageSegment
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
from nonebot.plugin import PluginMetadata

from .config import CONFIG, PLUGIN_DIR
from .registry import build_help_result
from .renderer import render_help_images

BG_DIR = PLUGIN_DIR / "data"
BG_DIR.mkdir(parents=True, exist_ok=True)
BG_PATH = BG_DIR / "background.png"

__plugin_meta__ = PluginMetadata(
    name="帮助中心",
    description="自动扫描当前插件并生成可检索的帮助图片",
    usage=(
        "/帮助\n"
        "/帮助 关键词\n"
        "示例：/帮助 绘图\n"
        "示例：/帮助 签到"
    ),
)


help_cmd = on_command(CONFIG.command, aliases=CONFIG.aliases, priority=5, block=True)


def _image_message_from_bytes(data: bytes) -> MessageSegment:
    encoded = base64.b64encode(data).decode("utf-8")
    return MessageSegment.image(f"base64://{encoded}")


def _build_image_node(bot_id: int, nickname: str, image_data: bytes) -> dict:
    """构建合并转发节点（每条消息即一页帮助图片）"""
    msg = Message(_image_message_from_bytes(image_data))
    try:
        seg = MessageSegment.node_custom(user_id=bot_id, nickname=nickname, content=msg)
        data = seg.data if isinstance(seg.data, dict) else {}
        if data:
            return {"type": "node", "data": data}
    except Exception:
        pass
    return {
        "type": "node",
        "data": {"uin": bot_id, "name": nickname, "user_id": bot_id, "nickname": nickname, "content": msg},
    }


@help_cmd.handle()
async def handle_help(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    keyword = arg.extract_plain_text().strip()
    result = await asyncio.to_thread(build_help_result, keyword)
    if keyword and not result.entries:
        await help_cmd.finish(result.subtitle)

    image_pages = await asyncio.to_thread(render_help_images, result)
    if not image_pages:
        await help_cmd.finish("帮助图片生成失败。")

    # 单页直接发图片
    if len(image_pages) == 1:
        await help_cmd.finish(_image_message_from_bytes(image_pages[0]))

    # 多页合并为聊天记录转发
    bot_id = int(bot.self_id)
    nickname = CONFIG.title  # "流萤Help"
    nodes = [_build_image_node(bot_id, nickname, page) for page in image_pages]

    if hasattr(event, 'group_id') and event.group_id:
        await bot.call_api("send_group_forward_msg", group_id=event.group_id, messages=nodes)
    else:
        await bot.call_api("send_private_forward_msg", user_id=event.user_id, messages=nodes)


# ==================== 帮助背景图管理 ====================

bg_cmd = on_command("修改帮助背景图", aliases={"修改帮助背景", "设置帮助背景"}, permission=SUPERUSER, priority=5, block=True)
bg_reset = on_command("重置帮助背景图", aliases={"重置帮助背景", "删除帮助背景"}, permission=SUPERUSER, priority=5, block=True)


async def _download_image(bot: Bot, url: str) -> bytes | None:
    """从 QQ 图片 URL 下载图片数据。"""
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.content
    except Exception:
        pass
    # fallback: 尝试用 bot 的 HTTP API
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                return resp.content
    except Exception:
        pass
    return None


@bg_reset.handle()
async def handle_bg_reset():
    if BG_PATH.exists():
        BG_PATH.unlink()
        await bg_reset.finish("✅ 帮助背景图已重置为默认")
    await bg_reset.finish("帮助背景图已是默认，无需重置")


@bg_cmd.handle()
async def handle_bg_set(bot: Bot, event: MessageEvent, arg: Message = CommandArg()):
    w, h = 0, 0

    async def _save(data: bytes) -> bool:
        nonlocal w, h
        from PIL import Image
        try:
            img = Image.open(io.BytesIO(data))
            img = img.convert("RGB")
            max_w, max_h = 2560, 4000
            if img.width > max_w or img.height > max_h:
                img.thumbnail((max_w, max_h), Image.LANCZOS)
            w, h = img.width, img.height
            img.save(BG_PATH, format="PNG")
            return True
        except Exception:
            BG_PATH.write_bytes(data)
            w, h = 0, 0
            return True

    # 1) 从消息中的图片获取
    for seg in event.message:
        if seg.type == "image":
            url = seg.data.get("url", "")
            if not url:
                url = seg.data.get("file", "")
            if url:
                data = await _download_image(bot, url)
                if data:
                    await _save(data)
                    await bg_cmd.finish(f"✅ 帮助背景图已更新（{w}x{h}），发送 /帮助 查看效果")
                await bg_cmd.finish("❌ 下载图片失败，请重试")

    # 2) 从回复消息中获取
    if event.reply:
        for seg in event.reply.message:
            if seg.type == "image":
                url = seg.data.get("url", "")
                if url:
                    data = await _download_image(bot, url)
                    if data:
                        await _save(data)
                        await bg_cmd.finish(f"✅ 帮助背景图已更新（{w}x{h}），发送 /帮助 查看效果")

    await bg_cmd.finish("请附带一张图片或回复一张图片使用此命令")
