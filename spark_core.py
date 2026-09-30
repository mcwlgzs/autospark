"""面板的纯逻辑层：不碰浏览器、不碰网络、不碰 FastAPI。

为什么单独拆一个模块：
  1. backend.py 已经 3500+ 行，混着 XPATH 常量、Selenium 操作、路由和业务规则，
     其中「发不发、什么时候发、要不要重发」这类规则最容易出错，却最难测；
  2. 这里的东西全部是 `输入 -> 输出` 的纯函数（或只依赖标准库的小类），
     可以在任何装了 Python 的机器上直接跑单元测试，不需要 Chrome、不需要 pip 装依赖；
  3. 定时/补跑/去重/文案这几块是「火花会不会断」的决策核心，出错代价最高，
     所以宁可多写一层，也要让它们可测。

依赖约定：只允许 import 标准库。任何 `requests` / `selenium` / `fastapi` 的调用
都必须留在 backend.py 里。
"""

from __future__ import annotations

import hashlib
import hmac
import random
import re
import secrets
import threading
import time
from datetime import date as _date
from datetime import datetime, timedelta

# ==================== 时间 ====================

DEFAULT_SEND_TIME = '22:00'
_WEEKDAYS = ('周一', '周二', '周三', '周四', '周五', '周六', '周日')


def format_time(time_str) -> str:
    """把用户输入的时间统一成 HH:MM。

    兼容 "9:23" / "9：23" / "09:23" 这类写法；无法解析时回落到默认时间。
    """
    if not time_str:
        return DEFAULT_SEND_TIME
    text = str(time_str).replace('：', ':').strip()
    parts = text.split(':')
    if len(parts) != 2:
        return DEFAULT_SEND_TIME
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError:
        return DEFAULT_SEND_TIME
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return DEFAULT_SEND_TIME
    return '%02d:%02d' % (hour, minute)


def parse_hhmm(value) -> tuple:
    """HH:MM -> (时, 分)，解析失败用默认时间。"""
    hour_text, minute_text = format_time(value).split(':')
    return int(hour_text), int(minute_text)


def today_str(now=None) -> str:
    return (now or datetime.now()).strftime('%Y-%m-%d')


def jitter_seconds(task_id, day, window_minutes) -> int:
    """给任务在「当天」算一个固定的随机偏移（秒）。

    关键点：同一天同一个任务必须算出同一个偏移。
    否则每轮 tick 都重算，任务会被无限推迟（永远差一点到点）。
    用 (任务标识, 日期) 做种子，所以「当天固定、跨天不同」。
    """
    if window_minutes is None or window_minutes <= 0:
        return 0
    seed = '%s|%s' % (task_id, day)
    digest = hashlib.sha256(seed.encode('utf-8')).hexdigest()
    return int(digest[:8], 16) % (int(window_minutes) * 60)


def planned_run_at(base_time, day, task_id=None, window_minutes=0, now=None) -> datetime:
    """算出某个任务在某一天真正应该触发的时刻 = 基准时间 + 当天随机偏移。

    例：基准 21:00、窗口 40 分钟 -> 实际落在 21:00~21:40 之间的某一分钟。
    固定时刻太规律是风控特征之一，所以默认留一个随机窗口。
    """
    hour, minute = parse_hhmm(base_time)
    if isinstance(day, str):
        day = datetime.strptime(day, '%Y-%m-%d').date()
    base = datetime(day.year, day.month, day.day, hour, minute)
    return base + timedelta(seconds=jitter_seconds(task_id, day, window_minutes))


def run_due(planned_at, now, last_run_date, grace_minutes) -> bool:
    """到点该不该跑（含「错过了要补跑」）。

    规则：
      * 还没到当天计划时间 -> 不跑；
      * 已经过了计划时间，但还在宽限窗口内，且今天没跑过 -> 跑（补跑，
        对应「机器重启/崩溃导致那一分钟被跳过」的场景）；
      * 今天已经跑过 -> 不跑（一天只发一次，绝不重复打扰对方）；
      * 超过宽限窗口（比如计划时间在 12 小时前）-> 不跑，等明天，
        避免半夜突然补发一条。
    """
    if now < planned_at:
        return False
    if last_run_date == planned_at.strftime('%Y-%m-%d'):
        return False
    if grace_minutes is not None and grace_minutes >= 0:
        if now - planned_at > timedelta(minutes=grace_minutes):
            return False
    return True


