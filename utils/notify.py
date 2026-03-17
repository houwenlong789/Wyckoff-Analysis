# -*- coding: utf-8 -*-
"""
统一通知：飞书 + 企微 + 钉钉。按配置的 webhook 分别发送，互不影响。
"""
from __future__ import annotations

import requests


def send_wecom_notification(webhook_url: str, title: str, content: str) -> bool:
    """发送企业微信群机器人 Markdown 消息。URL 为空则返回 False。"""
    if not webhook_url or not webhook_url.strip():
        return False
    url = webhook_url.strip()
    # 企微 markdown 单条最长 4096 字节，过长则截断并注明
    max_len = 4000
    body = f"# {title}\n\n{content}" if title else content
    if len(body.encode("utf-8")) > max_len:
        body = body[: max_len // 2] + "\n\n...(内容过长已截断)"
    payload = {"msgtype": "markdown", "markdown": {"content": body}}
    try:
        resp = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"[wecom] http {resp.status_code}: {resp.text[:200]}")
            return False
        data = resp.json()
        if data.get("errcode") != 0:
            print(f"[wecom] errcode {data.get('errcode')}: {data.get('errmsg', '')}")
            return False
        return True
    except Exception as e:
        print(f"[wecom] exception: {e}")
        return False


def send_dingtalk_notification(webhook_url: str, title: str, content: str) -> bool:
    """发送钉钉自定义机器人 Markdown 消息。URL 为空则返回 False。"""
    if not webhook_url or not webhook_url.strip():
        return False
    url = webhook_url.strip()
    # 钉钉 markdown text 建议不超过 2 万字符，这里按 4000 字节截断
    max_len = 4000
    text = f"# {title}\n\n{content}" if title else content
    if len(text.encode("utf-8")) > max_len:
        text = text[: max_len // 2] + "\n\n...(内容过长已截断)"
    payload = {"msgtype": "markdown", "markdown": {"title": title or "通知", "text": text}}
    try:
        resp = requests.post(url, headers={"Content-Type": "application/json"}, json=payload, timeout=10)
        if resp.status_code != 200:
            print(f"[dingtalk] http {resp.status_code}: {resp.text[:200]}")
            return False
        data = resp.json()
        if data.get("errcode") != 0:
            print(f"[dingtalk] errcode {data.get('errcode')}: {data.get('errmsg', '')}")
            return False
        return True
    except Exception as e:
        print(f"[dingtalk] exception: {e}")
        return False


def send_all_webhooks(
    feishu_url: str,
    wecom_url: str,
    dingtalk_url: str,
    title: str,
    content: str,
) -> None:
    """
    向已配置的飞书、企微、钉钉 webhook 各发一条通知；某个 URL 为空则跳过该渠道。
    飞书使用 utils.feishu.send_feishu_notification（支持分片）；企微/钉钉使用本模块。
    """
    if feishu_url and feishu_url.strip():
        try:
            from utils.feishu import send_feishu_notification
            send_feishu_notification(feishu_url.strip(), title, content)
        except Exception as e:
            print(f"[notify] feishu failed: {e}")
    if wecom_url and wecom_url.strip():
        try:
            send_wecom_notification(wecom_url.strip(), title, content)
        except Exception as e:
            print(f"[notify] wecom failed: {e}")
    if dingtalk_url and dingtalk_url.strip():
        try:
            send_dingtalk_notification(dingtalk_url.strip(), title, content)
        except Exception as e:
            print(f"[notify] dingtalk failed: {e}")
