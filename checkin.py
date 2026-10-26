# -*- coding: utf-8 -*-
"""晚安成电 · 小程序 ticket 一键打卡。

用法（配合微信 PC 手动打开小程序）：
    python checkin.py

流程：
    1. 备份并切换系统代理 -> mitmdump(8080)
    2. 启动 mitmdump（capture_wanan.py 抓取 ticket）
    3. 提示你手动在微信里打开「成电智慧学工」→「晚安成电」打卡页
    4. 轮询 out/miniprogram.json，拿到 30 分钟有效期内的 ticket
    5. 带 X-Checkin-Ticket 提交打卡（坐标来自 config.json）
    6. finally：还原系统代理 + 关闭 mitmdump，并 Server酱 通知结果

依赖与迁移：
    - pip install mitmproxy，并信任 mitmproxy 的 CA 证书（每台电脑首次做一次）
    - 迁移到其它电脑：把本目录拷过去，填好 config.json（学号位置/通知 key）即可
    - mitmdump 路径自动探测，或写在 config.json 的 mitmdump_path 字段

子命令：
    python checkin.py --no-submit   只抓 ticket 不提交（用于验证抓包）
    python checkin.py --dry-run     只打印步骤，不改代理/不启动 mitmdump
"""
import argparse
import datetime
import json
import math
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import winreg

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import wsc_checkin   # noqa: E402
import wsc_mp        # noqa: E402

CONFIG_PATH = os.environ.get('WANAN_CONFIG') or os.path.join(HERE, 'config.json')
OUT_DIR = os.environ.get('WANAN_OUT') or os.path.join(HERE, 'out')
CAPTURE_ADDON = os.path.join(HERE, 'capture_wanan.py')
PROXY_HOST = '127.0.0.1'
DEFAULT_PROXY_PORT = 8080
TICKET_TIMEOUT_S = 300      # 手动打开小程序，给足时间
POLL_INTERVAL_S = 2


# ============================ 基础工具 ============================
def load_config():
    if not os.path.exists(CONFIG_PATH):
        sys.exit('未找到 %s\n请先：copy config.example.json config.json 并填写。' % CONFIG_PATH)
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def is_placeholder(v):
    return (not v) or str(v).startswith('PUT_')


def log(*a):
    print('[%s] %s' % (datetime.datetime.now().strftime('%H:%M:%S'), ' '.join(str(x) for x in a)))


def find_mitmdump(cfg):
    p = cfg.get('mitmdump_path')
    if p and os.path.exists(p):
        return p
    for name in ('mitmdump', 'mitmdump.exe'):
        p = shutil.which(name)
        if p:
            return p
    # 与当前解释器同目录的 Scripts（conda/venv 常见布局，Windows）
    scripts = os.path.join(os.path.dirname(sys.executable), 'Scripts')
    for name in ('mitmdump.exe', 'mitmdump'):
        p = os.path.join(scripts, name)
        if os.path.exists(p):
            return p
    return None


# ============================ 系统代理 ============================
REG_KEY = r'Software\Microsoft\Windows\CurrentVersion\Internet Settings'


def read_proxy():
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY) as k:
        try:
            enable, _ = winreg.QueryValueEx(k, 'ProxyEnable')
        except FileNotFoundError:
            enable = 0
        try:
            server, _ = winreg.QueryValueEx(k, 'ProxyServer')
        except FileNotFoundError:
            server = ''
    return int(enable), (server or '')


def write_proxy(enable, server):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, 'ProxyEnable', 0, winreg.REG_DWORD, int(enable))
        if server:
            winreg.SetValueEx(k, 'ProxyServer', 0, winreg.REG_SZ, server)
        else:
            try:
                winreg.DeleteValue(k, 'ProxyServer')
            except FileNotFoundError:
                pass
    _refresh_wininet()


def _refresh_wininet():
    wininet = __import__('ctypes').windll.wininet
    wininet.InternetSetOptionW(0, 39, 0, 0)   # INTERNET_OPTION_SETTINGS_CHANGED
    wininet.InternetSetOptionW(0, 37, 0, 0)   # INTERNET_OPTION_REFRESH


