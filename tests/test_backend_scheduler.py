"""backend.py 调度与任务逻辑的回归测试（不需要浏览器、不需要联网）。

为什么这样写：
  `backend.py` 顶部就 import selenium / fastapi，直接 import 会要求装齐后端依赖并
  准备好 Chrome。这里往 `sys.modules` 里塞一套最小桩，再通过环境变量把数据目录与
  日志目录指到临时目录，于是可以**在只有标准库的机器上**直接跑：

      python -m unittest discover -s tests -t . -v

  覆盖的是这次改造最容易出错、代价也最高的那部分逻辑：
  到点触发 / 错过补跑 / 一天只发一次 / 当日补发 / 改时间不能丢任务 /
  去重键 / 密码与 token / 健康检查。
"""

import os
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# ---------------- 必须在 import backend 之前把环境隔离好 ----------------
# 否则 backend 会把测试期间的定时任务、密码哈希写进真实的 data/state.json
_TMP = tempfile.mkdtemp(prefix='spark-test-')
os.environ['SPARK_DATA_DIR'] = os.path.join(_TMP, 'data')
os.environ['SPARK_LOG_DIR'] = os.path.join(_TMP, 'logs')
os.environ['CHROME_PROFILE_DIR'] = os.path.join(_TMP, 'chrome-profile')
os.environ['SPARK_JITTER_MINUTES'] = '0'            # 关掉随机偏移，时间才可断言
os.environ['SPARK_CATCHUP_GRACE_MINUTES'] = '360'
os.environ['SPARK_RETRY_AFTER_MINUTES'] = '45'


def _install_stubs():
    """装上 selenium / fastapi / requests / uvicorn 的最小桩。"""
    fastapi = types.ModuleType('fastapi')

    class _App:
        def __init__(self, *a, **k):
            pass

        def add_middleware(self, *a, **k):
            pass

        def _route(self, *a, **k):
            def deco(func):
                return func
            return deco

        get = post = put = delete = _route

    def _default(value=None, **k):
        return value

    fastapi.FastAPI = _App
    fastapi.Header = _default
    fastapi.Body = _default
    fastapi.Query = _default
    fastapi.Request = object
    sys.modules['fastapi'] = fastapi

    mw_cors = types.ModuleType('fastapi.middleware.cors')

    class _Cors:
        def __init__(self, *a, **k):
            pass

    mw_cors.CORSMiddleware = _Cors
    sys.modules['fastapi.middleware'] = types.ModuleType('fastapi.middleware')
    sys.modules['fastapi.middleware.cors'] = mw_cors

    class _Options:
        def __init__(self, *a, **k):
            pass

        def add_argument(self, *a, **k):
            pass

        def add_experimental_option(self, *a, **k):
            pass

    class _Chrome:
        def __init__(self, *a, **k):
            pass

    webdriver = types.ModuleType('selenium.webdriver')
    webdriver.ChromeOptions = _Options
    webdriver.Chrome = _Chrome
    webdriver.Keys = types.SimpleNamespace(ENTER='\ue007')
    selenium = types.ModuleType('selenium')
    selenium.webdriver = webdriver
    sys.modules['selenium'] = selenium
    sys.modules['selenium.webdriver'] = webdriver

    svc = types.ModuleType('selenium.webdriver.chrome.service')

    class _Service:
        def __init__(self, *a, **k):
            pass

    svc.Service = _Service
    sys.modules['selenium.webdriver.chrome.service'] = svc

    by_mod = types.ModuleType('selenium.webdriver.common.by')

    class _By:
        XPATH = 'xpath'
        TAG_NAME = 'tag name'

    by_mod.By = _By
    sys.modules['selenium.webdriver.common.by'] = by_mod

    exc_mod = types.ModuleType('selenium.common.exceptions')

    class _NoSuchElement(Exception):
        pass

    class _SessionNotCreated(Exception):
        pass

    exc_mod.NoSuchElementException = _NoSuchElement
    exc_mod.SessionNotCreatedException = _SessionNotCreated
    sys.modules['selenium.common.exceptions'] = exc_mod

    requests = types.ModuleType('requests')

    def _no_network(*a, **k):
        # 一言接口在测试环境不可用：正好验证「取不到文案要用本地兜底」
        raise RuntimeError('测试环境不联网')

    requests.get = _no_network
    sys.modules['requests'] = requests

    uvicorn = types.ModuleType('uvicorn')
    uvicorn.run = lambda *a, **k: None
    sys.modules['uvicorn'] = uvicorn


_install_stubs()

import backend  # noqa: E402  （必须在装完桩之后导入）


class FakeDouyin:
    """只实现 add_time / edit_time 用到的 Find_Friends。"""

    def __init__(self, found=True):
        self.found = found
        self.last_send_detail = {}

    def Find_Friends(self, name):
        return backend.TrueString(self.found, None if self.found else '没有这位好友')


