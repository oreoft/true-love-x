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
from pathlib import Path

from true_love_common.hosts import server_host
from true_love_common.http.client import get, post
from true_love_common.media import to_url

from ..services import base_client
from ..services.ai_client.business import fetch_data
from ..core import Config

_config = Config()
alapi_config = _config.ALAPI
LOG = logging.getLogger("JobProcess")

# 摸鱼图、早报图的目录，通过 /media 开放给 base 下载后发送；所有机器人共用，一天只下载一次
MEDIA_DIRS = [Path("moyu-jpg"), Path("zaobao-jpg")]

# 默认网络请求超时时间（秒）
DEFAULT_TIMEOUT = 60


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
    """发 server 自己目录里的图片（path 相对工作目录），base 从 server 的 /media 下载"""
    return asyncio.run(base_client.send_file(bot_id, to_url(path, server_host()), receiver))


def send_daily_notice(bot_id, room_id, content='早上好☀️家人萌~'):
    try:
        ensure_today_images()
    except Exception as e:
        # 图片下载失败也照常推文字
        LOG.error("下载当天图片失败: %s", e)
    # 图片按北京时间的日期命名，和下载时一致
    current_date = get_current_date()
    moyu_file_path = f'moyu-jpg/{current_date}.jpg'
    zao_bao_file_path = f'zaobao-jpg/{current_date}.jpg'

    r_resp = fetch_data("/data/currency", {"currency": "日元"})
    if r_resp and "失败" not in r_resp:
        content += "\n\n今日日元汇率情况：\n" + r_resp

    r_resp2 = fetch_data("/data/currency", {"currency": "美元"})
    if r_resp2 and "失败" not in r_resp2:
        content += "\n\n今日美元汇率情况：\n" + r_resp2

    r_resp3 = fetch_data("/data/gold")
    if r_resp3 and "失败" not in r_resp3:
        content += "\n\n今日黄金汇率情况：\n" + r_resp3

    asyncio.run(base_client.send_text(bot_id, room_id, '', content))
    if check_image_openable(moyu_file_path):
        time.sleep(2)
        moyu_res = _send_img(bot_id, moyu_file_path, room_id)
        LOG.info(f"send_image: {moyu_file_path}, result: {moyu_res}")
    if check_image_openable(zao_bao_file_path):
        time.sleep(2)
        zao_bao_res = _send_img(bot_id, zao_bao_file_path, room_id)
        LOG.info(f"send_image: {zao_bao_file_path}, result: {zao_bao_res}")


def notice_moyu_schedule(bot_id, room_id):
    send_daily_notice(bot_id, room_id)


def notice_usa_moyu_schedule(bot_id, room_id):
    send_daily_notice(bot_id, room_id, "早上好☀️友友们~, \n现在国内太阳已经落下, 多赢阿美莉卡一天")


_download_lock = threading.Lock()


def ensure_today_images():
    """当天的摸鱼图、早报图还没有就先下载；加锁，同时触发的推送只下载一次"""
    with _download_lock:
        current_date = get_current_date()
        if not check_image_openable(f'moyu-jpg/{current_date}.jpg'):
            download_moyu_file()
        if not check_image_openable(f'zaobao-jpg/{current_date}.jpg'):
            download_zao_bao_file()


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
        except Exception as e:
            LOG.error(f"download_moyu_file Failed to fetch data. Retry count:{i}, Error:{e}")
            time.sleep(5)
    if file_url:
        response = get(file_url, timeout=DEFAULT_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
        with open(full_file_path, 'wb') as f:
            f.write(response.content)
        LOG.info(f'{local_filename}已下载到 {download_directory}')
    else:
        LOG.error(f"未能获取到摸鱼文件的链接 {download_directory}")


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
            LOG.error(f"download_zao_bao_file 尝试 {i + 1}/{retry_count} 失败: {e}")
            time.sleep(2)
    if response is None or not response.ok or not response.content:
        LOG.error("所有下载尝试均失败，未能获取到早报图片")
        return

    with open(full_file_path, 'wb') as file:
        file.write(response.content)
    LOG.info(f'{local_filename} 已下载到 {download_directory}')


def get_moyu_url_by_wx():
    url = "https://mp.weixin.qq.com/mp/appmsgalbum?action=getalbum&album_id=3743225907507462153"
    response = get(url, timeout=DEFAULT_TIMEOUT, follow_redirects=True)

    if response.status_code == 200:
        soup = BeautifulSoup(response.text, 'html.parser')
        album_items = soup.find_all('li', class_='album__list-item')

        for item in album_items:
            title = item.find('div', class_='album__item-title').text.strip()
            if f"[摸鱼人日历]{get_current_date()}" in title or f"[摸鱼人日历]{get_current_date().lstrip('0')}" in title:
                link = item['data-link']
                LOG.info(f"article link: {link}")
                result = send_to_jina(link)
                LOG.info(f"result link: {result}")
                return result
    else:
        LOG.error(f"download_moyu_file_by_wx Failed to fetch data. Status code:{response.status_code}")


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
                LOG.info(f"Found image URL after target text: {image_url}")
                return image_url

        LOG.error("No suitable image found after target text in the article")
        return None
    else:
        LOG.error(f"Failed to fetch data from WeChat article. Status code:{response.status_code}")
        return None


def get_current_date(tzs: str = "Asia/Shanghai"):
    tz = pytz.timezone(tzs)
    current_date_utc8 = datetime.now(tz)
    formatted_date = current_date_utc8.strftime('%m月%d号')
    return formatted_date


def check_image_openable(image_path):
    try:
        with Image.open(image_path) as img:
            img.verify()
            LOG.info("Image is openable and appears to be valid.")
            return True
    except (IOError, SyntaxError) as e:
        LOG.error(f"Cannot open image: {e}")
        return False

