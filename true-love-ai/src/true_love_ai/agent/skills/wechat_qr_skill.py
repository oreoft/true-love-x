# -*- coding: utf-8 -*-
"""微信扫码连通道 Skill（通过 Nexu 生成二维码，后台完成绑定）"""
import logging

from true_love_ai.agent.skill_registry import SkillFailed, register_skill

LOG = logging.getLogger("WechatQrSkill")

# 后台绑定没成功时发给扫码的人
BIND_FAILED_TEXT = "呜呜~领养没成功捏，二维码可能过期了，再说一次[领养真爱粉]重新扫码试试吧~"


@register_skill({
    "type": "function",
    "function": {
        "name": "wechat_qr_connect",
        "description": (
            "生成微信扫码登录二维码，用于将微信账号接入系统通道。"
            "当用户说[领养真爱粉]等时使用。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": []
        }
    }
})
async def wechat_qr_connect(params: dict, ctx: dict) -> str:
    from true_love_ai.core.config import get_config

    cfg = get_config()
    nexu = cfg.nexu
    if not nexu.base_url:
        return "Nexu 服务未配置，无法生成二维码"

    url = f"{nexu.base_url}/api/v1/channels/wechat/qr-start"
    headers = {"Authorization": f"Bearer {nexu.token}"} if nexu.token else {}

    qr_data = None
    last_error = None
    for attempt in range(1, 4):
        try:
            from true_love_common.http.client import async_post
            res = await async_post(url, headers=headers, timeout=30.0)
            res.raise_for_status()
            qr_data = res.data if isinstance(res.data, dict) else {}
            LOG.info("Nexu qr-start 响应: %s", qr_data)
            break
        except Exception as e:
            last_error = e
            LOG.warning("调用 Nexu qr-start 失败 (第%d次): %s", attempt, e)

    if qr_data is None:
        raise SkillFailed("抱歉捏，openclaw 服务暂时不可用，请稍后再试吧~") from last_error

    receiver = ctx.get("receiver", "")

    def start_bind():
        """二维码交到用户手上以后，后台等扫码完成绑定"""
        session_key = qr_data.get("sessionKey")
        if session_key:
            from true_love_ai.core.background import spawn
            spawn(_wait_and_bind(session_key, nexu.base_url, headers, receiver), "wechat_qr_bind")

    qr_url = qr_data.get("qrDataUrl", "")
    message = qr_data.get("message", "使用微信扫描以下二维码，以完成领养。")
    if qr_url and receiver:
        try:
            filename = _make_qr_image(qr_url)
        except Exception as e:
            raise SkillFailed("呜呜，我本想给你画个二维码的，但是笔断了捏，稍后再试试吧~") from e

        from true_love_ai.agent.server_client import send_file, send_text
        from true_love_ai.services.image_service import GEN_IMG_DIR
        if not await send_text(receiver, message):
            LOG.warning("二维码说明没发出去: receiver=%s", receiver)
        if not await send_file(receiver, f"{GEN_IMG_DIR.name}/{filename}"):
            raise SkillFailed("呜呜，二维码画好了但是没发出去捏，稍后再试试吧~")
        start_bind()
        return "好的！二维码已发送，请用微信扫描完成领养哦~"

    start_bind()
    return message


def _make_qr_image(qr_url: str) -> str:
    """把二维码内容画成图片存进生图目录，返回文件名"""
    import io
    import uuid
    import qrcode
    from true_love_ai.services.image_service import GEN_IMG_DIR

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=10,
        border=4,
    )
    qr.add_data(qr_url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    buf = io.BytesIO()
    img.save(buf, format="JPEG")

    filename = f"{uuid.uuid4().hex}.jpg"
    (GEN_IMG_DIR / filename).write_bytes(buf.getvalue())
    return filename


async def _wait_and_bind(session_key: str, base_url: str, headers: dict, receiver: str) -> None:
    """后台轮询扫码结果，完成最终绑定；绑定没成功时告诉扫码的人"""
    try:
        bound = await _bind(session_key, base_url, headers)
    except Exception as e:
        LOG.exception("后台微信扫码绑定流程出错: %s", e)
        bound = False
    if not bound:
        await _tell(receiver, BIND_FAILED_TEXT)


async def _tell(receiver: str, text: str) -> None:
    """后台流程里给用户发一句话；没有接收者时转给管理员"""
    from true_love_ai.agent.server_client import notify_master, send_text
    ok = await send_text(receiver, text) if receiver else await notify_master(text)
    if not ok:
        LOG.warning("扫码绑定结果没发出去: receiver=%s text=%s", receiver, text)


async def _bind(session_key: str, base_url: str, headers: dict) -> bool:
    """等扫码并绑定通道，返回是否绑定成功；没扫码或超时返回 False，接口报错抛异常"""
    from true_love_common.http.client import async_post

    wait_url = f"{base_url}/api/v1/channels/wechat/qr-wait"
    connect_url = f"{base_url}/api/v1/channels/wechat/connect"

    LOG.info("开始后台轮询 wechat-qr-wait, sessionKey=%s", session_key)
    wait_res = await async_post(wait_url, json={"sessionKey": session_key}, headers=headers, timeout=600.0)
    wait_res.raise_for_status()
    wait_data = wait_res.data if isinstance(wait_res.data, dict) else {}
    LOG.info("wechat-qr-wait 结果: %s", wait_data)

    if not (wait_data.get("connected") and wait_data.get("accountId")):
        LOG.warning("qr-wait 返回非预期结果或超时: %s", wait_data)
        return False
    account_id = wait_data["accountId"]
    LOG.info("扫码成功，绑定 accountId=%s", account_id)
    conn_res = await async_post(connect_url, json={"accountId": account_id}, headers=headers, timeout=30.0)
    conn_res.raise_for_status()
    LOG.info("微信通道绑定成功: %s", conn_res.data)
    return True