# ============================ mitmdump ============================
def start_mitm(cfg):
    os.makedirs(OUT_DIR, exist_ok=True)
    logf = open(os.path.join(OUT_DIR, 'mitmdump.log'), 'ab')
    proc = subprocess.Popen(
        [cfg['mitmdump'], '-s', CAPTURE_ADDON, '-p', str(cfg['proxy_port']), '--quiet'],
        cwd=HERE, stdout=logf, stderr=subprocess.STDOUT,
    )
    return proc, logf


def stop_mitm(proc, logf):
    if proc and proc.poll() is None:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass
    if logf:
        try:
            logf.close()
        except Exception:
            pass


# ============================ 抓 ticket ============================
def wait_for_ticket(start_epoch, timeout_s):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        mp = wsc_mp.load_mp()
        if mp and mp.get('ticket'):
            ts = mp.get('captured_at_epoch') or 0
            if ts >= start_epoch and wsc_mp.is_fresh(mp):
                return mp
        time.sleep(POLL_INTERVAL_S)
    return None


# ============================ Server酱 ============================
def notify_serverchan(sendkey, title, body):
    if is_placeholder(sendkey):
        return False, 'sendkey 未配置'
    url = 'https://sctapi.ftqq.com/%s.send' % sendkey
    data = urllib.parse.urlencode({'title': title, 'desp': body}).encode('utf-8')
    req = urllib.request.Request(url, data=data, method='POST')
    req.add_header('Content-Type', 'application/x-www-form-urlencoded')
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode('utf-8', 'replace')
    except Exception as e:
        return False, '推送异常: %s' % e
    try:
        j = json.loads(raw)
        code = j.get('code') if isinstance(j.get('code'), int) else j.get('errcode')
        if isinstance(code, int) and code != 0 and not (200 <= code < 300):
            return False, '推送被拒 code=%s' % code
    except Exception:
        pass
    return True, raw[:120]


def push(cfg, title, body):
    sendkey = cfg.get('notify_sendkey')
    if is_placeholder(sendkey):
        log('（未配置 notify_sendkey，跳过推送）')
        return
    label = cfg.get('label')
    if label:
        title = '[%s] %s' % (label, title)
    ok, note = notify_serverchan(sendkey, title, body)
    log('notify %s -> %s' % (title, 'ok' if ok else note))


# ============================ 打卡 ============================
def jitter_location(loc, radius_m=35.0):
    if radius_m <= 0:
        return loc
    lat = float(loc['latitude'])
    lng = float(loc['longitude'])
    theta = random.uniform(0, 2 * math.pi)
    dist = random.uniform(0, radius_m)
    m_per_deg_lng = 111320.0 * math.cos(math.radians(lat))
    out = dict(loc)
    out['latitude'] = lat + dist * math.cos(theta) / 111320.0
    out['longitude'] = lng + dist * math.sin(theta) / m_per_deg_lng
    return out


def do_submit(cfg, mp):
    """带 ticket 提交打卡，返回 (exit_code, title, body)。"""
    client = wsc_mp.new_mp_client(cfg, mp)
    ok, data, note = wsc_mp.parse_today(wsc_mp.mp_today(client))
    if not ok:
        return 2, '打卡失败', '查询今日状态失败：%s' % note
    status = data.get('recordStatusCode')
    log('今日状态：%s' % status)
    if status == 'CHECKED_IN':
        return 0, '打卡', '今天已打卡（%s），跳过' % data.get('checkinTime')

    loc = dict(cfg.get('location') or {})
    if not loc:
        return 2, '打卡失败', 'config.json 缺少 location'
    loc = jitter_location(loc, float(cfg.get('loc_jitter_m', 35)))
    payload = wsc_checkin.build_payload(loc, client.ua, reverse_geocode=loc.get('reverse_geocode', ''))

    r = wsc_checkin.submit(client, payload, ticket=mp['ticket'])
    if r.status != 200:
        return 2, '打卡失败', 'HTTP %s：%s' % (r.status, r.text[:120])
    try:
        j = r.json()
    except Exception:
        return 2, '打卡失败', '响应不是 JSON：%s' % r.text[:120]
    log('提交响应：code=%s msg=%s' % (j.get('code'), j.get('msg')))
    if j.get('code') != 0:
        return 2, '打卡失败', 'code=%s msg=%s' % (j.get('code'), j.get('msg'))

    ok, data, note = wsc_mp.parse_today(wsc_mp.mp_today(client))
    if not ok:
        return 1, '打卡待确认', '复查失败：%s' % note
    summary = wsc_checkin.safe_summary(data)
    if summary.get('recordStatusCode') == 'CHECKED_IN':
        if summary.get('abnormalFlag'):
            return 1, '打卡异常', 'abnormalFlag=true，distance=%s' % summary.get('distanceMeter')
        return 0, '打卡成功', 'checkinTime=%s，distance=%s m' % (
            summary.get('checkinTime'), summary.get('distanceMeter'))
    return 1, '打卡待确认', '接口成功但复查仍为 %s' % summary.get('recordStatusCode')


