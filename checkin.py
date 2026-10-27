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
    - mitmdump 自动探测（PATH → 当前解释器的 Scripts → 各 conda/venv 环境的 Scripts
      → 用户目录 %APPDATA%\\Python\\Python3XX\\Scripts → 模块方式启动），
      也可在 config.json 的 mitmdump_path 里写死绝对路径

子命令：
    python checkin.py --no-submit   只抓 ticket 不提交（用于验证抓包）
    python checkin.py --dry-run     只打印步骤，不改代理/不启动 mitmdump
    python checkin.py --doctor      打印环境诊断（Python / mitmdump / 证书 / 代理）
"""
import argparse
import datetime
import glob
import json
import math
import os
import random
import shutil
import subprocess
import sys
import sysconfig
import time
import urllib.parse
import urllib.request
import winreg

try:
    import site
except ImportError:     # pragma: no cover
    site = None

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


def _expand_path(p):
    """展开 %APPDATA% / ~ 之类的写法，并去掉两端引号与空白。"""
    return os.path.expandvars(os.path.expanduser(str(p).strip().strip('"').strip("'")))


def _version_key(d):
    """让 Python312 排在 Python39 前面（用于给多个用户目录排序）。"""
    name = os.path.basename(os.path.dirname(d))
    digits = ''.join(ch for ch in name if ch.isdigit())
    return (len(digits), digits)


def mitmdump_dirs():
    """可能藏着 mitmdump.exe 的目录：当前解释器 → 用户目录 → 其它 Python 安装。

    用户痛点：pip install --user 会把 mitmdump.exe 装到
    %APPDATA%\\Python\\Python3XX\\Scripts（不在 PATH 里），
    而 PATH 上往往是另一个 Python（如 Anaconda），于是自动探测失败。
    """
    dirs = []

    def add(d):
        if not d:
            return
        d = os.path.normpath(d)
        if d not in dirs:
            dirs.append(d)

    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    add(exe_dir)                                # Linux/mac 布局：与 python 同目录
    add(os.path.join(exe_dir, 'Scripts'))       # conda / venv 布局
    add(os.path.join(exe_dir, os.pardir, 'Scripts'))

    for scheme in ('nt', 'venv', 'nt_user', 'posix_user'):
        try:
            add(sysconfig.get_path('scripts', scheme=scheme))
        except (KeyError, AttributeError, TypeError):
            continue

    # conda / venv 的其它环境：base 里没装 mitmproxy、但 envs\WANAN 里装了的情况很常见。
    # checkin.py 自己不 import mitmproxy，用别的环境的 mitmdump.exe 完全没问题。
    env_roots = []
    for p in (os.environ.get('CONDA_PREFIX'), sys.prefix, sys.base_prefix):
        if p and os.path.isdir(p):
            p = os.path.normpath(p)
            if p not in env_roots:
                env_roots.append(p)
    home = os.path.expanduser('~')
    conda_globs = [os.path.join(home, '*conda*')]
    if os.environ.get('ProgramData'):
        conda_globs.append(os.path.join(os.environ['ProgramData'], '*conda*'))
    for pat in conda_globs:
        for p in glob.glob(pat):
            if os.path.isdir(p) and p not in env_roots:
                env_roots.append(p)
    for root in env_roots:
        for sub in ('Scripts', 'bin'):
            for d in sorted(glob.glob(os.path.join(root, 'envs', '*', sub)), reverse=True):
                add(d)

    if site is not None:
        user_base = getattr(site, 'USER_BASE', '') or ''
        if user_base:
            add(os.path.join(user_base, 'Scripts'))

    # %APPDATA%\Python\Python3XX\Scripts（pip install --user）、
    # %LOCALAPPDATA%\Programs\Python\Python3XX\Scripts（python.org 安装器）等
    globs = []
    if site is not None and getattr(site, 'USER_BASE', ''):
        globs.append(os.path.join(site.USER_BASE, 'Python*', 'Scripts'))
    if os.environ.get('LOCALAPPDATA'):
        globs.append(os.path.join(os.environ['LOCALAPPDATA'], 'Programs', 'Python', 'Python*', 'Scripts'))
    if os.environ.get('SystemDrive'):
        globs.append(os.path.join(os.environ['SystemDrive'] + os.sep, 'Python*', 'Scripts'))
    for pat in globs:
        for d in sorted(glob.glob(pat), key=_version_key, reverse=True):
            add(d)

    return dirs


def _runs(cmd):
    """跑一次 `--version`，确认这个候选真能启动（避免路径存在但解释器已删除）。"""
    try:
        r = subprocess.run(cmd + ['--version'], capture_output=True, timeout=30)
    except Exception:
        return False
    return r.returncode == 0


# 模块方式启动：mitmproxy 装好了但 Scripts 目录里没有（或找不到）mitmdump.exe。
# sys.argv[1:] 在 `python -c` 下正好是传给 mitmdump 的参数，新旧版本签名都兼容。
MODULE_RUNNER = ('import sys;'
                 'from mitmproxy.tools.main import mitmdump;'
                 'mitmdump(sys.argv[1:])')


def find_mitmdump(cfg):
    """返回启动 mitmdump 的命令前缀（list），找不到返回 None。"""
    # 1) config.json 里写死的路径，用户说了算，不再校验能否启动
    raw = cfg.get('mitmdump_path')
    if raw and not is_placeholder(raw):
        p = _expand_path(raw)
        if not os.path.isabs(p):
            p = os.path.join(HERE, p)
        if os.path.isfile(p):
            return [p]
        log('config.json 的 mitmdump_path 不存在：%s（继续自动探测）' % p)

    # 2) PATH
    for name in ('mitmdump', 'mitmdump.exe'):
        p = shutil.which(name)
        if p:
            return [p]

    # 3) 各个 Python 的 Scripts 目录（用户目录 --user 安装最常见）
    candidates = []
    for d in mitmdump_dirs():
        for name in ('mitmdump.exe', 'mitmdump'):
            p = os.path.join(d, name)
            if os.path.isfile(p) and p not in candidates:
                candidates.append(p)
    for p in candidates:
        if _runs([p]):
            return [p]

    # 4) 最后兜底：当前解释器能 import mitmproxy 时，用模块方式启动
    runner = [sys.executable, '-c', MODULE_RUNNER]
    if _runs(runner):
        return runner
    return None


def describe_mitmdump(cmd):
    if not cmd:
        return '(未找到)'
    if len(cmd) == 1:
        return cmd[0]
    return '%s（模块方式启动 mitmproxy）' % cmd[0]


MISSING_MITMDUMP_HINT = """未找到 mitmdump（mitmproxy 已安装但脚本探测不到时最常见）。

  脚本已自动探测：PATH → 当前解释器 Scripts → 各 conda/venv 环境的 Scripts
  → %APPDATA%\\Python\\Python3XX\\Scripts → 模块方式启动。仍失败就按下面排查：

  1) 先确认 mitmproxy 到底装在哪：
       python -c "import mitmproxy, sys; print(sys.executable)"
     或看看各环境里有没有 mitmdump.exe：
       conda env list
       dir /s /b %USERPROFILE%\\*conda*\\envs\\*\\Scripts\\mitmdump.exe
  2) 最省事：用你打算跑脚本的那个 Python 再装一遍
       python -m pip install mitmproxy
  3) 也可以直接把找到的 mitmdump.exe 绝对路径写进 config.json：
       "mitmdump_path": "D:\\\\programs\\\\Miniconda\\\\envs\\\\WANAN\\\\Scripts\\\\mitmdump.exe"
     （pip install --user 装的通常在
       %APPDATA%\\Python\\Python3XX\\Scripts\\mitmdump.exe，该目录默认不在 PATH）
  4) 双击 checkin.cmd 时，还可把带 mitmproxy 的 python.exe 写进 python_path.txt。
  5) 完整诊断：python checkin.py --doctor
