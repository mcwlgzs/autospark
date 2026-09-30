"""notifier.push() / validate_push_url() 的回归测试。

为什么单独写这个文件：
  这两个函数以前一个测试都没有 —— 只有 `_recording_notify`（把 notify 整个换掉）
  和一条「evil.example.com 被拒」的路由用例。结果 push() 对「HTTP 200 + 合法 JSON
  但不是对象」（`[]` / `123` / `"ok"`）会抛 AttributeError：在 /Api/Notify/Test 里
  变成 HTTP 500，在 notify() 的通知线程里更是静默死掉、连一条失败日志都没有。
  这里起一个本机 HTTP 服务，把所有返回体形状都真跑一遍（不 mock urllib，测的就是
  真实的请求/判定路径）。

只依赖标准库 + notifier，不 import backend（不需要 selenium / Chrome）。
"""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from notifier import mask_push_url, private_host_reason, push, validate_push_url  # noqa: E402

RESPONSES = {
    '/ok': (200, 'application/json', json.dumps({'error_code': 0})),
    '/bad': (200, 'application/json', json.dumps({'error_code': 1, 'error_message': 'token 不对'})),
    '/list': (200, 'application/json', '[]'),
    '/num': (200, 'application/json', '123'),
    '/text': (200, 'text/plain', 'ok'),
    '/html': (200, 'text/html', '<html>ok</html>'),
    '/fail': (500, 'text/plain', 'boom'),
}


class _HookHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    seen = []

    def _finish(self, code, ctype, payload):
        data = payload.encode('utf-8')
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length).decode('utf-8', 'replace')
        type(self).seen.append({
            'path': self.path,
            'body': body,
            'content_type': self.headers.get('Content-Type'),
            'user_agent': self.headers.get('User-Agent'),
        })
        if self.path == '/redirect':
            self.send_response(302)
            self.send_header('Location', 'http://127.0.0.1:1/hijack')
            self.send_header('Content-Length', '0')
            self.end_headers()
            return
        code, ctype, payload = RESPONSES.get(self.path, (404, 'text/plain', '没这个钩子'))
        self._finish(code, ctype, payload)

    def log_message(self, *args):
        pass


class PushTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), _HookHandler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        _HookHandler.seen = []

    def url(self, path):
        return 'http://127.0.0.1:%d%s' % (self.port, path)

    def test_request_shape_and_success(self):
        """参数名必须是 showdoc 那套 title/content，且带自己的 UA。"""
        ok, detail = push(self.url('/ok'), '定时任务失败', '给「小明」发送失败', allow_private=True)
        self.assertTrue(ok, detail)
        self.assertEqual(detail, '已发送')
        sent = _HookHandler.seen[-1]
        self.assertEqual(sent['content_type'], 'application/x-www-form-urlencoded')
        self.assertEqual(sent['user_agent'], 'spark-panel/1.0')
        self.assertIn('title=', sent['body'])
        self.assertIn('content=', sent['body'])

    def test_business_level_failure(self):
        """HTTP 200 但 error_code 非 0 要判失败，并把对方给的说明带回来。"""
        ok, detail = push(self.url('/bad'), '标题', '内容', allow_private=True)
        self.assertFalse(ok)
        self.assertIn('token 不对', detail)

    def test_json_array_body_is_not_a_crash(self):
        """回归：`[]` 是合法 JSON 但不是对象，旧代码 data.get 抛 AttributeError。

        它会让 /Api/Notify/Test 直接 500，也会让 notify() 的通知线程静默死掉。
        200 说明对方收下了，按「已发送」处理，至少不能崩。
        """
        ok, detail = push(self.url('/list'), '标题', '内容', allow_private=True)
        self.assertTrue(ok, detail)
        self.assertEqual(detail, '已发送')

    def test_json_scalar_body_is_not_a_crash(self):
        ok, detail = push(self.url('/num'), '标题', '内容', allow_private=True)
        self.assertTrue(ok, detail)
        self.assertEqual(detail, '已发送')

    def test_non_json_body_counts_as_delivered(self):
        for path in ('/text', '/html'):
            ok, detail = push(self.url(path), '标题', '内容', allow_private=True)
            self.assertTrue(ok, '%s -> %s' % (path, detail))
            self.assertEqual(detail, '已发送', path)

    def test_http_error_status(self):
        ok, detail = push(self.url('/fail'), '标题', '内容', allow_private=True)
        self.assertFalse(ok)
        self.assertIn('HTTP 500', detail)
        self.assertIn('boom', detail, '对面返回的说明要带回来，否则用户不知道哪里错了')

    def test_unknown_hook_is_404(self):
        ok, detail = push(self.url('/nope'), '标题', '内容', allow_private=True)
        self.assertFalse(ok)
        self.assertIn('HTTP 404', detail)
        self.assertIn('没这个钩子', detail)

    def test_redirect_is_refused(self):
        """302 到内网等于绕过主机名检查，必须直接拒绝。"""
        ok, detail = push(self.url('/redirect'), '标题', '内容', allow_private=True)
        self.assertFalse(ok, detail)
        self.assertIn('跳转', detail)

    def test_private_host_needs_the_switch(self):
        self.assertFalse(push(self.url('/ok'), '标题', '内容')[0],
                         '默认必须拒绝本机地址')
        self.assertTrue(push(self.url('/ok'), '标题', '内容', allow_private=True)[0])


class ValidatePushUrlTestCase(unittest.TestCase):
    def test_public_https_is_accepted(self):
        self.assertIsNone(validate_push_url('https://push.showdoc.com.cn/server/api/push/abcdef123456'))

    def test_private_hosts_are_rejected_by_default_and_allowed_by_the_switch(self):
        for url in ('http://127.0.0.1:9865/hook', 'http://localhost:9865/hook',
                    'http://192.168.1.10:9865/hook', 'http://box.local/hook'):
            reason = validate_push_url(url)
            self.assertIsNotNone(reason, url)
            self.assertIn('内网', reason)
            self.assertIsNone(validate_push_url(url, allow_private=True), url)

    def test_scheme_and_credentials(self):
        self.assertIn('http', validate_push_url('ftp://example.com/hook') or '')
        self.assertIsNotNone(validate_push_url('https://user:pw@example.com/hook'))
        self.assertIsNotNone(validate_push_url(''))
        self.assertIsNotNone(validate_push_url('example.com/hook'))

    def test_private_host_reason_does_not_resolve_dns(self):
        """文档承诺过：不发起 DNS 查询，所以随便一个公网域名都算通过。"""
        self.assertIsNone(private_host_reason('push.example.com'))
        self.assertIn('内网', private_host_reason('127.0.0.1'))
        self.assertIn('内网', private_host_reason('[::1]'))


class MaskPushUrlTestCase(unittest.TestCase):
    def test_token_is_hidden(self):
        # 用假 token：这个测试只验证「末段会被打码」，不需要真的推送地址
        token = 'sample-token-not-a-real-one-000000000000'
        masked = mask_push_url('https://push.example.com/server/api/push/%s' % token)
        self.assertIn('push.example.com', masked)
        self.assertNotIn(token, masked)
        self.assertIn('***', masked)

    def test_empty_stays_empty(self):
        self.assertEqual(mask_push_url(''), '')
        self.assertEqual(mask_push_url(None), '')


if __name__ == '__main__':
    unittest.main()
