# -*- coding: utf-8 -*-
"""
Job Process - 定时任务处理

包含各种定时任务的具体实现。要往外发消息的任务第一个参数是 bot_id（从哪个机器人发），第二个是接收者。
"""

import asyncio
import functools
import logging
import os
import threading
import time
from datetime import datetime

import pytz
from bs4 import BeautifulSoup
from PIL import Image

from true_love_common.http.client import get, post
from true_love_common import r2

from ..services import base_client
from ..services.ai_client.business import fetch_data
from ..core import Config

_config = Config()
alapi_config = _config.ALAPI
LOG = logging.getLogger("JobProcess")

# 摸鱼图、早报图的目录，通过 /media 开放给 base 下载后发送；所有机器人共用，一天只下载一次

# 默认网络请求超时时间（秒）
DEFAULT_TIMEOUT = 60

# 早报开头的问候：国内早上一条，美国早上一条
CN_MORNING = "早上好☀️家人萌~"
US_MORNING = "早上好☀️友友们~, \n现在国内太阳已经落下, 多赢阿美莉卡一天"

# 早报里跟在问候后面的行情：(小标题, AI 的 /data 接口, 参数)；AI 查不到就返回空串，这一段直接不发
MARKET_SECTIONS = [
    ("今日日元汇率情况：", "/data/currency", {"currency": "日元"}),
    ("今日美元汇率情况：", "/data/currency", {"currency": "美元"}),
    ("今日黄金汇率情况：", "/data/gold", None),
]


def log_function_execution(func):
    """装饰器：在函数执行前后打印信息，并记录执行时间。"""

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        LOG.info("开始执行job:[%s]", func.__name__)

        result = func(*args, **kwargs)

        LOG.info("job:[%s]执行完毕，cost:[%s]ms", func.__name__, (time.time() - start_time) * 1000)
        return result

    return wrapper


def _send_img(bot_id: str, path: str, receiver: str) -> tuple[bool, str]:
    """发 server 自己目录里的图片（path 相对工作目录）：先传到 R2，base 用预签名链接下载"""
    async def send() -> tuple[bool, str]:
        try:
            url = await r2.upload(r2.R2Config.from_dict(_config.R2), path, "server")
        except Exception as e:
            LOG.error("图片上传 R2 失败: %s", path, exc_info=True)
            return False, f"上传 R2 失败: {e}"
        return await base_client.send_file(bot_id, url, receiver)
    return asyncio.run(send())


def send_daily_notice(bot_id, room_id, content=CN_MORNING):
    """推文字和两张图，各自独立；文字或已有的图没发出去就在最后抛异常，调度器记失败并通知管理员"""
    try:
        ensure_today_images()
    except Exception:
        # 图片下载失败也照常推文字
        LOG.exception("下载当天图片失败")
    # 图片按北京时间的日期命名，和下载时一致
    current_date = get_current_date()

    for title, path, params in MARKET_SECTIONS:
        text = fetch_data(path, params)
        if text:
            content += f"\n\n{title}\n{text}"

    failures = []
    ok, err = asyncio.run(base_client.send_text(bot_id, room_id, '', content))
    if not ok:
        LOG.error("早报文字发送失败: bot_id=%s receiver=%s err=%s", bot_id, room_id, err)
        failures.append(f"文字: {err}")
    for folder in ("moyu-jpg", "zaobao-jpg"):
        image_path = f'{folder}/{current_date}.jpg'
        if not check_image_openable(image_path):
            # 没下载到不算推送失败，下载失败时已经记过
            LOG.warning("今天的图片没准备好，不发: %s", image_path)
            continue
        time.sleep(2)
        ok, err = _send_img(bot_id, image_path, room_id)
        if ok:
            LOG.info("早报图片已发送: bot_id=%s receiver=%s path=%s", bot_id, room_id, image_path)
        else:
            LOG.error("早报图片发送失败: bot_id=%s receiver=%s path=%s err=%s", bot_id, room_id, image_path, err)
            failures.append(f"{image_path}: {err}")
    if failures:
        raise RuntimeError(f"早报推送到 {room_id} 有 {len(failures)} 项失败: {'; '.join(failures)}")


def notice_moyu_schedule(bot_id, room_id):
    send_daily_notice(bot_id, room_id)


def notice_usa_moyu_schedule(bot_id, room_id):
    send_daily_notice(bot_id, room_id, US_MORNING)


_download_lock = threading.Lock()


def ensure_today_images():
    """当天的摸鱼图、早报图还没有就先下载，两张各下各的，互不拖累；加锁，同时触发的推送只下载一次"""
    with _download_lock:
        current_date = get_current_date()
        for folder, download in (("moyu-jpg", download_moyu_file), ("zaobao-jpg", download_zao_bao_file)):
            if check_image_openable(f'{folder}/{current_date}.jpg'):
                continue
            try:
                download()
            except Exception:
                LOG.exception("下载 %s 失败", folder)


