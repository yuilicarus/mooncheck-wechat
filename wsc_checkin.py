# -*- coding: utf-8 -*-
"""晚安成电打卡接口封装（微信小程序 ticket 方案）。

只含小程序打卡所需的最小接口：端点、请求头、载荷字段、安全字段提取。
端点和载荷字段来自实测抓包（POST /daily-checkin/api/student-checkin-record/checkin）。
"""
import datetime

BASE = 'https://jzsz.uestc.edu.cn'
APP_ID = 294

H5_URL = '%s/daily-checkin-h5/index.html?appId=%d' % (BASE, APP_ID)

EP_TODAY = BASE + '/daily-checkin/api/student-checkin-record/today'
EP_CHECKIN = BASE + '/daily-checkin/api/student-checkin-record/checkin'

# 微应用接口统一要求的头（缺 Platform/Referer 可能被拒）
H5_HEADERS = {
    'Platform': 'h5',
    'Accept': '*/*',
    'Origin': BASE,
    'Referer': H5_URL,
    'Sec-Fetch-Site': 'same-origin',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Dest': 'empty',
}

# 小程序新鉴权：打卡提交需额外带一次性票据头（checkin-ticket 接口下发，30 分钟有效）
TICKET_HEADER = 'X-Checkin-Ticket'

# 只允许打印的安全字段（响应体含姓名/手机号/辅导员联系方式，禁止整包输出）
SAFE_FIELDS = (
    'businessDate', 'recordStatusCode', 'checkinTime', 'distanceMeter', 'abnormalFlag',
    'checkinLocationName', 'checkinLocationLatitude', 'checkinLocationLongitude',
    'checkinRadiusMeter',
)


def build_payload(loc, user_agent, now=None, reverse_geocode=None):
    """按实测成功载荷逐字段构造（不做字段裁剪：裁剪有产生异常记录的风险）。"""
    now = now or datetime.datetime.now()
    return {
        'latitude': loc['latitude'],
        'longitude': loc['longitude'],
        'reverseGeocode': (loc.get('reverse_geocode', '') if reverse_geocode is None
                           else reverse_geocode),
        'clientTime': now.strftime('%Y-%m-%d %H:%M:%S'),
        'userAgent': user_agent,
        'coordinateSystemCode': loc.get('coordinate_system', 'gcj02'),
        'accuracy': loc.get('accuracy', 86),
        'locationDurationMs': loc.get('location_duration_ms', 1189),
        'networkTypeCode': loc.get('network_type', '4g'),
        'locationEnabled': False,
        'locationAuthorized': '',
        'locationAccuracy': '',
        'errorMessage': '',
    }


def submit(client, payload, ticket=None):
    """提交打卡。ticket 为小程序一次性票据，有则加 X-Checkin-Ticket 头。"""
    headers = dict(H5_HEADERS)
    if ticket:
        headers[TICKET_HEADER] = ticket
    return client.request('POST', EP_CHECKIN, headers=headers, json_body=payload)


def safe_summary(data):
    if not isinstance(data, dict):
        return {}
    return dict((k, data.get(k)) for k in SAFE_FIELDS)
