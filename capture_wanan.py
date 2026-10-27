# -*- coding: utf-8 -*-
"""mitmproxy addon：自动截获晚安成电小程序的 sessionId/ticket 并落盘。

用法（先安装 mitmproxy 并信任其 CA 证书，见下方说明）：
    mitmdump -s capture_wanan.py -p 8080

然后打开微信 PC 小程序进入「晚安成电」打卡页，addon 会把：
    - check-bind 的 sessionId（及学号 sn）
    - checkin-ticket 的 ticket / expiresAt
    - H5 侧 SESSION cookie
写入 out/miniprogram.json，供 checkin.py 一键打卡时消费。

抓包原理：不注入微信进程，只监听 HTTPS 明文（code/ticket 都在报文里）。
"""
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.environ.get('WANAN_OUT') or os.path.join(HERE, 'out')

HOST = 'jzsz.uestc.edu.cn'
CHECK_BIND = '/platform-app/api/auth/check-bind'
CHECKIN_TICKET = '/platform-app/api/auth/checkin-ticket'
H5_PREFIX = '/daily-checkin'  # H5 应用（index.html 与 /api/ 系列）


def _json_of(resp):
    try:
        return resp.json()
    except Exception:
        try:
            return json.loads(resp.get_text(strict=False) or '')
        except Exception:
            return None


def _cookie(resp_or_req, name):
    for h in resp_or_req.headers.get_all('Cookie') or []:
        for part in h.split(';'):
            k, _, v = part.strip().partition('=')
            if k == name and v:
                return v
    return None


class CaptureWanan:
    def __init__(self):
        self.sn = None
        self.session_id = None
        self.h5_session = None
        self.ticket = None
        self.expires_at_ms = None

    def response(self, flow):
        req = flow.request
        if req.pretty_host != HOST:
            return
        path = req.path
        resp = flow.response
        body = _json_of(resp) if resp else None
        if not body:
            return

        if path.startswith(CHECK_BIND) and body.get('code') == 0:
            d = body.get('data') or {}
            self.session_id = d.get('sessionId')
            self.sn = (d.get('user') or {}).get('sn')
            self._flush()
        elif path.startswith(CHECKIN_TICKET) and body.get('code') == 0:
            d = body.get('data') or {}
            self.ticket = d.get('ticket')
            self.expires_at_ms = d.get('expiresAt')
            self._flush()
        elif path.startswith(H5_PREFIX):
            s = _cookie(req, 'SESSION')
            if s and s != self.session_id:
                self.h5_session = s
                self._flush()

    def _flush(self):
        if not (self.session_id or self.ticket or self.h5_session):
            return
        os.makedirs(OUT, exist_ok=True)
        data = {
            'sn': self.sn,
            'session_id': self.session_id,
            'h5_session': self.h5_session,
            'ticket': self.ticket,
            'expires_at_ms': self.expires_at_ms,
            'captured_at_epoch': time.time(),
            'captured_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        }
        tmp = os.path.join(OUT, 'miniprogram.json.tmp')
        dst = os.path.join(OUT, 'miniprogram.json')
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, dst)
        print('[capture_wanan] 已写入 out/miniprogram.json：ticket=%s sn=%s' %
              ('有' if self.ticket else '无', self.sn or '未知'))


addons = [CaptureWanan()]