def retry_due(retry_at, now, done) -> bool:
    """当日补发队列到点没有。"""
    if done or not retry_at:
        return False
    return now >= retry_at


# ==================== 文案池 ====================

# 一行一条文案；支持占位符 {date} {weekday} {month} {day}
_PLACEHOLDERS = ('{date}', '{weekday}', '{month}', '{day}')


def parse_message_pool(raw) -> list:
    """把用户填的文案解析成候选列表（空行忽略）。

    支持一行一条：每天从里面随机挑一条，避免天天给同一个人发一模一样的话
    （同一句话重复出现本身就是机器特征）。
    """
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)):
        candidates = [str(item) for item in raw]
    else:
        candidates = str(raw).replace('\r\n', '\n').replace('\r', '\n').split('\n')
    return [item.strip() for item in candidates if item and item.strip()]


def render_message(raw, now=None, chooser=None) -> str:
    """从文案池里挑一条并填占位符。池子为空时返回空串（由调用方决定兜底文案）。"""
    pool = parse_message_pool(raw)
    if not pool:
        return ''
    pick = chooser or random.choice
    text = pick(pool)
    moment = now or datetime.now()
    replacements = {
        '{date}': moment.strftime('%Y-%m-%d'),
        '{weekday}': _WEEKDAYS[moment.weekday()],
        '{month}': str(moment.month),
        '{day}': str(moment.day),
    }
    for token, value in replacements.items():
        if token in text:
            text = text.replace(token, value)
    return text


def is_placeholder_only(raw) -> bool:
    """文案是否只由占位符组成（这种模板每天都会变，去重时按好友算而不是按文本算）。"""
    pool = parse_message_pool(raw)
    if not pool:
        return False
    return all(any(token in item for token in _PLACEHOLDERS) for item in pool)


# ==================== 发送记账 / 防重复 ====================

SEND_COOLDOWN_SECONDS = 90
HISTORY_KEEP_DAYS = 7


def history_key(day, name) -> str:
    """记账键：**日期 + 好友**。

    旧实现把文案的哈希也拼进键里，于是「改一下文案」或者「每天随机文案」
    就会绕过当天的去重，同一天给同一个人发两次。键只看日期和好友，
    文案作为记录内容保存，这样才符合「当天已经发过就别再发」的本意。
    """
    return '%s|%s' % (day, name or '')


def history_clean(history, today, keep_days=HISTORY_KEEP_DAYS) -> dict:
    """丢掉超过保留期的记录，避免状态文件无限膨胀。"""
    if not history:
        return {}
    if isinstance(today, datetime):
        today = today.strftime('%Y-%m-%d')
    cutoff = (datetime.strptime(today, '%Y-%m-%d') - timedelta(days=keep_days)).strftime('%Y-%m-%d')
    kept = {}
    for key, value in history.items():
        day = str(key).split('|', 1)[0]
        if day >= cutoff:
            kept[key] = value
    return kept


