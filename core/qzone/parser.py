import datetime
import json
import re
from typing import Any

import bs4
import json5

from astrbot.api import logger

from ..model import Comment, Post
from .constants import (
    QZONE_CODE_UNKNOWN,
    QZONE_MSG_EMPTY_RESPONSE,
    QZONE_MSG_INVALID_RESPONSE,
    QZONE_MSG_JSON_PARSE_ERROR,
    QZONE_MSG_NON_OBJECT_RESPONSE,
)


class QzoneParser:
    @staticmethod
    def _error_payload(message: str) -> dict[str, Any]:
        return {"code": QZONE_CODE_UNKNOWN, "message": message, "data": {}}

    @staticmethod
    def parse_response(text: str, *, debug: bool = False) -> dict[str, Any]:
        if debug:
            logger.debug(f"响应数据: {text}")
        if not text or not text.strip():
            logger.warning("响应内容为空")
            return QzoneParser._error_payload(QZONE_MSG_EMPTY_RESPONSE)

        if m := re.search(
            r"callback\s*\(\s*([^{]*(\{.*\})[^)]*)\s*\)",
            text,
            re.I | re.S,
        ):
            json_str = m.group(2)
        else:
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end < start:
                logger.warning("响应内容缺少 JSON 片段")
                return QzoneParser._error_payload(QZONE_MSG_INVALID_RESPONSE)
            json_str = text[start : end + 1]

        json_str = json_str.replace("undefined", "null").strip()
        try:
            data = json5.loads(json_str)
        except (ValueError, json.JSONDecodeError) as e:
            logger.error(f"JSON 解析错误: {e}")
            return QzoneParser._error_payload(QZONE_MSG_JSON_PARSE_ERROR)

        if not isinstance(data, dict):
            logger.error("JSON 解析结果不是 dict")
            return QzoneParser._error_payload(QZONE_MSG_NON_OBJECT_RESPONSE)

        if debug:
            logger.debug(f"解析后数据: {data}")
        return data

    @staticmethod
    def parse_upload_result(payload: dict[str, Any]) -> tuple[str, str]:
        data = payload["data"]
        picbo = data["url"].split("&bo=", 1)[1]
        richval = ",{},{},{},{},{},{},,{},{}".format(
            data["albumid"],
            data["lloc"],
            data["sloc"],
            data["type"],
            data["height"],
            data["width"],
            data["height"],
            data["width"],
        )
        return picbo, richval

    @staticmethod
    def parse_feeds(msglist: list[dict]) -> list[Post]:
        try:
            posts = []
            for msg in msglist:
                image_urls = []
                for img_data in msg.get("pic", []):
                    for key in ("url2", "url3", "url1", "smallurl"):
                        if raw := img_data.get(key):
                            image_urls.append(raw)
                            break
                for video in msg.get("video") or []:
                    video_image_url = video.get("url1") or video.get("pic_url")
                    image_urls.append(video_image_url)
                video_urls = []
                for video in msg.get("video") or []:
                    url = video.get("url3")
                    if url:
                        video_urls.append(url)
                rt_con = msg.get("rt_con", {}).get("content", "")
                comments = Comment.build_list(msg.get("commentlist") or [])
                post = Post(
                    tid=str(msg.get("tid", 0)),
                    uin=int(msg.get("uin", 0)),
                    name=msg.get("name", ""),
                    gin=0,
                    text=str(msg.get("content", "")).strip(),
                    images=image_urls,
                    videos=video_urls,
                    anon=False,
                    status="approved",
                    create_time=int(msg.get("created_time", 0) or 0),
                    rt_con=rt_con,
                    comments=comments,
                    extra_text=msg.get("source_name"),
                )
                posts.append(post)
            return posts
        except Exception as e:
            logger.error(f"解析说说列表失败: {e}")
            return []

    @staticmethod
    def parse_recent_feeds(data: dict) -> list[Post]:
        feeds: list = data.get("data", {}).get("data", {})
        if not data:
            return []
        try:
            posts = []
            for feed in feeds:
                if not feed:
                    continue
                if str(feed.get("appid", "")) != "311":
                    continue
                uin = feed.get("uin", "")
                tid = feed.get("key", "")
                if not uin or not tid:
                    continue
                create_time = feed.get("abstime", 0)
                nickname = feed.get("nickname", "")
                html_content = feed.get("html", "")
                if not html_content:
                    continue

                soup = bs4.BeautifulSoup(html_content, "html.parser")

                text_div = soup.find("div", class_="f-info")
                text = text_div.get_text(strip=True) if text_div else ""

                rt_con = ""
                txt_box = soup.select_one("div.txt-box")
                if txt_box:
                    rt_con = txt_box.get_text(strip=True)
                    if "：" in rt_con:
                        rt_con = rt_con.split("：", 1)[1].strip()

                image_urls = []
                if img_box := soup.find("div", class_="img-box"):
                    for img in img_box.find_all("img"):
                        src = img.get("src")
                        if src and not str(src).startswith("http://qzonestyle.gtimg.cn"):
                            image_urls.append(src)

                img_tag = soup.select_one("div.video-img img")
                if img_tag and "src" in img_tag.attrs:
                    image_urls.append(img_tag["src"])

                videos = []
                video_div = soup.select_one("div.img-box.f-video-wrap.play")
                if video_div and "url3" in video_div.attrs:
                    videos.append(video_div["url3"])

                comments: list[Comment] = []
                comment_items = soup.select("li.comments-item.bor3")
                if comment_items:
                    for item in comment_items:
                        data_uin = str(item.get("data-uin", ""))
                        comment_tid = str(item.get("data-tid", ""))
                        nick = str(item.get("data-nick", ""))

                        content_div = item.select_one("div.comments-content")
                        if content_div:
                            for op in content_div.select("div.comments-op"):
                                op.decompose()
                            content = content_div.get_text(" ", strip=True).split(":", 1)[-1]
                        else:
                            content = ""

                        comment_time_span = item.select_one("span.state")
                        comment_time = comment_time_span.get_text(strip=True) if comment_time_span else ""

                        parent_tid = None
                        parent_div = item.find_parent("div", class_="mod-comments-sub")
                        if parent_div:
                            parent_li = parent_div.find_parent("li", class_="comments-item")
                            if parent_li:
                                parent_tid = str(parent_li.get("data-tid"))

                        comments.append(
                            Comment(
                                uin=int(data_uin) if data_uin.isdigit() else 0,
                                nickname=nick,
                                content=content,
                                create_time=0,
                                create_time_str=comment_time,
                                tid=int(comment_tid) if comment_tid.isdigit() else 0,
                                parent_tid=int(parent_tid) if parent_tid and parent_tid.isdigit() else None,
                            )
                        )

                post = Post(
                    tid=str(tid),
                    uin=int(uin),
                    name=str(nickname),
                    text=text,
                    images=list(set(image_urls)),
                    videos=videos,
                    create_time=int(create_time) if str(create_time).isdigit() else 0,
                    rt_con=rt_con,
                    comments=comments,
                )
                posts.append(post)
            logger.info(f"成功解析 {len(posts)} 条最新说说")
            return posts
        except Exception as e:
            logger.error(f"解析说说错误：{e}")
            return []