"""


# ============================ CA 证书 ============================
def ca_cert_path():
    return os.path.join(os.path.expanduser('~'), '.mitmproxy', 'mitmproxy-ca-cert.cer')


def is_cert_trusted():
    try:
        r = subprocess.run(['certutil', '-store', 'Root'],
                           capture_output=True, timeout=20)
        return b'mitmproxy' in (r.stdout or b'').lower()
    except Exception:
        return False


def generate_ca_cert(cfg):
    cmd = cfg.get('mitmdump_cmd')
    if not cmd:
        return False
    cert = ca_cert_path()
    proc = subprocess.Popen(cmd + ['--quiet'], cwd=HERE,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            if os.path.exists(cert):
                return True
            time.sleep(0.5)
        return os.path.exists(cert)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def install_cert(cert):
    ps = ("Start-Process -FilePath 'certutil.exe' "
          "-ArgumentList '-addstore -f Root \"%s\"' -Verb RunAs -Wait" % cert)
    try:
        r = subprocess.run(['powershell', '-NoProfile', '-Command', ps],
                           capture_output=True, timeout=180)
        return r.returncode == 0
    except Exception:
        return False


def ensure_ca_cert(cfg):
    if is_cert_trusted():
        return True
    cert = ca_cert_path()
    log('mitmproxy CA 证书尚未受信任，开始首次配置 ...')
    if not os.path.exists(cert):
        log('生成 mitmproxy CA 证书 ...')
        if not generate_ca_cert(cfg):
            log('生成证书失败，请手动运行一次 mitmdump 后再试。')
            return False
    log('安装证书到「受信任的根证书颁发机构」（即将弹出 UAC 授权框，请点「是」）...')
    install_cert(cert)
    if is_cert_trusted():
        log('CA 证书已受信任。若微信正在运行，请重启微信后再打卡。')
        return True
    log('证书安装失败或已取消，请手动安装：%s' % cert)
    return False


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
    cmd = cfg['mitmdump_cmd'] + ['-s', CAPTURE_ADDON, '-p', str(cfg['proxy_port']), '--quiet']
    proc = subprocess.Popen(cmd, cwd=HERE, stdout=logf, stderr=subprocess.STDOUT)
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
    log('推送「%s」→ %s' % (title, '成功' if ok else note))


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


# ============================ 环境诊断 ============================
def doctor(cfg):
    """打印一份环境诊断，方便远程帮用户排查（不改任何设置）。"""
    print('---- 环境诊断 ----')
    print('Python      : %s' % sys.executable)
    print('版本        : %s' % sys.version.split()[0])
    try:
        import mitmproxy
        has_mitmproxy = True
        print('mitmproxy   : %s（%s）' % (getattr(mitmproxy, '__version__', '?'),
                                         os.path.dirname(mitmproxy.__file__)))
    except Exception as e:
        has_mitmproxy = False
        print('mitmproxy   : 当前解释器无法 import（%s）' % e)
    print('PATH 上的    : %s' % (shutil.which('mitmdump') or '(无 mitmdump)'))
    ppt = os.path.join(HERE, 'python_path.txt')
    if os.path.exists(ppt):
        try:
            with open(ppt, 'r', encoding='utf-8', errors='replace') as f:
                lines = [ln.strip() for ln in f if ln.strip()]
            print('python_path : %s' % (lines[0] if lines else '(空)'))
        except Exception as e:
            print('python_path : 读取失败（%s）' % e)
    else:
        print('python_path : (无 python_path.txt)')
    print('config 路径  : %s' % (cfg.get('mitmdump_path') or '(空)'))
    cmd = find_mitmdump(cfg)
    print('实际使用     : %s' % describe_mitmdump(cmd))
    if not cmd:
        print('探测过的目录 :')
        for d in mitmdump_dirs():
            print('               %s%s' % (d, '' if os.path.isdir(d) else '  (不存在)'))
    print('CA 证书      : %s（%s）' % ('已信任' if is_cert_trusted() else '未信任', ca_cert_path()))
    if not has_mitmproxy:
        print('提示        : 当前解释器没装 mitmproxy。checkin.py 自己不 import mitmproxy，')
        print('              只要能探测到 mitmdump 就能打卡；双击 checkin.cmd 会优先使用')
        print('              python_path.txt 里的解释器。')
    try:
        if os.name == 'nt':
            e, s = read_proxy()
            print('系统代理     : enable=%s server=%s' % (e, s or '(空)'))
    except Exception as e:
        print('系统代理     : 读取失败（%s）' % e)
    print('config.json  : %s' % ('存在' if os.path.exists(CONFIG_PATH) else '缺失'))
    print('------------------')
    return 0 if cmd else 2


# ============================ 主流程 ============================
def main():
    ap = argparse.ArgumentParser(description='晚安成电 小程序 ticket 一键打卡')
    ap.add_argument('--no-submit', action='store_true', help='只抓 ticket，不提交')
    ap.add_argument('--dry-run', action='store_true', help='只打印步骤，不改代理/不启动 mitmdump')
    ap.add_argument('--doctor', action='store_true', help='打印环境诊断（Python/mitmdump/证书/代理）')
    args = ap.parse_args()

    if args.doctor:
        try:
            cfg = load_config()
        except SystemExit:      # config.json 缺失时也能给出诊断
            cfg = {}
        return doctor(cfg)

    cfg = load_config()

    # 端口与 mitmdump 路径（可配置，否则自动探测）
    proxy_port = int(cfg.get('proxy_port') or DEFAULT_PROXY_PORT)
    mitmdump_cmd = find_mitmdump(cfg)
    if not mitmdump_cmd:
        sys.exit(MISSING_MITMDUMP_HINT)
    cfg['mitmdump_cmd'] = mitmdump_cmd
    cfg['proxy_port'] = proxy_port

    if args.dry_run:
        e, s = read_proxy()
        log('dry-run：mitmdump = %s' % describe_mitmdump(mitmdump_cmd))
        log('dry-run：当前代理 enable=%s server=%s；将临时改为 %s:%s' % (e, s, PROXY_HOST, proxy_port))
        log('dry-run：启动 mitmdump → 手动打开小程序 → 抓 ticket → %s → 还原代理'
            % ('提交打卡' if not args.no_submit else '仅抓取'))
        if not is_cert_trusted():
            log('dry-run：CA 证书尚未受信任，正式运行时会自动安装')
        return 0

    log('mitmdump = %s' % describe_mitmdump(mitmdump_cmd))

    if not ensure_ca_cert(cfg):
        log('注意：CA 证书未成功安装，抓取 ticket 可能失败（可手动安装后重试）。')

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