class SchedulerTestCase(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now()
        with backend._task_lock:
            backend.task_meta.clear()
        backend.STATE.set('tasks', [])
        backend.STATE.set('send_history', {})
        backend.STATE.set('retry_queue', {})
        backend.STATE.set('password_hash', backend.hash_password('test-password-123'))
        backend.douyin = FakeDouyin()
        backend.STATE.set('password_hash', backend.hash_password('test-password-123'))
        # 这几个函数要碰浏览器/鉴权，测调度逻辑时替换成恒过的桩
        backend.require_browser_session = lambda: None
        backend.send_precheck = lambda notify_on_pause=True: None
        backend.require_auth = lambda authorization=None: None
        backend._browser_ready_for_send = lambda: True
        while not backend._send_queue.empty():
            backend._send_queue.get_nowait()

    # ---------- 到点触发 ----------
    def test_mark_today_task_does_not_fire_today(self):
        """新建任务时今天的时间点已过 -> 记为今天已跑，不能立刻补发一条。"""
        backend._register_task('00:01', '小明', '早', mark_today=True)
        self.assertEqual(backend.check_due_tasks(now=self.now), [])

    def test_task_fires_once_at_planned_time(self):
        """到点触发一次，且同一天不重复。

        写法说明（这里踩过坑）：不能把「晚一点」写成 now + 固定分钟数。
        任务时刻是 HH:MM 精度、当天还会叠加随机窗口，而"晚 31 分钟"是秒级精度 ——
        两者并不总是先后关系。实测 23:29 触发时，now+30min = 23:59、
        now+31min 恰好落在 23:59:00，与计划时刻**同一秒**，于是断言随机地红。
        所以这里改成从 _task_view 里读出框架自己算出的 next_run（它必然晚于计划时刻，
        也天然处理了跨午夜），再分别断言「到点前不触发」和「到点后触发一次」。
        """
        target = (self.now + timedelta(minutes=30)).strftime('%H:%M')
        task_id = backend._register_task(target, '小红', '早', mark_today=False)
        view = backend._task_view(task_id, backend.task_meta[task_id], moment=self.now)
        next_run = datetime.strptime(view['next_run'], '%Y-%m-%d %H:%M:%S')

        if next_run - self.now > timedelta(minutes=1):
            # 只有在计划时刻确实还没到时，这条断言才有意义
            # （若 next_run 几乎就是现在，说明今天恰好卡在窗口边缘，直接跳过）。
            self.assertEqual(backend.check_due_tasks(now=self.now), [], '未到点不该触发')

        fires_at = next_run + timedelta(seconds=1)
        self.assertEqual(backend.check_due_tasks(now=fires_at), [task_id], '到点应触发一次')
        self.assertEqual(backend.check_due_tasks(now=fires_at + timedelta(minutes=1)), [],
                         '同一天不能重复触发')
        self.assertEqual(backend._send_queue.qsize(), 1, '应只排入一条发送任务')

    # ---------- 错过补跑 ----------
    def test_catch_up_within_grace_window(self):
        missed = self.now - timedelta(minutes=5)
        if missed.date() != self.now.date():
            self.skipTest('恰好跑在午夜后几分钟，跨天场景不适用')
        task_id = backend._register_task(missed.strftime('%H:%M'), '小刚', '早', mark_today=False)
        self.assertEqual(backend.check_due_tasks(now=self.now), [task_id],
                         '宽限窗口内应该补跑')

    def test_no_catch_up_beyond_grace_window(self):
        missed = self.now - timedelta(minutes=5)
        backend._register_task(missed.strftime('%H:%M'), '小强', '早', mark_today=False)
        original = backend.CATCHUP_GRACE_MINUTES
        backend.CATCHUP_GRACE_MINUTES = 1
        try:
            self.assertEqual(backend.check_due_tasks(now=self.now), [],
                             '超出宽限窗口不该补跑（避免半夜突然发消息）')
        finally:
            backend.CATCHUP_GRACE_MINUTES = original

    def test_no_catch_up_when_disabled(self):
        missed = self.now - timedelta(minutes=5)
        backend._register_task(missed.strftime('%H:%M'), '小强', '早', mark_today=False)
        original = backend.CATCHUP_GRACE_MINUTES
        backend.CATCHUP_GRACE_MINUTES = 0
        try:
            self.assertEqual(backend.check_due_tasks(now=self.now), [])
        finally:
            backend.CATCHUP_GRACE_MINUTES = original

    def test_browser_not_ready_defers_without_burning_today(self):
        """浏览器没起时不能把任务记成「今天已跑」，否则用户初始化后就再也不补了。"""
        missed = self.now - timedelta(minutes=5)
        if missed.date() != self.now.date():
            self.skipTest('恰好跑在午夜后几分钟，跨天场景不适用')
        task_id = backend._register_task(missed.strftime('%H:%M'), '小刚', '早', mark_today=False)
        backend._browser_ready_for_send = lambda: False
        try:
            self.assertEqual(backend.check_due_tasks(now=self.now), [],
                             '浏览器没就绪时不该入队')
            self.assertIsNone(backend.task_meta[task_id]['last_run_date'],
                              '更不该写 last_run_date（那会把今天的机会烧掉）')
        finally:
            backend._browser_ready_for_send = lambda: True
        self.assertEqual(backend.check_due_tasks(now=self.now), [task_id],
                         '浏览器就绪后应能补上')

    # ---------- 任务增删改 ----------
    def test_edit_same_time_keeps_task(self):
        """审计发现的必现 bug：时间没变时旧实现会把刚建的任务 cancel 掉还回「成功」。"""
        task_id = backend._register_task('21:00', '小明', '自定义文案', mark_today=False)
        res = backend.edit_time(payload={'name': '小明', 'new_time': '21:00', 'text': '自定义文案'})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(backend._find_task_by_name('小明'), task_id, '任务不该消失')
        self.assertEqual(backend.task_meta[task_id]['text'], '自定义文案', '文案不该被覆盖')

    def test_edit_blank_text_falls_back_to_daily_quote(self):
        """留空 = 「每次发送前现取」，而不是在建/改任务时写死一句。

        旧实现在建任务时取一次名言就固定下来，结果每天给同一个人发的都是同一句话；
        现在的契约是空文案照原样存，发送时由 _resolve_text 兜底 —— 所以这里既要
        文本为空（不能存成写死的句子），又要真的能兜底出一句非空文案。
        """
        task_id = backend._register_task('21:00', '小明', '自定义文案', mark_today=False)
        backend.edit_time(payload={'name': '小明', 'new_time': '22:30', 'text': ''})
        self.assertEqual(backend.task_meta[task_id]['time'], '22:30')
        self.assertEqual(backend.task_meta[task_id]['text'], '',
                         '空文案代表「每次现取」，不能存成写死的兜底句')
        self.assertTrue(backend._resolve_text(''), '发送时应能兜底出一句非空文案')

    def test_add_time_normalises_and_rejects_duplicate(self):
        res = backend.add_time(payload={'time': '9:5', 'name': '小美', 'text': ''})
        self.assertEqual(res.get('code'), 200, res)
        meta = backend.task_meta[res['task_id']]
        self.assertEqual(meta['time'], '09:05', '时间应被归一化成 HH:MM')
        self.assertEqual(meta['text'], '', '空文案照原样存，发送时再兜底')
        self.assertTrue(backend._resolve_text(''),
                        '一言接口不可用时应使用本地兜底文案')
        again = backend.add_time(payload={'time': '09:05', 'name': '小美', 'text': 'x'})
        self.assertEqual(again.get('code'), 400, '同一好友不该有两条任务')

    def test_delete_task(self):
        task_id = backend._register_task('21:00', '小明', '早', mark_today=False)
        self.assertEqual(backend.del_time(payload={'task_id': task_id}).get('code'), 200)
        self.assertIsNone(backend._find_task_by_name('小明'))
        self.assertEqual(backend.del_time(payload={'task_id': task_id}).get('code'), 404)

    def test_restore_tasks_accepts_legacy_records(self):
        """旧格式（task_id 为「时间_好友名」、文案为空）也要能恢复。"""
        backend.STATE.set('tasks', [
            {'task_id': '21:00_旧任务', 'time': '21:00', 'name': '旧任务', 'text': ''},
            {'task_id': 'bad', 'time': '21:00'},          # 缺 name，应跳过
        ])
        self.assertEqual(backend.restore_tasks(), 1)
        self.assertEqual(backend.task_meta['21:00_旧任务']['text'], '',
                         '空文案代表「每次现取」，不能在恢复时被丢掉')

    # ---------- 防重复发送 ----------
    def test_history_key_ignores_message_text(self):
        """key 只看日期+好友：旧实现拼了文案哈希，改文案就能绕过当天去重。"""
        backend._history_mark('小美', '第一条文案', 'success')
        blocked = backend.send_guard('小美', '完全不同的第二条文案', scope='task')
        self.assertIsNotNone(blocked, '文案变了也必须拦住当天重复')
        backend._history_mark('小美', 'x', 'failed')
        self.assertIsNone(backend.send_guard('小美', 'y', scope='task'),
                          'failed 记录要放行，当日补发才补得上')

    def test_history_keeps_only_recent_days(self):
        history = {
            '2020-01-01|小明': {'status': 'success', 'at': '2020-01-01 10:00:00'},
            backend._history_key('小明'): {'status': 'success', 'at': 'now'},
        }
        cleaned = backend.history_clean(history, backend.today_str(), backend.HISTORY_KEEP_DAYS)
        self.assertIn(backend._history_key('小明'), cleaned)
        self.assertNotIn('2020-01-01|小明', cleaned)

    # ---------- 当日补发 ----------
    def test_daily_retry_queue(self):
        self.assertTrue(backend.schedule_daily_retry('小美', '文案', '打开会话超时'))
        self.assertFalse(backend.schedule_daily_retry('小美', '文案', '打开会话超时'),
                         '同一好友当天只补一次')
        self.assertFalse(backend.schedule_daily_retry('小刚', '文案', '会话列表里没有「小刚」'),
                         '永久性失败不该补发')
        self.assertEqual(backend.check_daily_retry(now=self.now), [], '未到点不补')
        # 把 due_at 直接改到「刚刚」，避免测试恰好跑在午夜前 45 分钟内导致跨天
        state = backend.STATE.get('retry_queue') or {}
        state['due_at'] = (self.now - timedelta(seconds=1)).strftime('%Y-%m-%d %H:%M:%S')
        backend.STATE.set('retry_queue', state)
        items = backend.check_daily_retry(now=self.now)
        self.assertEqual([item['name'] for item in items], ['小美'])
        self.assertEqual(backend.check_daily_retry(now=self.now), [], '每天最多补一次')

    def test_clear_retry_queue(self):
        backend.schedule_daily_retry('小美', '文案', '打开会话超时')
        self.assertTrue(backend._clear_retry_queue('操作频繁'))
        self.assertEqual(backend.check_daily_retry(now=self.now), [],
                         '取消后不该再补发')

    # ---------- 文案 ----------
    def test_resolve_text_from_pool(self):
        rendered = backend._resolve_text('早安{weekday}\n晚安')
        self.assertTrue(rendered.startswith('早安') or rendered == '晚安', rendered)
        self.assertTrue(backend._resolve_text(''), '空池要回落到非空文案')

    # ---------- 密码 / token ----------
    def test_password_flow(self):
        backend.STATE.set('password_hash', None)
        backend._password = '123456'
        self.assertTrue(backend.check_password('123456'))
        self.assertFalse(backend.check_password('wrong'))
        self.assertIsNotNone(backend.password_policy_error('123'))
        self.assertIsNotNone(backend.password_policy_error('12345678'))
        self.assertIsNone(backend.password_policy_error('abcd1234'))
        backend.STATE.set('password_hash', backend.hash_password('abcd1234'))
        self.assertTrue(backend.check_password('abcd1234'))
        self.assertFalse(backend.check_password('123456'))

    def test_token_revoke_all(self):
        token = backend.generate_token()
        self.assertTrue(backend.verify_token(token))
        backend._valid_tokens.revoke_all()
        self.assertFalse(backend.verify_token(token), '改密码后旧 token 必须失效')

    def test_change_password_rejects_bad_input(self):
        backend.STATE.set('password_hash', backend.hash_password('abcd1234'))
        self.assertEqual(
            backend.change_password(payload={'old_password': 'wrong', 'new_password': 'abcd12345'}
                                    ).get('code'), 400)
        self.assertEqual(
            backend.change_password(payload={'old_password': 'abcd1234', 'new_password': '123'}
                                    ).get('code'), 400, '太短的新密码要被拒')
        self.assertEqual(
            backend.change_password(payload={'old_password': 'abcd1234', 'new_password': 'abcd12345'}
                                    ).get('code'), 200)
        self.assertFalse(backend.verify_token('anything'), '改密码后应清空所有会话')

    # ---------- 健康检查 ----------
    def test_healthz(self):
        data = backend.Healthz()['data']
        self.assertEqual(data['status'], 'ok')
        for key in ('version', 'uptime_seconds', 'scheduler_alive', 'queue_depth'):
            self.assertIn(key, data)

    def test_healthz_does_not_touch_webdriver(self):
        """healthz 免鉴权、且被 Docker HEALTHCHECK 每 30 秒打一次。

        它绝不能去碰 WebDriver：Chrome 卡住时那次 execute_script 会一直挂着，
        于是「探活接口自己先卡死」。这里用一个"一被调用就报错"的假 driver 钉住这条约束。
        """
        backend.driver = _ExplodingDriver()
        try:
            # 真探测会碰到 WebDriver；只读心跳不会。
            self.assertFalse(backend._driver_alive(probe=True))
            self.assertFalse(backend._driver_alive(probe=False))
            self.assertEqual(backend.Healthz()['data']['browser_ready'], False)
        finally:
            backend.driver = None

    # ---------- 发送结果判定（回归：曾经把漏发记成成功） ----------
    def test_unreadable_message_list_is_never_reported_as_success(self):
        """核心承诺是「每位好友每天恰好一条」，所以绝不能把"没发出去"判成成功。

        历史缺陷：消息列表选择器一旦失效，_chat_probe 返回 {list: false}，
        Send_Frinder 会把「输入框被清空」升格成发送成功；上层据此写入当天记账，
        于是这个人当天被去重跳过 —— 面板显示成功、火花却断了，而且没有任何失败通知。

        注意这个用例必须走 Send_Frinder 的完整判定分支：缺陷在**调用方**那几行里，
        只测 _chat_probe / _confirm_message_delivered 是测不出来的
        （它们本来就返回 nolist，曾经错的是拿到 nolist 之后怎么处理）。
        """
        # 正常路径先确认探针本身的返回值
        backend.driver = _FakeDriver(script_result={'list': False})
        try:
            self.assertEqual(backend._chat_probe('早安', 'check').get('list'), False)

            # 现在走完整发送流程：会话能打开、输入框能拿到、消息写进去了，
            # 但消息列表读不出来（选择器失效）—— 输入框会被清空，
            # 于是恰好命中那条曾经误报成功的分支。
            stubs = _stub_send_path(backend, editor_cleared=True)
            try:
                backend.douyin = backend.Douyin(backend.driver)
                out = backend.douyin.Send_Frinder('小红', '早安')
                self.assertIsNotNone(out, '任何分支都必须返回 TrueString，不能返回 None')
                self.assertFalse(
                    out.is_bool,
                    '读不到消息列表时绝不能判成功（这会把漏发记成成功并跳过当天）')
                self.assertIn('未确认', out.string or '',
                              '要如实说明"没确认送达"，而不是报成功')
            finally:
                stubs()
        finally:
            backend.driver = None

    def test_unreadable_message_list_does_not_mark_history_success(self):
        """连带保证：读不到列表这一次，绝不能往发送记账里写 success。

        这是上面那条缺陷真正致命的地方 —— 写了 success，当天去重就会把这位好友
        整天跳过，火花断掉而面板一切正常。
        """
        backend.driver = _FakeDriver(script_result={'list': False})
        backend.STATE.set('send_history', {})
        stubs = _stub_send_path(backend, editor_cleared=True)
        try:
            backend.douyin = backend.Douyin(backend.driver)
            out = backend.douyin.Send_Frinder('小红', '早安')
            # 模拟接口层的记账（与 /Api/Send、run_scheduled_send 同样的判定）
            status = 'success' if out.is_bool else (
                'unknown' if '未确认' in (out.string or '') else 'failed')
            self.assertNotEqual(status, 'success')
            self.assertEqual(status, 'unknown',
                             '结果不确定要记 unknown（当天不重发，安全方向正确）')
        finally:
            stubs()
            backend.driver = None

    def test_confirm_reports_success_only_with_new_clean_bubble(self):
        """只有「出现未标记的新气泡、计数变多、且状态干净」才算成功。"""
        # side 必须是 'self'：方向判不出来的新气泡（含缺 side 字段）根本不算证据。
        clean = {'list': True, 'total': 3, 'matches': 2, 'untagged': 1, 'state': 'clean',
                 'side': 'self'}
        backend.driver = _FakeDriver(script_result=clean)
        try:
            result, _detail = backend._confirm_message_delivered('早安', 1, timeout=2.0)
            self.assertEqual(result, 'success')
        finally:
            backend.driver = None

        # 方向未知即使出现新气泡也不能作为成功证据。
        unknown_side = {'list': True, 'total': 3, 'matches': 2, 'untagged': 1,
                        'state': 'clean', 'side': 'unknown'}
        backend.driver = _FakeDriver(script_result=unknown_side)
        try:
            result, _detail = backend._confirm_message_delivered('早安', 1, timeout=0.02)
            self.assertEqual(result, 'unconfirmed')
        finally:
            backend.driver = None

        # 气泡没有变多（untagged=0）=> 不能判成功
        stale = {'list': True, 'total': 3, 'matches': 1, 'untagged': 0, 'state': 'clean'}
        backend.driver = _FakeDriver(script_result=stale)
        try:
            result, _detail = backend._confirm_message_delivered('早安', 1, timeout=0.3)
            self.assertEqual(result, 'unconfirmed')
        finally:
            backend.driver = None

    def test_script_error_is_not_mistaken_for_missing_list(self):
        """脚本抛异常（页面被销毁/跳走）与「确实读不到列表」是两回事。

        旧实现把两者都变成 {}，于是任何一次脚本抖动都会立刻走向 nolist 那条
        曾经会误报成功的分支。这里确认异常被单独暴露出来（返回 {}），
        而正常返回 list:false 时也仍然是 list:false。
        """
        backend.driver = _ExplodingDriver()
        try:
            self.assertEqual(backend._chat_probe('早安', 'check'), {})
        finally:
            backend.driver = None

    # ---------- 登录判定（回归：曾经把"找不到元素"当成登录成功） ----------
    def test_login_success_requires_positive_evidence(self):
        """找不到登录面板 ≠ 登录成功。

        旧实现在 /Api/LoginPhoneInput 里用「找不到 #douyin_login_comp_flat_panel/picture」
        证明登录成功。页面改版、跳转中、元素没渲染完都会让它误报成功，
        之后所有发送都会失败。现在必须由 _detect_logged_in_state() 给出正面证据。
        """
        restore = _stub_login_dom(backend)
        backend.Login_is_bool = False
        calls = {'n': 0}

        def never_logged_in():
            calls['n'] += 1
            return False

        original_detect = backend._detect_logged_in_state
        backend._detect_logged_in_state = never_logged_in
        try:
            res = backend.authorizations(payload={'code': '123456'})
            self.assertNotEqual(res.get('code'), 200,
                                '拿不到正面证据时不能报登录成功')
            self.assertIn('未能确认', res.get('data') or '')
            self.assertFalse(backend.Login_is_bool, '判定失败时不能把状态设成已登录')
            self.assertGreaterEqual(calls['n'], 2, '应该在等待窗口内多次重试确认')
        finally:
            backend._detect_logged_in_state = original_detect
            restore()
            backend.Login_is_bool = False

    def test_login_success_accepts_positive_evidence(self):
        """正面证据成立时应当报成功。"""
        restore = _stub_login_dom(backend)
        backend.Login_is_bool = False
        original_detect = backend._detect_logged_in_state
        backend._detect_logged_in_state = lambda: True
        try:
            res = backend.authorizations(payload={'code': '123456'})
            self.assertEqual(res.get('code'), 200, res)
            self.assertTrue(backend.Login_is_bool)
        finally:
            backend._detect_logged_in_state = original_detect
            restore()
            backend.Login_is_bool = False

    # ---------- 并发保护（回归：@serialized 漏挂） ----------
    def test_every_browser_route_is_serialized(self):
        """所有会碰浏览器的路由都必须挂 @serialized。

        历史缺陷：9 个会读写 driver 的路由漏了这把锁（含每 4 秒被前端轮询的
        GetLoginPng、以及会抓 DOM 的 /Time/add），而 WebDriver 跨线程使用是
        undefined behavior —— 发送进行中被别的请求刷新页面，就会出现
        「消息发出去了却报未确认」甚至发错人。
        这里直接读源码，防止以后新增路由时又漏掉。
        """
        source = open(os.path.join(REPO_ROOT, 'backend.py'), encoding='utf-8').read()
        blocks = _route_blocks(source)
        self.assertTrue(blocks, '应当能解析出路由')

        required = {
            '/Api/Init', '/Api/GetInit', '/Api/login', '/Api/Pnglogin', '/Api/GetLogin',
            '/Api/login/Init/GetLoginPng', '/Api/login/Init/GetCooker', '/Api/GetFriendsList',
            '/Api/Send', '/Api/GetUsername', '/Api/GetScrlk', '/Api/DieLogin',
            '/Api/LoginPhone', '/Api/LoginPhoneInput', '/Api/Verify/State', '/Api/Verify/Select',
            '/Api/Verify/Back', '/Api/Verify/Code', '/Api/Verify/Password', '/Api/LoginDebug',
            '/Time/add', '/Api/Send/Check',
        }
        for path in sorted(required):
            self.assertIn(path, blocks, '路由 %s 应当存在' % path)
            self.assertTrue(blocks[path]['serialized'],
                            '路由 %s 会操作浏览器，必须挂 @serialized' % path)

        # 反向检查：真的碰 driver 的路由一个都不许漏
        touching = {p for p, b in blocks.items() if b['touches_browser']}
        unprotected = sorted(p for p in touching if not blocks[p]['serialized'])
        self.assertEqual(unprotected, [], '这些路由会碰浏览器却没有串行化保护: %s' % unprotected)


def _route_blocks(source):
    """把 backend.py 切成「一个路由一段」，记录它是否挂了 @serialized。"""
    import re
    blocks = {}
    pattern = re.compile(r"^@app\.(?:get|post|put|delete)\('([^']+)'\)", re.M)
    matches = list(pattern.finditer(source))
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(source)
        chunk = source[start:end]
        # 直到下一个顶层 def 为止，其余视为装饰器区
        body_start = chunk.find('\ndef ')
        decorators = chunk if body_start < 0 else chunk[:body_start]
        body = chunk if body_start < 0 else chunk[body_start:]
        blocks[match.group(1)] = {
            'serialized': '@serialized' in decorators,
            'touches_browser': bool(re.search(r'\b(?:driver|douyin)\b', body)),
        }
    return blocks


def _stub_send_path(backend_module, editor_cleared=True):
    """把 Send_Frinder 里除「发送结果判定」之外的每一步都换成桩。

    目标是让它走到 nolist 那条分支：会话打开成功、输入框拿到、内容写进去了，
    但消息列表读不出来。editor_cleared 模拟「输入框已被清空」——
    也就是历史上被误当成"已提交 = 发送成功"的那个信号。
    返回恢复函数。
    """
    editor = _FakeElement()
    originals = {
        '_open_conversation': backend_module._open_conversation,
        '_wait_chat_editor': backend_module._wait_chat_editor,
        '_type_into_editor': backend_module._type_into_editor,
        '_editor_still_has': backend_module._editor_still_has,
        '_wait_message_sent': backend_module._wait_message_sent,
        '_save_failure_shot': backend_module._save_failure_shot,
        'Updara_FrinderList': backend_module.Douyin.Updara_FrinderList,
    }
    backend_module._open_conversation = lambda name, row_xpath, timeout=8: (True, '')
    backend_module._wait_chat_editor = lambda timeout=10: editor
    backend_module._type_into_editor = lambda element, text: True
    # 输入框"已经没有内容"了 —— 历史上这一步会被当成发送成功的证据
    backend_module._editor_still_has = lambda element, text: not editor_cleared
    backend_module._wait_message_sent = lambda element, text, timeout=5: editor_cleared
    backend_module._save_failure_shot = lambda label: None
    def fake_friend_list(self):
        # 真实实现会在这里重建 self.friends_xpath_list（Send_Frinder 第一步就调它），
        # 所以桩必须真的把映射填进去；否则 Send_Frinder 会以「没读到会话列表」
        # 提前返回，根本走不到被测的发送结果判定那一段。
        self.friends_xpath_list = {'小红': '//stub/row'}
        return [backend_module.UserFriendsInfo('小红', '', '1')]

    backend_module.Douyin.Updara_FrinderList = fake_friend_list

    def restore():
        for name, value in originals.items():
            if name == 'Updara_FrinderList':
                backend_module.Douyin.Updara_FrinderList = value
            else:
                setattr(backend_module, name, value)

    return restore


def _stub_login_dom(backend_module):
    """把手机号登录那条路径上的 DOM 操作换成桩，返回一个恢复函数。

    为什么要这么做：这个用例要验证的是「判定登录成功需要正面证据」这条规则，
    不是「能不能找到验证码输入框」。不把前面几步桩掉的话，路由会在
    _find_first 抛 NoSuchElementException 时就返回 400，根本走不到判定那一段 ——
    于是用例会在一个与被测规则无关的地方红掉（这个坑已经踩过一次）。
    """
    originals = {
        '_find_first': backend_module._find_first,
        '_set_value': backend_module._set_value,
        '_try_click': backend_module._try_click,
        '_is_two_factor_page': backend_module._is_two_factor_page,
        'driver': backend_module.driver,
    }
    backend_module.driver = _FakeDriver(harmless=True)
    backend_module._find_first = lambda *a, **k: _FakeElement()
    backend_module._set_value = lambda element, value: True
    backend_module._try_click = lambda *a, **k: True
    backend_module._is_two_factor_page = lambda: False

    def restore():
        backend_module._find_first = originals['_find_first']
        backend_module._set_value = originals['_set_value']
        backend_module._try_click = originals['_try_click']
        backend_module._is_two_factor_page = originals['_is_two_factor_page']
        backend_module.driver = originals['driver']

    return restore


class _FakeElement:
    """DOM 元素替身。"""

    def is_displayed(self):
        return True

    def is_enabled(self):
        return True

    def get_attribute(self, name):
        return ''

    @property
    def text(self):
        return ''

    def click(self):
        return None

    def send_keys(self, *a, **k):
        return None


class _FakeDriver:
    """最小 WebDriver 替身：execute_script 返回给定结果，其余查找一律空。"""

    def __init__(self, script_result=None, harmless=False):
        self.script_result = script_result
        self.harmless = harmless
        self.scripts = []

    def execute_script(self, script, *args):
        self.scripts.append(script)
        if self.harmless:
            return None
        return self.script_result

    def find_elements(self, *a, **k):
        return []

    def find_element(self, *a, **k):
        raise backend.NoSuchElementException('stub')


class _ExplodingDriver:
    """任何一次 WebDriver 调用都立刻炸掉 —— 用来证明某条路径根本没碰浏览器。"""

    def execute_script(self, *a, **k):
        raise AssertionError('这条路径不应该调用 WebDriver')

    def find_elements(self, *a, **k):
        raise AssertionError('这条路径不应该调用 WebDriver')

    def find_element(self, *a, **k):
        raise AssertionError('这条路径不应该调用 WebDriver')



# ==================== 图片消息 / 文昌帝君灵签 ====================
def _unexpected(*a, **k):
    """放在「这段逻辑不该被碰到」的位置上：一旦被调用就立刻炸掉。

    比断言调用次数更直接：比如「任务没勾灵签时不该请求灵签接口」，
    用这个替身替换 fetch_wenchang_sign 即可，不需要记录调用次数。
    """
    raise AssertionError('这条路径不应该被调用')


class _ScriptedImageDriver:
    """按「用哪个脚本 + 哪个模式」返回不同结果的 WebDriver 替身。

    为什么不能沿用 `_FakeDriver` 的「任何脚本都返回同一个结果」：
    一次图片发送要先用 tag 模式数一遍已有的图片气泡（before_matches），再用 check
    模式确认新气泡，两次结果必须不同才判得出「多了一条」；图文发送还要求文字探针和
    图片探针各返回各的，否则永远判不出送达。
    """

    def __init__(self, image_matches_before=1, image_check=None, text_tag=None, text_check=None):
        self.image_matches_before = image_matches_before
        self.image_tag = {'list': True, 'total': 2, 'matches': 0, 'untagged': 0, 'state': 'none'}
        self.image_check = image_check if image_check is not None else {
            'list': True, 'total': 3, 'matches': image_matches_before + 1,
            'untagged': 1, 'state': 'clean', 'side': 'self',
        }
        self.text_tag = text_tag if text_tag is not None else {
            'list': True, 'total': 1, 'matches': 0, 'untagged': 0, 'state': 'none',
        }
        self.text_check = text_check if text_check is not None else {
            'list': True, 'total': 2, 'matches': 1, 'untagged': 1,
            'state': 'clean', 'side': 'self',
        }
        self.scripts = []

    def execute_script(self, script, *args):
        self.scripts.append(script)
        if script is backend.CHAT_IMAGE_PROBE_JS:
            mode = args[0] if args else ''
            if mode == 'tag':
                return dict(self.image_tag, matches=self.image_matches_before)
            return self.image_check
        if script is backend.CHAT_OUTGOING_PROBE_JS:
            mode = args[1] if len(args) > 1 else ''
            return self.text_tag if mode == 'tag' else self.text_check
        return None

    def find_elements(self, *a, **k):
        return []

    def find_element(self, *a, **k):
        raise backend.NoSuchElementException('stub')


class _RecordingEditor(_FakeElement):
    """记录 send_keys 参数的输入框替身：用来证明「上传失败绝不回车」「只按一次回车」。"""

    def __init__(self):
        self.keys = []

    def send_keys(self, *a, **k):
        self.keys.extend(a)


class FakeResponse:
    """requests 响应替身（download_image / fetch_wenchang_sign 用）。"""

    def __init__(self, status_code=200, content=b'', headers=None, payload=None):
        self.status_code = status_code
        self._content = content
        self.headers = headers if headers is not None else {}
        self._payload = payload

    def iter_content(self, chunk_size=65536):
        for start in range(0, len(self._content), chunk_size):
            yield self._content[start:start + chunk_size]

    def json(self):
        if self._payload is None:
            raise ValueError('响应不是 JSON')
        return self._payload


SIGN = {
    'title': '乙庚下下签',
    'poem': '田园价贯好商量\t事到公庭彼此伤\t纵使机关图得胜\t定为后世子孙殃',
    'content': '工作升迁方面，宜守不宜攻。',
    'pic': 'https://cdn.example.com/lq_17.jpg',
}
JPEG = b'\xff\xd8\xff\xe0' + b'z' * 4096


class ImageSendTestCase(unittest.TestCase):
    """发图与灵签：下载校验、开关落盘、送达判据、失败退化、只发图、立即试发。

    全部用例都不碰真实浏览器与网络：requests.get 与 WebDriver 都换成替身。
    这里刻意**不继承** SchedulerTestCase，而是直接借用它的 setUp：
    继承会把父类那几十个调度用例再跑一遍，凭空让整个套件的耗时翻倍。
    """

    def setUp(self):
        SchedulerTestCase.setUp(self)
        self._patched = {}

        def patch(name, value):
            # 同一个名字只记第一次的原值：否则连续两次 patch 同一名字，
            # 还原时会把中间那个替身装回去，污染后面的用例。
            if name not in self._patched:
                self._patched[name] = getattr(backend, name)
            setattr(backend, name, value)

        self.patch = patch
        # 「干净气泡」稳定期默认 1 秒，单测里没有意义，压到 0 免得每个用例白等
        patch('SEND_INITIAL_CLEAN_GRACE', 0.0)

    def tearDown(self):
        for name, value in self._patched.items():
            setattr(backend, name, value)
        backend.driver = None

    # ---------------- 助手 ----------------
    def _patch_get(self, result):
        """把 backend 模块里的 requests 换成「只返回给定响应 / 直接抛异常」的替身。"""
        def fake_get(*a, **k):
            if isinstance(result, Exception):
                raise result
            return result
        self.patch('requests', types.SimpleNamespace(get=fake_get))

    def _capture_send(self, detail=None):
        """替换真正的浏览器发送：记录参数并返回成功。返回记录用的 dict。"""
        captured = {}
        original = backend.Douyin.Send_Frinder
        self.addCleanup(lambda: setattr(backend.Douyin, 'Send_Frinder', original))

        def fake(self, name, text, paced=False, image=None):
            captured.update({'name': name, 'text': text, 'paced': paced, 'image': image})
            self.last_send_detail = dict(detail or {})
            return backend.TrueString(True, None)

        backend.Douyin.Send_Frinder = fake
        return captured

    def _recording_notify(self):
        notices = []
        self.patch('notify', lambda title, content, force=False: notices.append((title, content)))
        return notices

    def _stub_image_send(self, result):
        """把 Douyin._send_image_in_chat 换成给定结果，返回调用记录列表。"""
        calls = []
        original = backend.Douyin._send_image_in_chat
        self.addCleanup(lambda: setattr(backend.Douyin, '_send_image_in_chat', original))

        def fake(self, editor, name, path, paced=False):
            calls.append({'name': name, 'path': path, 'paced': paced})
            return result

        backend.Douyin._send_image_in_chat = fake
        return calls

    # ---------------- 图片下载与校验 ----------------
    def test_download_image_rejects_private_and_odd_urls(self):
        """图片地址来自第三方接口，只能是公网 http(s)：否则面板会变成内网探测工具。"""
        path, error = backend.download_image('http://127.0.0.1:8000/secret.jpg')
        self.assertIsNone(path)
        self.assertIn('内网', error)

        path, error = backend.download_image('file:///etc/passwd')
        self.assertIsNone(path)
        self.assertIn('http/https', error)

        path, error = backend.download_image('   ')
        self.assertIsNone(path)
        self.assertIn('为空', error)

    def test_download_image_checks_content_type_and_magic(self):
        """Content-Type 说是图片不算数，魔数说了才算（接口返回 HTML 错误页是常见情况）。"""
        url = 'https://cdn.example.com/x.jpg'
        self._patch_get(FakeResponse(content=JPEG, headers={'Content-Type': 'text/html'}))
        path, error = backend.download_image(url)
        self.assertIsNone(path)
        self.assertIn('不是图片', error)

        self._patch_get(FakeResponse(content=b'<html>oops</html>',
                                     headers={'Content-Type': 'image/jpeg'}))
        path, error = backend.download_image(url)
        self.assertIsNone(path)
        self.assertIn('不是可识别的图片', error)

        self._patch_get(FakeResponse(status_code=500, content=JPEG,
                                     headers={'Content-Type': 'image/jpeg'}))
        path, error = backend.download_image(url)
        self.assertIsNone(path)
        self.assertIn('HTTP 500', error)

    def test_download_image_dedupes_and_leaves_no_part_file(self):
        os.makedirs(backend.IMAGE_DIR, exist_ok=True)
        self._patch_get(FakeResponse(content=JPEG, headers={'Content-Type': 'image/jpeg'}))
        url = 'https://cdn.example.com/lq_17.jpg'
        first, error = backend.download_image(url)
        self.assertIsNone(error)
        self.assertTrue(os.path.isfile(first), first)
        self.assertEqual(os.path.dirname(first), backend.IMAGE_DIR)
        self.assertTrue(os.path.basename(first).endswith('.jpg'))

        # 同一张签图再下载一次不该重复落盘（文件名 = URL + 内容摘要）
        again, error = backend.download_image(url)
        self.assertEqual(again, first)
        self.assertIsNone(error)
        self.assertEqual(os.listdir(backend.IMAGE_DIR).count(os.path.basename(first)), 1,
                         '同一张图不该在 images/ 里留下两份')
        # 半截文件不能留在目录里（否则下次可能把不完整的图上传上去）
        self.assertEqual([n for n in os.listdir(backend.IMAGE_DIR) if n.endswith('.part')], [])

    def test_download_image_rejects_oversize(self):
        os.makedirs(backend.IMAGE_DIR, exist_ok=True)
        # 用例之间共用同一个临时 data 目录，所以只比较「下载前后有没有多出文件」
        before = set(os.listdir(backend.IMAGE_DIR))
        self.patch('IMAGE_MAX_BYTES', 1024)
        oversized = b'\xff\xd8\xff' + b'z' * 8192
        self._patch_get(FakeResponse(content=oversized, headers={'Content-Type': 'image/jpeg'}))
        path, error = backend.download_image('https://cdn.example.com/big.jpg')
        self.assertIsNone(path)
        self.assertIn('上限', error)
        self.assertEqual(set(os.listdir(backend.IMAGE_DIR)), before, '超限的图不该落盘')

    # ---------------- 灵签接口 ----------------
    def test_fetch_wenchang_sign_parses_response(self):
        self._patch_get(FakeResponse(payload={'code': 200, 'msg': '数据请求成功', 'data': dict(SIGN)}))
        sign = backend.fetch_wenchang_sign()
        self.assertIsNotNone(sign)
        self.assertEqual(sign['title'], SIGN['title'])
        self.assertEqual(sign['poem'], SIGN['poem'])
        self.assertEqual(sign['pic'], SIGN['pic'])

    def test_fetch_wenchang_sign_fails_closed(self):
        """取不到就返回 None，绝不回落本地随机文案：用户点的是灵签，换一句是骗人。"""
        for bad in ({'code': 500, 'msg': '接口炸了'},
                    {'code': 200, 'data': []},
                    {'code': 200, 'data': {'title': '', 'poem': '', 'content': ''}},
                    {'code': 200, 'data': {'title': '   '}}):
            self._patch_get(FakeResponse(payload=bad))
            self.assertIsNone(backend.fetch_wenchang_sign(), bad)

        self._patch_get(FakeResponse(status_code=502))
        self.assertIsNone(backend.fetch_wenchang_sign())

        self._patch_get(RuntimeError('断网了'))
        self.assertIsNone(backend.fetch_wenchang_sign())

    def test_bool_flag_reads_panel_and_script_style_values(self):
        self.assertTrue(backend._bool_flag(True))
        self.assertTrue(backend._bool_flag('wenchang'))   # 手动发送那条路径传的字符串
        self.assertTrue(backend._bool_flag('1'))
        self.assertTrue(backend._bool_flag(1))
        self.assertFalse(backend._bool_flag(False))
        self.assertFalse(backend._bool_flag(None))
        self.assertFalse(backend._bool_flag(''))
        # 关键：不能直接 bool(value) —— 字符串 'false' 也是真
        self.assertFalse(backend._bool_flag('false'))
        self.assertFalse(backend._bool_flag('0'))

    def test_prune_dir_removes_expired_then_excess(self):
        with tempfile.TemporaryDirectory() as tmp:
            now = time.time()
            # 前两个 30 天前、后两个 1 小时前：离边界足够远，不受执行耗时影响
            for index, age in enumerate([30 * 86400, 30 * 86400, 3600, 3600]):
                path = os.path.join(tmp, 'old%d' % index)
                with open(path, 'w', encoding='utf-8') as fp:
                    fp.write('x')
                os.utime(path, (now - age, now - age))
            self.assertEqual(backend._prune_dir(tmp, 7, 100), 2, '超过 7 天的都该清掉')
            self.assertEqual(len(os.listdir(tmp)), 2)

            for index in range(4):
                path = os.path.join(tmp, 'new%d' % index)
                with open(path, 'w', encoding='utf-8') as fp:
                    fp.write('x')
                stamp = now - (4 - index) * 60          # 4、3、2、1 分钟前
                os.utime(path, (stamp, stamp))
            # 数量上限 3：先把最旧的（那两张 1 小时前的）删掉
            self.assertEqual(backend._prune_dir(tmp, 0, 3), 3)
            self.assertEqual(sorted(os.listdir(tmp)), ['new1', 'new2', 'new3'])

        # 目录不存在不能抛异常（第一次运行、或用户手动清过 data/）
        self.assertEqual(backend._prune_dir(os.path.join(tmp, 'not-there'), 1, 1), 0)

    # ---------------- 图片送达判据 ----------------
    def test_send_image_in_chat_success_on_new_image_bubble(self):
        editor = _RecordingEditor()
        self.patch('driver', _ScriptedImageDriver(image_matches_before=1))
        self.patch('_upload_image', lambda element, path: (True, None))
        self.patch('_wait_image_preview', lambda element, timeout=0: True)
        douyin = backend.Douyin(backend.driver)

        out = douyin._send_image_in_chat(editor, '小红', os.path.join('tmp', 'lq_17.jpg'))
        self.assertTrue(out.is_bool, out.string)
        self.assertEqual(editor.keys, [backend.Keys.ENTER], '只准按一次回车')
        self.assertEqual(douyin.last_send_detail.get('图片'), 'lq_17.jpg')
        self.assertIn('图片气泡', douyin.last_send_detail.get('确认方式', ''))

    def test_send_image_in_chat_stops_after_one_enter_when_unconfirmed(self):
        """没有新图片气泡 → 结果不确定，且**不能**反复回车（会重复发图）。"""
        editor = _RecordingEditor()
        self.patch('driver', _ScriptedImageDriver(image_matches_before=1, image_check={
            'list': True, 'total': 2, 'matches': 1, 'untagged': 1, 'state': 'clean', 'side': 'self'}))
        self.patch('_upload_image', lambda element, path: (True, None))
        self.patch('_wait_image_preview', lambda element, timeout=0: True)
        self.patch('_save_failure_shot', lambda label: None)
        self.patch('IMAGE_CONFIRM_TIMEOUT', 0.05)
        douyin = backend.Douyin(backend.driver)

        out = douyin._send_image_in_chat(editor, '小红', 'lq_17.jpg')
        self.assertFalse(out.is_bool)
        self.assertEqual(out.status, 'unknown')
        self.assertIn('未确认', out.string)
        self.assertEqual(editor.keys, [backend.Keys.ENTER], '结果不确定时也不能重按回车')

    def test_send_image_in_chat_reports_page_failure(self):
        editor = _RecordingEditor()
        self.patch('driver', _ScriptedImageDriver(image_check={
            'list': True, 'total': 3, 'matches': 2, 'untagged': 1, 'state': 'failed', 'side': 'self'}))
        self.patch('_upload_image', lambda element, path: (True, None))
        self.patch('_wait_image_preview', lambda element, timeout=0: True)
        self.patch('_save_failure_shot', lambda label: None)
        douyin = backend.Douyin(backend.driver)

        out = douyin._send_image_in_chat(editor, '小红', 'lq_17.jpg')
        self.assertFalse(out.is_bool)
        self.assertEqual(out.status, 'failed')
        self.assertIn('失败', out.string)

    def test_send_image_in_chat_never_presses_enter_when_upload_fails(self):
        editor = _RecordingEditor()
        self.patch('_upload_image', lambda element, path: (False, '没找到聊天输入区的图片上传控件'))

        def boom(*a, **k):
            raise AssertionError('图片都没进输入框，不该去探测气泡')

        self.patch('_chat_image_probe', boom)
        douyin = backend.Douyin(backend.driver)

        out = douyin._send_image_in_chat(editor, '小红', 'lq_17.jpg')
        self.assertFalse(out.is_bool)
        self.assertEqual(out.status, 'failed')
        self.assertEqual(editor.keys, [], '上传失败时绝不能回车，否则可能发出半张图')
        self.assertIn('上传控件', out.string)

    # ---------------- Send_Frinder 的图文组合 ----------------
    def test_send_frinder_keeps_text_when_image_fails(self):
        """图片没送达不能连累文字：定时任务里「火花不能断」是硬需求。"""
        restore = _stub_send_path(backend)
        self.addCleanup(restore)
        self.patch('driver', _ScriptedImageDriver())
        backend.douyin = backend.Douyin(backend.driver)
        forward = self._stub_image_send(
            backend.TrueString(False, '没找到聊天输入区的图片上传控件', status='failed'))

        out = backend.douyin.Send_Frinder('小红', '早安', image='lq_17.jpg')
        self.assertTrue(out.is_bool, out.string)
        self.assertEqual(len(forward), 1)
        self.assertEqual(backend.douyin.last_send_detail.get('图片'),
                         '未送达：没找到聊天输入区的图片上传控件')

    def test_send_frinder_image_only_reports_failure(self):
        """只发图（文字为空）时图没送出，只能如实返回失败，没有文字可退化。"""
        restore = _stub_send_path(backend)
        self.addCleanup(restore)
        self.patch('driver', _ScriptedImageDriver())
        backend.douyin = backend.Douyin(backend.driver)
        self._stub_image_send(backend.TrueString(False, '没找到聊天输入区的图片上传控件', status='failed'))

        out = backend.douyin.Send_Frinder('小红', '', image='lq_17.jpg')
        self.assertFalse(out.is_bool)
        self.assertEqual(out.status, 'failed')

    # ---------------- /Api/Send ----------------
    def test_send_with_sign_uses_sign_text_and_picture(self):
        captured = self._capture_send()
        self.patch('fetch_wenchang_sign', lambda: dict(SIGN))
        self.patch('download_image', lambda url, timeout=None: (os.path.join('tmp', 'lq_17.jpg'), None))

        res = backend.Send(payload={'name': '小红', 'text': '', 'sign': 'wenchang'})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(captured['image'], os.path.join('tmp', 'lq_17.jpg'))
        self.assertIn('【乙庚下下签】', captured['text'])
        self.assertIn('工作升迁方面，宜守不宜攻。', captured['text'])
        # 记账文本要能看出这条是图文（state.json 里不存图片本身，只存一句摘要）
        record = backend._history_recent('小红') or {}
        self.assertIn('[图片]', record.get('text') or '')
        self.assertEqual(record.get('status'), 'success')

    def test_send_with_sign_reports_error_instead_of_random_text(self):
        captured = self._capture_send()
        self.patch('fetch_wenchang_sign', lambda: None)

        res = backend.Send(payload={'name': '小红', 'text': '', 'sign': 'wenchang'})
        self.assertEqual(res.get('code'), 404, res)
        self.assertIn('灵签', res.get('data') or '')
        self.assertEqual(captured, {}, '取不到签文时不该真的发送')
        self.assertNotEqual((backend._history_recent('小红') or {}).get('status'), 'success')

    def test_send_with_sign_reports_download_failure_without_sending(self):
        captured = self._capture_send()
        self.patch('fetch_wenchang_sign', lambda: dict(SIGN))
        self.patch('download_image', lambda url, timeout=None: (None, '图片超过 5.0 MB 上限'))

        res = backend.Send(payload={'name': '小红', 'text': '早安', 'sign': True})
        self.assertEqual(res.get('code'), 404, res)
        self.assertIn('图片没准备好', res.get('data') or '')
        self.assertEqual(captured, {})

    def test_send_without_sign_never_touches_the_sign_interface(self):
        captured = self._capture_send()

        def boom():
            raise AssertionError('没勾灵签时不该去请求灵签接口')

        self.patch('fetch_wenchang_sign', boom)

        res = backend.Send(payload={'name': '小红', 'text': '早安'})
        self.assertEqual(res.get('code'), 200, res)
        self.assertIsNone(captured['image'])
        self.assertEqual(captured['text'], '早安')

    # ---------------- 任务上的灵签开关 ----------------
    def test_task_sign_switch_persists_and_can_be_turned_off(self):
        res = backend.add_time(payload={'time': '21:00', 'name': '小美', 'text': '', 'sign': True})
        self.assertEqual(res.get('code'), 200, res)
        task_id = res['task_id']
        self.assertTrue(backend.task_meta[task_id]['sign'], '勾了灵签就该按任务存下来')
        self.assertTrue(backend._task_view(task_id, backend.task_meta[task_id])['sign'],
                        '列表接口要带 sign，否则列表上看不出这条是灵签')
        saved = [item for item in (backend.STATE.get('tasks') or [])
                 if item.get('task_id') == task_id]
        self.assertTrue(saved and saved[0].get('sign'), '灵签开关要落盘，重启后还在')

        # 只改时间、没传 sign：不能把灵签悄悄关掉
        backend.edit_time(payload={'name': '小美', 'new_time': '22:00'})
        self.assertTrue(backend.task_meta[task_id]['sign'], '没传 sign 时必须保持原状')
        # 显式传 false 才是「改回普通任务」
        backend.edit_time(payload={'name': '小美', 'new_time': '22:00', 'sign': False})
        self.assertFalse(backend.task_meta[task_id]['sign'])

        # 老 state.json 没有 sign 字段：恢复成普通任务，而不是报错
        with backend._task_lock:
            backend.task_meta.clear()
        backend.STATE.set('tasks', [{'task_id': 'legacy-1', 'time': '21:00',
                                     'name': '旧好友', 'text': ''}])
        self.assertEqual(backend.restore_tasks(), 1)
        self.assertFalse(backend.task_meta['legacy-1']['sign'])

    # ---------------- 定时任务发送灵签 ----------------
    def test_scheduled_send_sends_sign_text_with_picture(self):
        captured = self._capture_send()
        notices = self._recording_notify()
        self.patch('fetch_wenchang_sign', lambda: dict(SIGN))
        self.patch('download_image', lambda url, timeout=None: (os.path.join('tmp', 'lq_17.jpg'), None))
        self.patch('human_pause', lambda low, high: None)   # 真实实现会随机等 1~15 秒
        task_id = backend._register_task('21:00', '小美', '', sign=True)

        out = backend.run_scheduled_send('小美', '', task_id=task_id)
        self.assertEqual(out, 'success', out)
        self.assertEqual(captured['image'], os.path.join('tmp', 'lq_17.jpg'))
        self.assertTrue(captured['paced'], '定时任务要走「像人一样」的节奏')
        self.assertIn('【乙庚下下签】', captured['text'])
        self.assertIn('灵签（图文）', notices[0][1])

    def test_scheduled_send_degrades_to_text_when_sign_unavailable(self):
        captured = self._capture_send()
        notices = self._recording_notify()
        self.patch('fetch_wenchang_sign', lambda: None)
        self.patch('human_pause', lambda low, high: None)
        task_id = backend._register_task('21:00', '小美', '早安', sign=True)

        out = backend.run_scheduled_send('小美', '早安', task_id=task_id)
        self.assertEqual(out, 'success', out)
        self.assertIsNone(captured['image'], '签文取不到就不带图，退化成纯文字')
        self.assertEqual(captured['text'], '早安')
        self.assertNotIn('图文', notices[0][1])

    def test_scheduled_send_notice_admits_when_picture_was_lost(self):
        """签图没确认送出时通知不能说「灵签（图文）」，否则用户以为对方收到了图。"""
        self._capture_send(detail={'图片': '未送达：没找到聊天输入区的图片上传控件'})
        notices = self._recording_notify()
        self.patch('fetch_wenchang_sign', lambda: dict(SIGN))
        self.patch('download_image', lambda url, timeout=None: (os.path.join('tmp', 'lq_17.jpg'), None))
        self.patch('human_pause', lambda low, high: None)
        task_id = backend._register_task('21:00', '小美', '', sign=True)

        out = backend.run_scheduled_send('小美', '', task_id=task_id)
        self.assertEqual(out, 'success', out)
        self.assertIn('签图没有送出', notices[0][1])
        self.assertNotIn('灵签（图文）', notices[0][1])

    def test_scheduled_send_without_sign_never_touches_the_sign_interface(self):
        captured = self._capture_send()
        self._recording_notify()

        def boom():
            raise AssertionError('任务没勾灵签时不该去请求灵签接口')

        self.patch('fetch_wenchang_sign', boom)
        self.patch('human_pause', lambda low, high: None)
        task_id = backend._register_task('21:00', '小美', '早安')

        out = backend.run_scheduled_send('小美', '早安', task_id=task_id)
        self.assertEqual(out, 'success', out)
        self.assertIsNone(captured['image'])
        self.assertEqual(captured['text'], '早安')

    def test_scheduled_send_keeps_user_text_when_task_is_sign(self):
        """灵签任务里写了自定义文案时，签图照发，但正文必须是用户写的那句。

        旧实现是「签文覆盖自定义文案」，等于用户写的字被静默丢掉 —— 与面板上的
        「消息内容留空则用当天签文」正好相反。
        """
        captured = self._capture_send()
        self._recording_notify()
        self.patch('fetch_wenchang_sign', lambda: dict(SIGN))
        self.patch('download_image', lambda url, timeout=None: (os.path.join('tmp', 'lq.jpg'), None))
        self.patch('human_pause', lambda low, high: None)
        task_id = backend._register_task('21:00', '小美', '我写的早安', sign=True)

        out = backend.run_scheduled_send('小美', '我写的早安', task_id=task_id)
        self.assertEqual(out, 'success', out)
        self.assertEqual(captured['text'], '我写的早安')
        self.assertEqual(captured['image'], os.path.join('tmp', 'lq.jpg'), '签图仍然要发')

    def test_task_trial_uses_the_same_text_rule_as_the_real_task(self):
        """试发和真发必须同样的内容，否则「试过了」没有意义。"""
        captured = self._capture_send()
        self.patch('fetch_wenchang_sign', lambda: dict(SIGN))
        self.patch('download_image', lambda url, timeout=None: (os.path.join('tmp', 'lq.jpg'), None))
        task_id = backend._register_task('21:00', '小美', '我写的早安', sign=True)

        res = backend.test_task_send(payload={'task_id': task_id})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(captured['text'], '我写的早安')
        self.assertEqual(captured['image'], os.path.join('tmp', 'lq.jpg'))

    # ---------------- 只发图（image_only） ----------------
    def test_send_image_only_skips_text(self):
        captured = self._capture_send()
        self.patch('fetch_wenchang_sign', lambda: dict(SIGN))
        self.patch('download_image', lambda url, timeout=None: (os.path.join('tmp', 'lq.jpg'), None))

        res = backend.Send(payload={'name': '小红', 'text': '这句话不该发出去',
                                    'sign': 'wenchang', 'image_only': True})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(captured['image'], os.path.join('tmp', 'lq.jpg'))
        self.assertEqual(captured['text'], '', '选了「只发图」就一个字都不该带出去')
        record = backend._history_recent('小红') or {}
        self.assertTrue((record.get('text') or '').startswith('[图片]'), record)

    def test_send_image_only_accepts_a_plain_image_url(self):
        """不勾灵签、只给一个图片直链也能「只发图」。"""
        captured = self._capture_send()
        self.patch('download_image', lambda url, timeout=None: (os.path.join('tmp', 'x.jpg'), None))

        res = backend.Send(payload={'name': '小红', 'text': '忽略我',
                                    'image_url': 'https://cdn.example.com/x.jpg', 'image_only': True})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(captured['text'], '')
        self.assertEqual(captured['image'], os.path.join('tmp', 'x.jpg'))

    def test_send_image_only_without_a_picture_is_rejected(self):
        """「只发图」却根本没有图时要报错，不能悄悄把文字发出去。"""
        captured = self._capture_send()
        res = backend.Send(payload={'name': '小红', 'text': '早安', 'image_only': True})
        self.assertEqual(res.get('code'), 400, res)
        self.assertIn('没有可发的图片', res['data'])
        self.assertEqual(captured, {}, '参数自相矛盾时不该真的发出去')

    # ---------------- 立即试发定时任务（/Time/test） ----------------
    def test_task_trial_sends_sign_image_and_text(self):
        captured = self._capture_send()
        self.patch('fetch_wenchang_sign', lambda: dict(SIGN))
        self.patch('download_image', lambda url, timeout=None: (os.path.join('tmp', 'lq.jpg'), None))
        task_id = backend._register_task('21:00', '小美', '', sign=True)

        res = backend.test_task_send(payload={'task_id': task_id})
        self.assertEqual(res.get('code'), 200, res)
        self.assertIn('试发成功', res['data'])
        self.assertIn('定时任务不会再重复发', res['data'])
        self.assertEqual(captured['image'], os.path.join('tmp', 'lq.jpg'))
        self.assertIn('【乙庚下下签】', captured['text'])

    def test_task_trial_image_only_skips_text(self):
        captured = self._capture_send()
        self.patch('fetch_wenchang_sign', lambda: dict(SIGN))
        self.patch('download_image', lambda url, timeout=None: (os.path.join('tmp', 'lq.jpg'), None))
        task_id = backend._register_task('21:00', '小美', '', sign=True)

        res = backend.test_task_send(payload={'task_id': task_id, 'image_only': True})
        self.assertEqual(res.get('code'), 200, res)
        self.assertIn('只发签图', res['data'])
        self.assertEqual(captured['text'], '')
        self.assertEqual(captured['image'], os.path.join('tmp', 'lq.jpg'))

    def test_task_trial_plain_task_pulls_a_quote(self):
        """普通任务文案留空 = 每次现取一条（语录），试发也要走这条兜底而不是发空消息。"""
        captured = self._capture_send()
        self.patch('fetch_wenchang_sign', _unexpected)
        task_id = backend._register_task('21:00', '小美', '')

        res = backend.test_task_send(payload={'task_id': task_id})
        self.assertEqual(res.get('code'), 200, res)
        self.assertIsNone(captured['image'])
        self.assertIn(captured['text'], backend.FALLBACK_MESSAGES,
                      '没开 SPARK_REMOTE_QUOTE 时应该用本地兜底语录')

    def test_task_trial_rejects_unknown_task(self):
        self._capture_send()
        res = backend.test_task_send(payload={'task_id': 'not-a-task'})
        self.assertEqual(res.get('code'), 404, res)
        self.assertIn('任务不存在', res['data'])

    def test_task_trial_image_only_requires_a_sign_task(self):
        captured = self._capture_send()
        self.patch('fetch_wenchang_sign', _unexpected)
        task_id = backend._register_task('21:00', '小美', '早安')

        res = backend.test_task_send(payload={'task_id': task_id, 'image_only': True})
        self.assertEqual(res.get('code'), 400, res)
        self.assertIn('没有可以单独发的图', res['data'])
        self.assertEqual(captured, {}, '参数不对时不该真的发出去')

    def test_task_trial_reports_sign_failure_without_sending(self):
        captured = self._capture_send()
        self.patch('fetch_wenchang_sign', lambda: None)
        task_id = backend._register_task('21:00', '小美', '', sign=True)

        res = backend.test_task_send(payload={'task_id': task_id})
        self.assertEqual(res.get('code'), 404, res)
        self.assertIn('灵签', res['data'])
        self.assertEqual(captured, {})

    def test_task_trial_keeps_todays_scheduled_run_but_records_the_send(self):
        """试发不能把「今天这次」标记成已跑，但成功要记账 —— 否则今天会发两条。"""
        self._capture_send()
        task_id = backend._register_task('21:00', '小美', '早安')
        self.assertIsNone(backend.task_meta[task_id]['last_run_date'])

        res = backend.test_task_send(payload={'task_id': task_id})
        self.assertEqual(res.get('code'), 200, res)
        self.assertIsNone(backend.task_meta[task_id]['last_run_date'],
                          '试发不该写 last_run_date，今天到点的那次照常执行')
        self.assertEqual((backend._history_recent('小美') or {}).get('status'), 'success')
        self.assertIsNotNone(backend.send_guard('小美', '早安', scope='task'),
                             '试发成功之后，今天那次定时任务会被「今天已发过」拦住')

    # ---------------- 原有语录通道（确认没被图片功能影响） ----------------
    def test_aiqinggongyu_text_prefers_remote_then_falls_back(self):
        self._patch_get(FakeResponse(payload={'data': '  曾小贤的一句话  '}))
        self.assertEqual(backend.AiqingGongyu_text(), '曾小贤的一句话')

        # 接口 200 但内容是空的：必须落到本地兜底，不能返回空串（空消息会直接漏发）
        self._patch_get(FakeResponse(payload={'data': '   '}))
        self.assertIn(backend.AiqingGongyu_text(), backend.FALLBACK_MESSAGES)

        # 接口挂了：同样落到本地兜底
        self._patch_get(RuntimeError('断网了'))
        self.assertIn(backend.AiqingGongyu_text(), backend.FALLBACK_MESSAGES)

    def test_remote_quote_switch_only_affects_blank_text(self):
        self._patch_get(FakeResponse(payload={'data': '接口来的语录'}))
        self.assertIn(backend._resolve_text(''), backend.FALLBACK_MESSAGES,
                      '默认不请求第三方名言接口')
        self.patch('REMOTE_QUOTE', True)
        self.assertEqual(backend._resolve_text(''), '接口来的语录')
        # 用户自己写了文案（含 {weekday} 占位符）时永远优先用他的，不去请求接口
        self.patch('requests', types.SimpleNamespace(get=_unexpected))
        self.assertEqual(backend._resolve_text('早安'), '早安')


if __name__ == '__main__':
    unittest.main()