def make_history_record(status, at=None, text=None, detail=None, kind=None) -> dict:
    """一条发送记账。

    kind 记录「这条是谁发出去的」：定时 / 补跑 / 当日补发 / 人工重发 / 手动发送 /
    试发。以前不存这个字段，面板上就只剩时间和结果，看不出是定时发的还是自己手点
    的那一下 —— 而这两件事在排查（尤其是「怎么又发了一条」）时差别很大。
    旧记录没有 kind 是正常的，读取端按空值处理即可。
    """
    record = {'status': status, 'at': at or datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
    if kind:
        record['kind'] = str(kind)[:32]
    if text:
        record['text'] = str(text)[:200]
    if detail:
        record['detail'] = str(detail)[:200]
    return record


def guard_decision(record, scope, now=None, cooldown_seconds=SEND_COOLDOWN_SECONDS):
    """发送前的重复检查，返回阻止原因（None = 放行）。

    scope='task'   定时/补发：当天已经发过（含结果不确定）就跳过 —— 绝不重复打扰对方；
    scope='manual' 手动发送：只做短冷却，防手滑连点；
    status='failed' 一律放行，这样「当日补发」才能真的补上失败的那条。
    """
    if not record:
        return None
    status = record.get('status')
    when = record.get('at', '')
    if scope == 'manual':
        moment = now or datetime.now()
        try:
            elapsed = (moment - datetime.strptime(when, '%Y-%m-%d %H:%M:%S')).total_seconds()
        except (ValueError, TypeError):
            return None
        if elapsed < cooldown_seconds:
            return '刚刚已经发过同样的内容（%s），%d 秒内不重复发送' % (when, cooldown_seconds)
        return None
    if status == 'success':
        return '今天已经发送过（%s），已跳过' % when
    if status == 'unknown':
        return ('今天发送的这条结果未确认（%s），为避免重复发送已跳过；'
                '请先到抖音确认是否已送达' % when)
    return None


STATUS_SUCCESS = 'success'
STATUS_FAILED = 'failed'
STATUS_UNKNOWN = 'unknown'

# 「没能确认是否送达」的标志。判定发送结果属于"确定失败"还是"结果不确定"时，
# 靠这个常量而不是散落各处的字符串字面量 —— 历史缺陷正是这么来的：
# 调用方写的是 `'未确认' in reason`，而实际文案是「未能确认这条消息是否送达」，
# 于是"结果不确定"被记成了 failed，当天就会去重发一条可能已经送达的消息。
UNCONFIRMED_MARKERS = ('未确认', '未能确认', '无法确认')


def send_status_from_reason(reason) -> str:
    """把发送失败的原因文案归类成记账用的状态。

    为什么要单独一个函数：success / failed / unknown 的区别直接决定
    「当天要不要再补发一条」，而这两种错误的代价是**不对称**的 ——
    把"结果不确定"误记成 failed，就会给对方发第二条（打扰真人，也可能是风控特征）；
    误记成 unknown 只是当天少发一条（火花有风险，但不会重复打扰）。
    所以这里把规则收在一处，并用 UNCONFIRMED_MARKERS 明确列出所有"不确定"的说法，
    而不是让每个调用点各自 `in` 一个字符串。
    """
    text = str(reason or '')
    for marker in UNCONFIRMED_MARKERS:
        if marker in text:
            return STATUS_UNKNOWN
    return STATUS_FAILED


# ==================== 错误分级与重试 ====================

ERROR_TRANSIENT = 'transient'
ERROR_PERMANENT = 'permanent'
ERROR_AUTH = 'authentication'
ERROR_RATE_LIMIT = 'rate_limit'

# 分类 -> (最多重试次数, 起始等待秒数, 退避倍数)
RETRY_BACKOFF = {
    ERROR_TRANSIENT: (3, 3.0, 1.5),
    ERROR_PERMANENT: (0, 0.0, 1.0),
    ERROR_AUTH: (0, 0.0, 1.0),
    ERROR_RATE_LIMIT: (1, 30.0, 1.0),
}
PERMANENT_HINTS = ('会话列表里没有', '好友名为空', '消息内容为空', '没有定时任务', '不存在')
RISK_HINTS = ('风控', '安全验证', '完成验证', '验证身份', '操作频繁', '频繁')
AUTH_HINTS = ('登录状态已失效', '重新登录', '未登录', '密码错误')
TRANSIENT_HINTS = ('超时', '没有找到', '没找到', '结构', '渲染', 'timeout', '暂时')


def classify_error(message) -> str:
    """把失败文案归类，决定要不要重试、退避多久。"""
    text = str(message or '')
    for hint in PERMANENT_HINTS:
        if hint in text:
            return ERROR_PERMANENT
    for hint in RISK_HINTS:
        if hint in text:
            return ERROR_RATE_LIMIT
    for hint in AUTH_HINTS:
        if hint in text:
            return ERROR_AUTH
    for hint in TRANSIENT_HINTS:
        if hint in text:
            return ERROR_TRANSIENT
    return ERROR_TRANSIENT


def retry_delay(category, attempt):
    """第 attempt 次重试前应该等多久（秒）；返回 None 表示不该再重试。"""
    max_retries, base, multiplier = RETRY_BACKOFF.get(category, RETRY_BACKOFF[ERROR_TRANSIENT])
    if attempt >= max_retries:
        return None
    return base * (multiplier ** attempt)


def should_stop_round(message) -> bool:
    """是否应该立刻停止本轮全部发送（认证失效 / 风控）。"""
    return classify_error(message) in (ERROR_AUTH, ERROR_RATE_LIMIT)


def retryable(message) -> bool:
    """这次失败值不值得进「当日补发」队列。

    只有临时性故障才补发：结果不确定（可能已送达）和永久性错误都不补，
    否则等于给同一个人发两遍。
    """
    return classify_error(message) == ERROR_TRANSIENT


# ==================== 密码 ====================

PBKDF2_ITERATIONS = 200_000
PBKDF2_PREFIX = 'pbkdf2_sha256'


def hash_password(password, salt=None, iterations=PBKDF2_ITERATIONS) -> str:
    """PBKDF2-HMAC-SHA256 加盐哈希，格式：pbkdf2_sha256$迭代次数$盐$摘要。

    旧实现是无盐 SHA-256，state.json 一旦泄露可以用彩虹表秒破。
    """
    if password is None:
        password = ''
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', str(password).encode('utf-8'), salt.encode('utf-8'), int(iterations))
    return '%s$%d$%s$%s' % (PBKDF2_PREFIX, int(iterations), salt, digest.hex())


def _legacy_sha256(password) -> str:
    return hashlib.sha256(str(password or '').encode('utf-8')).hexdigest()


def verify_password(password, stored) -> tuple:
    """校验密码，返回 (是否正确, 是否需要重新哈希升级)。

    兼容历史遗留的无盐 SHA-256：老用户登录成功一次就顺手升级成 PBKDF2，
    不需要手动改密码，也不会把任何人锁在门外。
    """
    if not stored:
        return False, False
    text = str(stored)
    if text.startswith(PBKDF2_PREFIX + '$'):
        parts = text.split('$')
        if len(parts) != 4:
            return False, False
        _, iterations, salt, digest = parts
        try:
            rounds = int(iterations)
        except (ValueError, TypeError, OverflowError):
            return False, False
        # 迭代次数还要卡在合理区间：state.json 被改坏时可能写入天文数字，
        # 那会让 pbkdf2 抛 OverflowError（"Python int too large to convert to C long"）
        # 或把登录接口卡死几十分钟 —— 两种都应该老实回「密码错误」。
        if not (1 <= rounds <= 10_000_000):
            return False, False
        try:
            candidate = hashlib.pbkdf2_hmac(
                'sha256', str(password or '').encode('utf-8'), salt.encode('utf-8'), rounds).hex()
        except (ValueError, TypeError, OverflowError, MemoryError):
            return False, False
        # 一律用 bytes 比较：hmac.compare_digest 在 str 上遇到非 ASCII 会抛 TypeError，
        # 而 state.json 被手工改坏 / 迁移写错时完全可能出现中文或乱码 ——
        # 那时登录接口应该老老实实回「密码错误」，而不是 500。
        ok = hmac.compare_digest(candidate.encode('utf-8'), digest.encode('utf-8'))
        needs_upgrade = ok and rounds < PBKDF2_ITERATIONS
        return ok, needs_upgrade
    # 旧格式（无盐 SHA-256）：无论对错都要求升级（同样按 bytes 比较，理由同上）
    ok = hmac.compare_digest(_legacy_sha256(password).encode('utf-8'), text.encode('utf-8'))
    return ok, ok


MIN_PASSWORD_LENGTH = 8


def password_policy_error(password) -> str:
    """新密码最小强度校验，通过返回 None。"""
    if password is None or not str(password).strip():
        return '新密码不能为空'
    text = str(password)
    if len(text) < MIN_PASSWORD_LENGTH:
        return '新密码至少 %d 位' % MIN_PASSWORD_LENGTH
    if text.isdigit():
        return '新密码不能是纯数字'
    return None


# ==================== 面板会话 token ====================

TOKEN_TTL_HOURS = 72


class TokenBook:
    """面板登录 token 表：带签发时间、空闲过期、可一键全部失效。

    旧实现只是一个内存 set：token 永不过期，改密码也不会踢掉别人，
    只增不减。这里做到：
      * 空闲超过 TTL 自动失效（一直在用的面板不会掉线，弃用的会过期）；
      * 改密码 / 退出登录时可以 revoke_all()，把其它浏览器一起踢下线；
      * prune() 清理过期条目，避免长期运行内存缓慢增长。
    """

    def __init__(self, ttl_hours=TOKEN_TTL_HOURS):
        self.ttl_seconds = float(ttl_hours) * 3600
        self._issued = {}
        self._lock = threading.Lock()

    def issue(self, now=None) -> str:
        token = secrets.token_hex(32)
        with self._lock:
            self._issued[token] = float(now if now is not None else time.time())
        return token

    def verify(self, token, now=None, refresh=True) -> bool:
        if not token:
            return False
        moment = float(now if now is not None else time.time())
        with self._lock:
            issued = self._issued.get(token)
            if issued is None:
                return False
            if self.ttl_seconds > 0 and moment - issued > self.ttl_seconds:
                self._issued.pop(token, None)
                return False
            if refresh:
                self._issued[token] = moment
            return True

    def revoke(self, token) -> None:
        with self._lock:
            self._issued.pop(token, None)

    def revoke_all(self) -> int:
        with self._lock:
            count = len(self._issued)
            self._issued.clear()
            return count

    def prune(self, now=None) -> int:
        if self.ttl_seconds <= 0:
            return 0
        moment = float(now if now is not None else time.time())
        with self._lock:
            expired = [token for token, issued in self._issued.items() if moment - issued > self.ttl_seconds]
            for token in expired:
                self._issued.pop(token, None)
            return len(expired)

    def __len__(self) -> int:
        with self._lock:
            return len(self._issued)


# ==================== 每日计划（给面板展示用） ====================

def describe_plan(base_time, day, task_id, window_minutes) -> str:
    """把「今天实际会在几点几分发」渲染成人看的字符串。"""
    planned = planned_run_at(base_time, day, task_id, window_minutes)
    if window_minutes and window_minutes > 0:
        return '%s（含 %d 分钟随机窗口）' % (planned.strftime('%H:%M'), window_minutes)
    return planned.strftime('%H:%M')


def next_planned_run(base_time, now, task_id=None, window_minutes=0) -> datetime:
    """今天还没到点就是今天，已经过了就是明天。"""
    planned = planned_run_at(base_time, now.date(), task_id, window_minutes)
    if planned <= now:
        planned = planned_run_at(base_time, now.date() + timedelta(days=1), task_id, window_minutes)
    return planned


def as_date(value):
    """把 'YYYY-MM-DD' / date / datetime 统一成 date。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, _date):
        return value
    try:
        return datetime.strptime(str(value), '%Y-%m-%d').date()
    except ValueError:
        return None


# ==================== 图片消息 / 文昌帝君灵签 ====================
# 为什么这些判断放在纯逻辑层：图片是「第三方接口给的直链」下载下来的，然后直接交给浏览器
# 上传到聊天框 —— 「下到的东西到底是不是图片」这一步判错，轻则白跑一次发送，
# 重则把任意文件塞进聊天输入框。而魔数、体积上限、后缀这些东西的判断完全不依赖
# 网络和浏览器，放这里就能脱离环境直接单测（backend.py 那边只负责发请求和落盘）。
DEFAULT_IMAGE_MAX_BYTES = 5 * 1024 * 1024

# (文件头, 后缀, MIME)。GIF 只看前 6 字节，不解析后面的版本号内容。
_IMAGE_MAGIC = (
    (b'\xff\xd8\xff', '.jpg', 'image/jpeg'),
    (b'\x89PNG\r\n\x1a\n', '.png', 'image/png'),
    (b'GIF87a', '.gif', 'image/gif'),
    (b'GIF89a', '.gif', 'image/gif'),
)


def image_kind(data) -> tuple:
    """按文件头判断图片类型，返回 (后缀, MIME)；不是已知图片返回 (None, None)。

    只认魔数、不认后缀名：第三方接口给的直链后缀和真实内容对不上是常事
    （比如 pic 写着 .jpg 其实返回 PNG），按后缀处理会把上传搞坏。
    """
    if not data:
        return None, None
    for prefix, suffix, mime in _IMAGE_MAGIC:
        if data.startswith(prefix):
            return suffix, mime
    # WEBP 是容器格式：前 4 字节 'RIFF' + 4 字节长度 + 第 8~12 字节 'WEBP'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return '.webp', 'image/webp'
    return None, None


def image_payload_error(data, content_type='', max_bytes=DEFAULT_IMAGE_MAX_BYTES) -> str:
    """校验下载到的图片，返回中文错误原因（通过返回 None）。"""
    if not data:
        return '图片内容为空'
    if max_bytes and len(data) > max_bytes:
        return '图片超过 %.1f MB 上限' % (max_bytes / 1024.0 / 1024.0)
    declared = str(content_type or '').split(';')[0].strip().lower()
    if declared and not declared.startswith('image/'):
        # 有些图床出错时会返回一个 200 + HTML 错误页，光看状态码发现不了
        return '对方返回的不是图片（Content-Type: %s）' % declared
    suffix, _mime = image_kind(data)
    if suffix is None:
        return '文件内容不是可识别的图片（仅支持 JPG / PNG / GIF / WEBP）'
    return None


def render_sign_text(sign) -> str:
    """把一条文昌帝君灵签拼成可以直接发出去的私信文本。

    接口的 poem 用制表符分隔四句，原样发出去在手机上是一整行、很难读，
    这里统一拆成换行（同时也兼容接口改用 \\n 分隔的情况）。
    """
    sign = sign or {}
    title = str(sign.get('title') or '').strip()
    raw_poem = str(sign.get('poem') or '').replace('\r', '\n')
    poem_lines = [re.sub(r'[ \t]+', ' ', piece).strip()
                  for piece in re.split(r'[\t\n]+', raw_poem)]
    content = str(sign.get('content') or '').strip()
    lines = []
    if title:
        lines.append('【%s】' % title)
    lines.extend(line for line in poem_lines if line)
    if content:
        lines.append(content)
    return '\n'.join(lines).strip()


def sign_summary(sign) -> str:
    """签文的一句话摘要，用于日志和发送记账（不把整段正文写进记录里）。"""
    title = str((sign or {}).get('title') or '').strip()
    return ('文昌帝君灵签 · %s' % title) if title else '文昌帝君灵签'


def image_record_text(text='', label='') -> str:
    """图文发送时写进发送记账的摘要文本。

    记账键只看「日期 + 好友」，这里存的是给人看的记录；图片本身不进 state.json
    （那是 JSON 原子写，塞 base64 会把状态文件撑爆），所以只留一句可读的说明。
    """
    body = str(text or '').strip()
    tag = str(label or '').strip()
    if body and tag:
        return '%s\n[图片] %s' % (body, tag)
    if body:
        return body
    return ('[图片] %s' % tag) if tag else '[图片]'