# ============================ 主流程 ============================
def main():
    ap = argparse.ArgumentParser(description='晚安成电 小程序 ticket 一键打卡')
    ap.add_argument('--no-submit', action='store_true', help='只抓 ticket，不提交')
    ap.add_argument('--dry-run', action='store_true', help='只打印步骤，不改代理/不启动 mitmdump')
    args = ap.parse_args()

    cfg = load_config()

    # 端口与 mitmdump 路径（可配置，否则自动探测）
    proxy_port = int(cfg.get('proxy_port') or DEFAULT_PROXY_PORT)
    mitmdump = find_mitmdump(cfg)
    if not mitmdump:
        sys.exit('未找到 mitmdump。请 pip install mitmproxy，或在 config.json 填 mitmdump_path。')
    cfg['mitmdump'] = mitmdump
    cfg['proxy_port'] = proxy_port

    if args.dry_run:
        e, s = read_proxy()
        log('dry-run：当前代理 enable=%s server=%s；将临时改为 %s:%s' % (e, s, PROXY_HOST, proxy_port))
        log('dry-run：启动 mitmdump → 手动打开小程序 → 抓 ticket → %s → 还原代理'
            % ('提交打卡' if not args.no_submit else '仅抓取'))
        return 0

    start_epoch = time.time()
    orig_proxy = read_proxy()
    mitm_proc = None
    logf = None
    try:
        log('切换系统代理 -> %s:%s' % (PROXY_HOST, proxy_port))
        write_proxy(1, '%s:%s' % (PROXY_HOST, proxy_port))

        log('启动 mitmdump ...')
        mitm_proc, logf = start_mitm(cfg)
        time.sleep(2.0)

        print()
        print('=' * 62)
        print('  请现在在微信里打开「成电智慧学工」小程序，')
        print('  进入「晚安成电」打卡页（若已在页内，请退出后重新进入）。')
        print('  脚本将在 %d 秒内自动捕获 ticket 并继续。' % TICKET_TIMEOUT_S)
        print('=' * 62)
        print()

        mp = wait_for_ticket(start_epoch, TICKET_TIMEOUT_S)
        if not mp:
            title = '需重新登录/授权'
            body = '未捕获到有效 ticket。\n'
            body += '请确认：1) 微信已登录；2) 小程序「成电智慧学工」能正常进入「晚安成电」；\n'
            body += '3) 系统代理指向 mitmdump、CA 证书已信任。确认后重跑 checkin.py。'
            push(cfg, title, body)
            log('未捕获到 ticket')
            return 2
        log('已捕获 ticket（sn=%s）' % mp.get('sn'))

        if args.no_submit:
            push(cfg, '抓取成功', 'ticket 已就绪（未提交）')
            return 0

        code, title, body = do_submit(cfg, mp)
        push(cfg, title, body)
        return code
    finally:
        log('还原系统代理 -> enable=%s server=%s' % orig_proxy)
        try:
            write_proxy(orig_proxy[0], orig_proxy[1])
        except Exception as e:
            log('还原代理失败（请手动检查系统代理）：%s' % e)
        stop_mitm(mitm_proc, logf)


if __name__ == '__main__':
    sys.exit(main())