@log_function_execution
def download_moyu_file():
    # 使用当前工作目录
    download_directory = 'moyu-jpg/'
    os.makedirs(download_directory, exist_ok=True)
    local_filename = f'{get_current_date()}.jpg'
    full_file_path = os.path.join(download_directory, local_filename)
    retry_count = 3
    file_url = ''
    for i in range(retry_count):
        try:
            file_url = get_moyu_url_by_wx()
            if file_url:
                break
            LOG.warning("download_moyu_file 未能获取到数据，重试中... Retry count:%d", i + 1)
            time.sleep(30)
        except Exception:
            LOG.warning("download_moyu_file 取链接失败，第 %d 次", i + 1, exc_info=True)
            time.sleep(5)
    if file_url:
        response = get(file_url, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        with open(full_file_path, 'wb') as f:
            f.write(response.content)
        LOG.info("%s 已下载到 %s", local_filename, download_directory)
    else:
        LOG.error("未能获取到摸鱼文件的链接: %s", local_filename)


@log_function_execution
def download_zao_bao_file():
    # 使用当前工作目录
    download_directory = 'zaobao-jpg/'
    os.makedirs(download_directory, exist_ok=True)
    local_filename = f'{get_current_date()}.jpg'
    full_file_path = os.path.join(download_directory, local_filename)

    url = "https://v3.alapi.cn/api/zaobao"
    token = alapi_config.get("token", "")
    if not token:
        LOG.error("ALAPI token 未配置，请在 config.yaml 中设置 alapi.token")
        return
    payload = f"token={token}&format=image"
    headers = {'Content-Type': "application/x-www-form-urlencoded"}
    retry_count = 3
    response = None
    for i in range(retry_count):
        try:
            response = post(url, data=payload, headers=headers, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
            response.raise_for_status()
            break
        except Exception as e:
            LOG.warning("download_zao_bao_file 尝试 %d/%d 失败: %s", i + 1, retry_count, e)
            response = None
            time.sleep(2)
    if response is None or not response.ok or not response.content:
        LOG.error("所有下载尝试均失败，未能获取到早报图片")
        return

    with open(full_file_path, 'wb') as file:
        file.write(response.content)
    LOG.info("%s 已下载到 %s", local_filename, download_directory)


def get_moyu_url_by_wx():
    url = "https://mp.weixin.qq.com/mp/appmsgalbum?action=getalbum&album_id=3743225907507462153"
    response = get(url, timeout=DEFAULT_TIMEOUT, follow_redirects=True)

    if response.status_code != 200:
        LOG.error("摸鱼日历专辑获取失败: status=%s", response.status_code)
        return None

    soup = BeautifulSoup(response.text, 'html.parser')
    current_date = get_current_date()
    for item in soup.find_all('li', class_='album__list-item'):
        title_div = item.find('div', class_='album__item-title')
        title = title_div.text.strip() if title_div else ""
        if f"[摸鱼人日历]{current_date}" in title or f"[摸鱼人日历]{current_date.lstrip('0')}" in title:
            link = item['data-link']
            LOG.info("article link: %s", link)
            result = send_to_jina(link)
            LOG.info("image link: %s", result)
            return result
    LOG.warning("摸鱼日历专辑里没有今天的标题: %s", current_date)
    return None


def send_to_jina(link):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.0.0 Safari/537.36'
    }
    response = get(link, headers=headers, timeout=DEFAULT_TIMEOUT, follow_redirects=True)

    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        found_target = False

        for element in soup.descendants:
            if isinstance(element, str):
                found_target = True
                continue

            if found_target and element.name == 'img' and element.get('data-src') and float(
                    element.get("data-ratio", 0)) >= 1:
                image_url = element['data-src']
                if not image_url.startswith('http'):
                    image_url = 'https:' + image_url
                LOG.info("Found image URL after target text: %s", image_url)
                return image_url

        LOG.error("No suitable image found after target text in the article")
        return None
    else:
        LOG.error("Failed to fetch data from WeChat article. Status code: %s", response.status_code)
        return None


def get_current_date(tzs: str = "Asia/Shanghai"):
    tz = pytz.timezone(tzs)
    current_date_utc8 = datetime.now(tz)
    formatted_date = current_date_utc8.strftime('%m月%d号')
    return formatted_date


def check_image_openable(image_path):
    """图片存在且能打开；只是检查，没有图是正常情况（还没下载），由调用方决定要不要记"""
    try:
        with Image.open(image_path) as img:
            img.verify()
            return True
    except (IOError, SyntaxError):
        return False

