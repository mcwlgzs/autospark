"""消息通知：手机推送（showdoc / webhook）和邮箱（SMTP）。

凭据只留在本机 state.json：
  * 推送地址里的 token、邮箱密码都不写进日志，接口也只回打码后的信息；
  * 推送和邮件正文只放摘要，不放聊天内容；
  * 发送失败不影响主流程。

推送地址和 SMTP 主机默认拒绝本机、内网和链路本地地址。
确需本机邮局或 webhook 时，由调用方传入 allow_private=True
（对应环境变量 SPARK_ALLOW_PRIVATE_PUSH）。
"""

from __future__ import annotations

import ipaddress
import json
import re
import smtplib
import urllib.error
import urllib.parse
import urllib.request
from email.message import EmailMessage

_EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
_BLOCKED_HOSTS = frozenset({
    'localhost',
    'localhost.localdomain',
    'metadata.google.internal',
    'metadata.google',
})


class _RefuseRedirect(urllib.request.HTTPRedirectHandler):
    """推送地址一旦 302 到内网，等于绕过主机名检查。直接拒绝跳转。"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(newurl, code, '拒绝跳转到其他地址', headers, fp)


def private_host_reason(host):
    """主机名若指向本机或内网，返回原因；公网主机返回 None。不发起 DNS 查询。"""
    name = (host or '').strip().lower().rstrip('.')
    if name.startswith('[') and name.endswith(']'):
        name = name[1:-1]
    if not name:
        return '地址缺少主机名'
    if (name in _BLOCKED_HOSTS or name.endswith('.local')
            or name.endswith('.internal') or name.endswith('.localhost')):
        return '不能使用本机或内网地址'
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return None
    if not address.is_global:
        return '不能使用本机或内网地址'
    return None


def mask_push_url(url):
    """打码后的推送地址，用于回显给前端（不暴露 token 全文）。"""
    if not url:
        return ''
    try:
        parts = urllib.parse.urlsplit(url)
        path = parts.path.rstrip('/')
        tail = path.rsplit('/', 1)[-1] if path else ''
        masked_tail = (tail[:3] + '***' + tail[-3:]) if len(tail) > 6 else '***'
        return '%s://%s/…/%s' % (parts.scheme, parts.netloc, masked_tail)
    except Exception:
        return '***'


def validate_push_url(url, allow_private=False):
    """粗略校验推送地址，返回错误说明（通过返回 None）。"""
    if not url:
        return '推送地址不能为空'
    try:
        parts = urllib.parse.urlsplit(url)
    except Exception:
        return '推送地址格式不对'
    if parts.scheme not in ('http', 'https') or not parts.hostname:
        return '推送地址需要以 http:// 或 https:// 开头'
    if parts.username or parts.password:
        return '推送地址不要把账号密码写在 URL 里'
    reason = private_host_reason(parts.hostname)
    if reason and not allow_private:
        return '推送地址%s。如确需本机 webhook，请设置环境变量 SPARK_ALLOW_PRIVATE_PUSH=1' % reason
    return None


def push(url, title, content, timeout=8, allow_private=False):
    """发一条推送，返回 (是否成功, 说明)。"""
    invalid = validate_push_url(url, allow_private=allow_private)
    if invalid:
        return False, invalid
    payload = urllib.parse.urlencode({'title': title, 'content': content}).encode('utf-8')
    request = urllib.request.Request(url, data=payload, method='POST')
    request.add_header('Content-Type', 'application/x-www-form-urlencoded')
    request.add_header('User-Agent', 'spark-panel/1.0')
    opener = urllib.request.build_opener(_RefuseRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read().decode('utf-8', 'replace')
            status = response.status
    except urllib.error.HTTPError as exc:
        # urllib 对 4xx/5xx 直接抛 HTTPError（根本走不到下面的 status != 200 分支），
        # 不单独处理的话用户只会看到「请求失败: HTTP Error 500: Internal Server Error」——
        # 英文原文，而且对面返回的说明（往往写着 token 不对 / 参数缺失）全被丢掉了。
        try:
            detail = exc.read().decode('utf-8', 'replace').strip()
        except Exception:
            detail = ''
        if exc.code in (301, 302, 303, 307, 308):
            return False, '请求失败: 推送地址跳转到其他地址（HTTP %s），出于安全考虑不会跟随' % exc.code
        return False, '接口返回 HTTP %s%s' % (exc.code, ('：%s' % detail[:200]) if detail else '')
    except Exception as exc:
        return False, '请求失败: %s' % exc
    if status != 200:
        return False, '接口返回 HTTP %s' % status
    try:
        data = json.loads(body)
    except Exception:
        return True, '已发送'
    if not isinstance(data, dict):
        # 合法 JSON 但不是对象（[]、123、"ok"）：旧代码直接 data.get 会抛 AttributeError，
        # 在 /Api/Notify/Test 里变成 HTTP 500，在 notify() 的通知线程里更是静默死掉、
        # 连一条失败日志都没有。这里跟「响应不是 JSON」一视同仁：HTTP 200 就算送达。
        return True, '已发送'
    error_code = data.get('error_code', data.get('code', 0))
    if str(error_code) in ('0', '200', 'None'):
        return True, '已发送'
    return False, '接口返回: %s' % (data.get('error_message') or data.get('message') or body[:200])


def validate_email_settings(settings, allow_private=False):
    """校验 SMTP 配置，返回错误说明（通过返回 None）。"""
    settings = settings or {}
    host = str(settings.get('host') or '').strip()
    if not host:
        return '请填写 SMTP 服务器'
    reason = private_host_reason(host)
    if reason and not allow_private:
        return 'SMTP 服务器%s。如确需本机邮局，请设置环境变量 SPARK_ALLOW_PRIVATE_PUSH=1' % reason
    try:
        port = int(settings.get('port') or 465)
    except (TypeError, ValueError):
        return 'SMTP 端口不正确'
    if port < 1 or port > 65535:
        return 'SMTP 端口不正确'
    recipient = str(settings.get('to') or '').strip()
    if not _EMAIL_RE.match(recipient):
        return '收件邮箱格式不正确'
    sender = str(settings.get('from') or settings.get('username') or '').strip()
    if sender and not _EMAIL_RE.match(sender):
        return '发件邮箱格式不正确'
    return None


def mask_email(address):
    text = str(address or '').strip()
    if '@' not in text:
        return ''
    name, _, domain = text.partition('@')
    head = name[:2] if len(name) > 2 else (name[:1] or '*')
    return '%s***@%s' % (head, domain)


def _redact(text, secret):
    rendered = str(text or '')
    if secret:
        rendered = rendered.replace(str(secret), '***')
    return rendered[:200]


def send_email(settings, title, content, timeout=20, allow_private=False):
    """用 SMTP 发一封纯文本邮件，返回 (是否成功, 说明)。"""
    invalid = validate_email_settings(settings, allow_private=allow_private)
    if invalid:
        return False, invalid
    settings = settings or {}
    host = str(settings.get('host') or '').strip()
    port = int(settings.get('port') or 465)
    username = str(settings.get('username') or '').strip()
    password = str(settings.get('password') or '')
    sender = str(settings.get('from') or username).strip()
    recipient = str(settings.get('to') or '').strip()
    use_ssl = bool(settings.get('ssl', True))
    message = EmailMessage()
    message['Subject'] = str(title or '火花助手通知')[:200]
    message['From'] = sender
    message['To'] = recipient
    message.set_content(str(content or ''))
    try:
        if use_ssl:
            client = smtplib.SMTP_SSL(host, port, timeout=timeout)
        else:
            client = smtplib.SMTP(host, port, timeout=timeout)
        try:
            client.ehlo()
            if not use_ssl:
                client.starttls()
                client.ehlo()
            if username:
                client.login(username, password)
            client.send_message(message)
        finally:
            try:
                client.quit()
            except Exception:
                pass
    except Exception as exc:
        return False, '邮件发送失败: %s' % _redact(exc, password)
    return True, '已发送'
