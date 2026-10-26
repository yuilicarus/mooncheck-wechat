# -*- coding: utf-8 -*-
"""晚安成电 · 微信小程序 ticket 打卡（2026-10 起的新鉴权）。

鉴权链：
    wx.login code → POST /platform-app/api/auth/check-bind      → sessionId
    wx.login code → POST /platform-app/api/auth/checkin-ticket  → ticket（30 分钟、一次性）
    打卡提交：POST /daily-checkin/api/student-checkin-record/checkin
              额外头 X-Checkin-Ticket: <ticket>，会话改用小程序 SESSION。

ticket 只能由微信客户端生成，本模块只负责「消费」capture_wanan.py 落盘的
miniprogram.json（sessionId / ticket / h5_session）。
"""
import json
import os
import time

import wsc_checkin
from wsc_http import HttpClient

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.environ.get('WANAN_OUT') or os.path.join(HERE, 'out')

TICKET_TTL_S = 30 * 60


def load_mp(path=None):
    path = path or os.path.join(OUT_DIR, 'miniprogram.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def is_fresh(mp):
    """ticket 是否仍在 30 分钟有效期内（过期需重新打开小程序刷新）。"""
    if not mp or not mp.get('ticket'):
        return False
    ts = mp.get('captured_at_epoch')
    if not ts:
        return False
    return (time.time() - float(ts)) < TICKET_TTL_S


def new_mp_client(cfg, mp):
    """构造带小程序会话的客户端（直接使用小程序 SESSION，无需门户预热）。"""
    c = HttpClient(user_agent=cfg.get('user_agent') or None)
    # H5 侧会话优先；缺失时退回 check-bind 的 sessionId（其值即 mp-weixin 的 SESSION）
    sid = mp.get('h5_session') or mp.get('session_id')
    if sid:
        c.jar.set('SESSION', sid, 'jzsz.uestc.edu.cn', '/')
    return c


def mp_today(client):
    """只读查询今日状态（小程序会话）。"""
    return client.request('GET', wsc_checkin.EP_TODAY, headers=wsc_checkin.H5_HEADERS)


def parse_today(resp):
    """把 today 响应解析成 (ok, data, note)。"""
    if resp.status in (301, 302):
        return False, None, '会话已失效（HTTP %s 重定向）' % resp.status
    if resp.status != 200:
        return False, None, 'HTTP %s' % resp.status
    try:
        j = resp.json()
    except Exception:
        return False, None, '响应不是 JSON：%s' % resp.text[:120]
    if j.get('code') != 0:
        return False, None, 'code=%s msg=%s' % (j.get('code'), j.get('msg'))
    return True, (j.get('data') or {}), ''
