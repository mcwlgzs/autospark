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
            # 记下参数与实验选项：build_chrome_options() 的产物要被断言（例如
            # Windows 上的 --do-not-de-elevate），纯空实现就只能看源码猜了。
            self.arguments = []
            self.experimental_options = {}

        def add_argument(self, *a, **k):
            for value in a:
                self.arguments.append(value)

        def add_experimental_option(self, *a, **k):
            if len(a) >= 2:
                self.experimental_options[a[0]] = a[1]

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

# setUp() 会把 require_auth 换成「永远放行」的桩（好让别的用例直接调路由函数）。
# 想验证鉴权本身的用例必须先把真身存下来，否则测的是桩、不是代码。
_REAL_REQUIRE_AUTH = backend.require_auth


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

    # ---------- 消息记录（/Api/History/List） ----------
    def test_history_record_keeps_kind(self):
        """kind 要落盘并翻成中文：面板上要能看出是定时发的还是自己手点的。"""
        backend._history_mark('小美', '文案', 'success', kind='task')
        backend._history_mark('小刚', '文案', 'success', kind='test')
        rows = {row['name']: row for row in backend._history_rows()}
        self.assertEqual(rows['小美']['kind'], 'task')
        self.assertEqual(rows['小美']['kind_text'], '定时')
        self.assertEqual(rows['小刚']['kind_text'], '试发')

    def test_history_rows_parse_legacy_keys(self):
        """旧数据的键是「日期|好友|文案哈希」，也要能读出好友名字。"""
        backend.STATE.set('send_history', {
            '2026-09-14|宝宝|4085313ead': {'status': 'success', 'at': '2026-09-14 15:14:33'},
            '2026-09-15|小美': {'status': 'failed', 'at': '2026-09-15 09:00:00', 'detail': '超时'},
        })
        rows = backend._history_rows()
        by_name = {row['name']: row for row in rows}
        self.assertEqual(set(by_name), {'宝宝', '小美'})
        self.assertEqual(by_name['宝宝']['day'], '2026-09-14')
        self.assertEqual(by_name['宝宝']['kind_text'], '', '旧记录没有 kind，不该报错')
        self.assertEqual(by_name['小美']['status_text'], '失败')
        self.assertEqual(rows[0]['name'], '小美', '最新一条要排在最前面')

    def test_history_list_filters_but_stats_stay_global(self):
        backend.STATE.set('send_history', {
            backend.today_str() + '|小美': {'status': 'success', 'at': '2026-09-28 10:00:00',
                                            'text': '早安', 'kind': 'task'},
            backend.today_str() + '|小刚': {'status': 'failed', 'at': '2026-09-28 11:00:00',
                                            'text': '晚安', 'kind': 'manual'},
        })
        token = 'Bearer ' + backend.generate_token()
        result = backend.HistoryList(status='failed', authorization=token)
        self.assertEqual(result['code'], 200)
        payload = result['data']
        self.assertEqual(payload['total'], 1)
        self.assertEqual([row['name'] for row in payload['list']], ['小刚'])
        # 关键：统计不跟着筛选走，否则用户一筛「失败」就看不出整体情况
        self.assertEqual(payload['stats'], {'success': 1, 'failed': 1, 'unknown': 0})

        hit = backend.HistoryList(keyword='晚安', authorization=token)['data']
        self.assertEqual([row['name'] for row in hit['list']], ['小刚'])
        miss = backend.HistoryList(keyword='不存在的内容', authorization=token)['data']
        self.assertEqual(miss['total'], 0)
        self.assertEqual(miss['list'], [])

    def test_history_list_requires_auth(self):
        """没 token / 假 token 一律 401，真 token 才放行。

        注意 setUp 把 require_auth 换成了放行的桩，这里临时换回真身。
        """
        stubbed = backend.require_auth
        backend.require_auth = _REAL_REQUIRE_AUTH
        try:
            self.assertEqual(backend.HistoryList()['code'], 401)
            self.assertEqual(backend.HistoryList(authorization='Bearer 不是token')['code'], 401)
            self.assertEqual(backend.HistoryList(authorization='不是Bearer前缀')['code'], 401)
            token = backend.generate_token()
            self.assertEqual(backend.HistoryList(authorization='Bearer ' + token)['code'], 200)
        finally:
            backend.require_auth = stubbed

    # ---------- 安全中心（/Api/Security/Overview、/Api/Security/RevokeAll） ----------
    @staticmethod
    def _fake_request(peer='127.0.0.1', headers=None, scheme='http'):
        """_security_checks 只用到 client.host / headers / url.scheme，给个最小替身即可。

        不直接用 starlette 的 Request，是因为这套测试要能在没装后端依赖的机器上跑。
        """
        return types.SimpleNamespace(
            client=types.SimpleNamespace(host=peer),
            headers=dict(headers or {}),
            url=types.SimpleNamespace(scheme=scheme),
        )

    def test_security_checks_cover_all_groups(self):
        """20 项检查一个都不能少，id 不能重复，分组与状态值必须是约定的那几个。"""
        checks, facts = backend._security_checks(self._fake_request())
        ids = [item['id'] for item in checks]
        self.assertEqual(len(ids), len(set(ids)), '检查项 id 不能重复')
        for expected in ('panel_password', 'panel_sessions', 'login_throttle', 'listen_scope',
                         'transport', 'cors', 'power_endpoints', 'douyin_session',
                         'fingerprint', 'automation_engine', 'risk_detect',
                         'anti_detection', 'send_pause', 'cookie_expiry', 'pacing',
                         'daily_volume', 'state_file', 'profile_dir', 'app_log', 'runtime'):
            self.assertIn(expected, ids, '少了检查项：%s' % expected)
        for item in checks:
            self.assertIn(item['group'], ('面板', '账号', '防封号', '数据', '运行时'), item['id'])
            self.assertIn(item['status'], ('ok', 'info', 'warn', 'risk'), item['id'])
            self.assertTrue(item['title'], item['id'])
            self.assertTrue(item['detail'], item['id'])
        self.assertIn('browser_ready', facts)
        self.assertIn('stealth_injected', facts)
        self.assertEqual(facts['bind_host'], backend.BIND_HOST)

    def test_security_overview_score_math(self):
        """评分口径：100 扣掉各项罚分，level 与 score 的分档必须自洽。"""
        result = backend.SecurityOverview(self._fake_request())
        self.assertEqual(result['code'], 200)
        data = result['data']
        self.assertEqual(sum(data['counts'].values()), len(data['checks']),
                         'counts 是 checks 的重新计数，不能对不上')
        penalty = sum(backend.SECURITY_STATUS_PENALTY.get(item['status'], 0)
                      for item in data['checks'])
        self.assertEqual(data['score'], max(0, 100 - penalty))
        expected = 'good' if data['score'] >= 85 else ('warn' if data['score'] >= 60 else 'risk')
        self.assertEqual(data['level'], expected)

    def test_panel_password_check_matches_stored_hash(self):
        """三种密码状态要和「有没有存哈希 / 是不是 PBKDF2」严格对应。

        只读不写：state.json 里可能是用户真实的密码哈希，测试绝不能改它。
        """
        checks, _ = backend._security_checks(self._fake_request())
        item = next(c for c in checks if c['id'] == 'panel_password')
        stored = backend._stored_password_hash()
        if not stored:
            self.assertEqual(item['status'], 'risk', '没设自定义密码必须报风险')
        elif str(stored).startswith('pbkdf2_sha256$'):
            self.assertEqual(item['status'], 'ok')
        else:
            self.assertEqual(item['status'], 'warn', '旧版无盐 SHA-256 只能报注意')

    def test_human_size_units(self):
        self.assertEqual(backend._human_size(512), '512 B')
        self.assertEqual(backend._human_size(2048), '2.0 KB')
        self.assertEqual(backend._human_size(1024 * 1024 * 3), '3.0 MB')
        self.assertEqual(backend._human_size('abc'), '未知')

    def test_security_routes_require_auth(self):
        """没 token / 假 token 一律 401（setUp 的放行桩这里要临时换成真身）。"""
        stubbed = backend.require_auth
        backend.require_auth = _REAL_REQUIRE_AUTH
        try:
            self.assertEqual(backend.SecurityOverview(self._fake_request())['code'], 401)
            self.assertEqual(
                backend.SecurityOverview(self._fake_request(), 'Bearer 不是token')['code'], 401)
            self.assertEqual(backend.SecurityRevokeAll()['code'], 401)
            self.assertEqual(
                backend.SecurityRevokeAll('Bearer 不是token', {'password': 'x'})['code'], 401)
            token = backend.generate_token()
            self.assertEqual(
                backend.SecurityOverview(self._fake_request(), 'Bearer ' + token)['code'], 200)
        finally:
            backend.require_auth = stubbed

    def test_security_revoke_all_needs_password(self):
        """只凭 token 不能踢掉全部会话——这是「怀疑密码泄漏」时才按的按钮。"""
        original_book = backend._valid_tokens
        original_check = backend.check_password
        original_log = backend.log_event
        book = backend.TokenBook()
        backend._valid_tokens = book
        backend.check_password = lambda pw: pw == '测试密码'
        backend.log_event = lambda *a, **k: None      # 别把测试写进真实日志
        try:
            token = backend.generate_token()
            self.assertEqual(
                backend.SecurityRevokeAll('Bearer ' + token, {'password': '不是密码'})['code'], 400)
            self.assertEqual(len(book), 1, '密码不对时不能吊销任何会话')

            result = backend.SecurityRevokeAll('Bearer ' + token, {'password': '测试密码'})
            self.assertEqual(result['code'], 200)
            self.assertEqual(result['data']['revoked'], 1)
            self.assertEqual(len(book), 0)
            self.assertFalse(backend.verify_token(token), '被吊销的 token 不能再通过校验')
        finally:
            backend._valid_tokens = original_book
            backend.check_password = original_check
            backend.log_event = original_log

    # ---------- 抖音账号（/Api/Account/Info、/Api/Account/Note） ----------
    def test_account_info_reports_unlogged_state(self):
        """浏览器没起、没登录时，账号页必须如实说「未登录」，不能编 cookie 状态。"""
        original = backend.require_auth
        backend.require_auth = lambda authorization=None: None
        try:
            result = backend.AccountInfo()
        finally:
            backend.require_auth = original
        self.assertEqual(result['code'], 200)
        data = result['data']
        for key in ('cookie_status', 'cookie_text', 'friend_count', 'friend_count_known',
                    'sent_today', 'cookie_expire', 'note', 'added_at', 'engine', 'profile_dir'):
            self.assertIn(key, data)
        self.assertFalse(data['logged_in'])
        self.assertEqual(data['cookie_status'], 'unlogged')
        self.assertFalse(data['friend_count_known'], '没抓过好友列表就不能报一个数字')

    def test_account_note_roundtrip_and_length_limit(self):
        """备注要能存下来再读出来；超长要被后端挡住（前端校验不能算数）。"""
        original = backend.require_auth
        original_log = backend.log_event
        backend.require_auth = lambda authorization=None: None
        backend.log_event = lambda *a, **k: None      # 别把测试写进真实日志
        try:
            saved = backend.AccountSetNote(None, {'note': '  主号  '})
            self.assertEqual(saved['code'], 200)
            self.assertEqual(saved['data']['note'], '主号', '两端空白要修掉')
            self.assertEqual(backend.AccountInfo()['data']['note'], '主号', '备注要落盘并能读回')

            too_long = backend.AccountSetNote(
                None, {'note': 'x' * (backend.ACCOUNT_NOTE_MAX + 1)})
            self.assertEqual(too_long['code'], 400)
            self.assertEqual(backend.AccountInfo()['data']['note'], '主号', '被拒的备注不能改写旧值')

            self.assertEqual(backend.AccountSetNote(None, {'note': ''})['code'], 200)
            self.assertEqual(backend.AccountInfo()['data']['note'], '')
        finally:
            backend.require_auth = original
            backend.log_event = original_log

    def test_account_routes_require_auth(self):
        """账号接口必须鉴权：返回值里带着 Cookie 名和登录态目录。"""
        stubbed = backend.require_auth
        backend.require_auth = _REAL_REQUIRE_AUTH
        try:
            self.assertEqual(backend.AccountInfo()['code'], 401)
            self.assertEqual(backend.AccountInfo('Bearer 不是token')['code'], 401)
            self.assertEqual(backend.AccountSetNote('Bearer 不是token', {'note': 'x'})['code'], 401)
            token = backend.generate_token()
            self.assertEqual(backend.AccountInfo('Bearer ' + token)['code'], 200)
        finally:
            backend.require_auth = stubbed

    # ---------- 发送预检：隔着好几屏的好友也要能找到 ----------
    def _precheck_env(self, screen, catalog, scroll_result='found'):
        """搭一个「当前画面 vs 全量会话」对不上的预检环境。

        真实数据（用户 m02066 现场）：会话列表 200+ 条，虚拟滚动只挂几十行，
        Updara_FrinderList 结束时停在列表底部，于是 friends_xpath_list 里只有底部那批人，
        而好友列表页给人看的是滚动过程中见过的全部好友（friend_catalog）。
        返回 (恢复函数, 被滚动查找过的名字列表)。
        """
        backend.driver = _FakeDriver(script_result=[])
        douyin = backend.Douyin(backend.driver)
        douyin.friends_xpath_list = dict(screen)
        douyin.friend_catalog = [{'name': n, 'key': 'k%d' % i, 'avatar': '', 'fire': ''}
                                 for i, n in enumerate(catalog)]
        douyin.friend_ambiguous = set()
        backend.douyin = douyin
        # 真实的 Updara_FrinderList 会重建这两个映射（也会把这里的夹具清空），
        # 而夹具本身就是「它跑完之后剩下的样子」，所以这里替换成空操作。
        douyin.Updara_FrinderList = lambda: []
        scrolled = []

        def fake_scroll(name, max_steps=60):
            scrolled.append(name)
            if scroll_result == 'found':
                douyin.friends_xpath_list[name] = '//stub/found'

        douyin._scroll_until_friend = fake_scroll
        originals = {
            'driver': backend.driver,
            '_open_conversation': backend._open_conversation,
            '_wait_chat_editor': backend._wait_chat_editor,
            '_chat_probe': backend._chat_probe,
        }
        backend._open_conversation = lambda name, xpath, timeout=8: (True, '')
        backend._wait_chat_editor = lambda timeout=10: object()
        backend._chat_probe = lambda text, mode: {'list': True}

        def restore():
            for key, value in originals.items():
                setattr(backend, key, value)

        return restore, scrolled

    def test_send_check_scrolls_to_find_friend_off_screen(self):
        """预检不能只看「当前画面」里那几十行。

        缺陷现场：好友在列表顶部，而 Updara_FrinderList 结束时停在底部，
        用户从好友列表里点了这位好友，预检直接说「会话列表里没有」。
        发消息那边一直会滚回去找人，预检漏了这一步。
        """
        restore, scrolled = self._precheck_env(
            screen={'Lotus': '//stub/bottom'}, catalog=['Lotus', 'MK空白'])
        try:
            out = backend.SendCheck(payload={'name': 'MK空白'})
        finally:
            restore()
        self.assertEqual(out['code'], 200, out)
        self.assertEqual(scrolled, ['MK空白'], '不在当前画面里时必须滚下去找一遍')

    def test_send_check_reports_full_catalog_size(self):
        """「共读到 N 个」要用整份列表，不是当前画面那几十行 —— 否则数字看着像读错了。"""
        restore, _scrolled = self._precheck_env(
            screen={'Lotus': '//stub/bottom'},
            catalog=['Lotus', '白棂若清', '见面鱼块'], scroll_result='missing')
        try:
            out = backend.SendCheck(payload={'name': '查无此人'})
        finally:
            restore()
        self.assertEqual(out['code'], 404)
        self.assertIn('共读到 3 个', out['data'])
        self.assertIn('请在好友列表里刷新', out['data'])

    def test_send_check_says_when_friend_exists_but_unlocated(self):
        """好友确实在列表里、只是没滚到 —— 要如实说，不能报成「会话列表里没有这个人」。"""
        restore, _scrolled = self._precheck_env(
            screen={'Lotus': '//stub/bottom'},
            catalog=['Lotus', '白棂若清'], scroll_result='missing')
        try:
            out = backend.SendCheck(payload={'name': '白棂若清'})
        finally:
            restore()
        self.assertEqual(out['code'], 404)
        self.assertIn('没定位到', out['data'])
        self.assertNotIn('会话列表里没有', out['data'])

    # ---------- 会话标题识别（子串选择器会命中外层容器） ----------
    def test_chat_open_name_js_reads_the_real_title_not_the_container(self):
        """[class*="RightPanelHeadertitle"] 会先命中 RightPanelHeadertitleContainer。

        实测取证（2026-09-28 真实页面）：
          RightPanelHeadertitleContainer.innerText == 'MK空白 699'   ← 被先命中的那个
          RightPanelHeadertitle.innerText          == 'MK空白'       ← 真正的标题
        旧代码拿容器文本做严格相等比较，于是会话明明已经打开也判成「没打开」，
        预检和发送前的「确认打开的就是本人」守卫一起失效（带火花的会话必然失败）。
        """
        code = '\n'.join(line for line in backend.CHAT_OPEN_NAME_JS.splitlines()
                         if not line.strip().startswith('//'))
        self.assertIn('[class~="RightPanelHeadertitle"]', code,
                      '必须用 CSS 整词匹配 ~=，子串选择器会命中外层容器')
        self.assertLess(code.index('[class~="RightPanelHeadertitle"]'),
                        code.index('[class*="RightPanelHeadertitle"]'),
                        '整词选择器必须排在子串选择器前面')
        self.assertIn(r'/\s+\d+$/', code,
                      '兜底读到容器时，只允许剥掉尾巴上「空格+火花数」这一种附加内容')

    # ---------- 清空消息记录（/Api/History/Clear） ----------
    def test_history_clear_keeps_today_and_removes_old(self):
        """scope='old' 只能清今天以前的：今天的记录是防重复发送的依据。"""
        today = backend.today_str()
        old_day = (self.now - timedelta(days=2)).strftime('%Y-%m-%d')
        backend.STATE.set('send_history', {
            old_day + '|小美': {'status': 'success', 'at': old_day + ' 09:00:00'},
            old_day + '|小刚|abc123': {'status': 'failed', 'at': old_day + ' 10:00:00'},
            today + '|小红': {'status': 'success', 'at': today + ' 08:00:00'},
        })
        original_log = backend.log_event
        backend.log_event = lambda *a, **k: None       # 别把测试写进真实日志
        try:
            result = backend.HistoryClear(payload={'scope': 'old'})
        finally:
            backend.log_event = original_log

        self.assertEqual(result['code'], 200, result)
        self.assertEqual(result['data'], {'removed': 2, 'kept': 1, 'scope': 'old'})
        history = backend.STATE.get('send_history')
        self.assertEqual(set(history), {today + '|小红'}, '旧记录要删干净、今天的一条要留住')
        # 留住的那条必须仍然拦得住当天重复发送，否则清空就拆了去重
        self.assertIsNotNone(backend.send_guard('小红', '早安', scope='task'))
        self.assertIsNone(backend.send_guard('小美', '早安', scope='task'))

        # 不传 scope 时默认走 'old'，而不是「什么都没清」或「全清」
        backend.STATE.set('send_history', {
            old_day + '|小美': {'status': 'success', 'at': old_day + ' 09:00:00'},
            today + '|小红': {'status': 'success', 'at': today + ' 08:00:00'},
        })
        backend.log_event = lambda *a, **k: None
        try:
            defaulted = backend.HistoryClear()
        finally:
            backend.log_event = original_log
        self.assertEqual(defaulted['data'], {'removed': 1, 'kept': 1, 'scope': 'old'})

        # 非法 scope 要被挡住，且一条都不许删
        backend.log_event = lambda *a, **k: None
        try:
            bad = backend.HistoryClear(payload={'scope': 'yesterday'})
        finally:
            backend.log_event = original_log
        self.assertEqual(bad, {'code': 400, 'data': '清空范围不支持，只支持 old 或 all'})
        self.assertEqual(set(backend.STATE.get('send_history')), {today + '|小红'})

    def test_history_clear_all_removes_everything(self):
        """scope='all' 连今天一起清 —— 前端会对这一步做明确警告。"""
        today = backend.today_str()
        backend.STATE.set('send_history', {
            today + '|小红': {'status': 'success', 'at': today + ' 08:00:00'},
            today + '|小刚': {'status': 'unknown', 'at': today + ' 09:00:00'},
        })
        original_log = backend.log_event
        backend.log_event = lambda *a, **k: None
        try:
            result = backend.HistoryClear(payload={'scope': 'all'})
        finally:
            backend.log_event = original_log

        self.assertEqual(result['code'], 200, result)
        self.assertEqual(result['data'], {'removed': 2, 'kept': 0, 'scope': 'all'})
        self.assertEqual(backend.STATE.get('send_history'), {}, 'all 必须清空 send_history')

    def test_history_clear_requires_auth(self):
        """清空是破坏性操作：没 token / 假 token 一律 401，且一条都不许真删。"""
        today = backend.today_str()
        backend.STATE.set('send_history', {
            today + '|小红': {'status': 'success', 'at': today + ' 08:00:00'},
        })
        stubbed = backend.require_auth
        original_log = backend.log_event
        backend.require_auth = _REAL_REQUIRE_AUTH      # setUp 装的是「永远放行」的桩
        backend.log_event = lambda *a, **k: None
        try:
            for bad in (None, 'Bearer 不是token', '不是Bearer前缀'):
                result = backend.HistoryClear(payload={'scope': 'all'}, authorization=bad)
                self.assertEqual(result.get('code'), 401, result)
            self.assertIn(today + '|小红', backend.STATE.get('send_history'),
                          '未授权时绝不能真删掉记录')

            token = backend.generate_token()
            ok = backend.HistoryClear(payload={'scope': 'all'},
                                      authorization='Bearer ' + token)
            self.assertEqual(ok['code'], 200, ok)
            self.assertEqual(backend.STATE.get('send_history'), {},
                             '真 token 应当能清空')
        finally:
            backend.require_auth = stubbed
            backend.log_event = original_log

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
    """把 backend.py 解析成「一个路由一段」，记录它是否挂了 @serialized。

    这里必须用 ast 而不是按「下一个 @app. 的位置」正则切块。正则版本会把夹在
    两个路由之间的**顶层辅助函数**算进前一个路由的 body：安全中心的 helper
    `_security_checks()` 里就有 `driver.get_cookies()`，它紧跟在
    `/Api/Logs/Clear` 后面，于是正则版本误报
    「/Api/Logs/Clear 会碰浏览器却没有串行化保护」。ast 直接给出每个函数
    自己的源码段，路由和 helper 从此不会串味。
    """
    import ast
    import re
    blocks = {}
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        path = None
        serialized = False
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Name) and decorator.id == 'serialized':
                serialized = True
                continue
            if isinstance(decorator, ast.Call) and getattr(decorator.func, 'id', '') == 'serialized':
                serialized = True
                continue
            match = re.match(r"app\.(?:get|post|put|delete)\('([^']+)'\)", ast.unparse(decorator))
            if match:
                path = match.group(1)
        if path is None:
            continue
        body = ast.get_source_segment(source, node) or ''
        blocks[path] = {
            'serialized': serialized,
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

    # ---------------- 抖音的「确认发送文件」浮层 ----------------
    def _patch_send_file_modal(self, present=True, clicked=True, closes=True,
                               title='发送给 MK空白'):
        """把「确认发送文件」浮层换成受控替身。

        返回 (state, calls)。state['open'] = 浮层当前是否还开着（点成功后抖音会自己关掉）；
        calls 记录每次调用的 mode，用来断言「点的是发送、失败时点的是取消」。
        """
        state = {'open': bool(present)}
        calls = []

        def fake(mode='check'):
            calls.append(mode)
            if mode == 'check':
                return {'present': state['open'], 'title': title}
            if mode == 'confirm':
                if clicked and closes:
                    state['open'] = False
                return {'present': True, 'clicked': bool(clicked)}
            state['open'] = False
            return {'present': True, 'clicked': True}

        self.patch('_send_file_modal', fake)
        return state, calls

    def test_send_image_clicks_douyin_send_file_modal_instead_of_pressing_enter(self):
        """真机 bug：上传图片后抖音弹「确认发送文件」浮层，此时按回车什么都不会发生。

        2026-09-28 的现场：浮层是 .MsgInputSendFileModalbox（「发送给 MK空白：xxx.jpg 57.4 KB」，
        按钮 .MsgInputSendFileModalbtnSure / ...btnCancle）。当时只对输入框按了回车，
        于是图片一直挂在浮层里没发出去，25 秒后判成「未确认」；紧接着退化发文字时，
        输入框又被这个模态层盖住，报出「消息内容没有写进输入框」。
        """
        editor = _RecordingEditor()
        self.patch('driver', _ScriptedImageDriver(image_matches_before=1))
        self.patch('_upload_image', lambda element, path: (True, None))
        self.patch('_wait_image_preview', lambda element, timeout=0: True)
        _, calls = self._patch_send_file_modal()
        douyin = backend.Douyin(backend.driver)

        out = douyin._send_image_in_chat(editor, 'MK空白', 'lq_17.jpg')

        self.assertTrue(out.is_bool, out.string)
        self.assertIn('confirm', calls, '必须点浮层里的「发送」，回车在这个浮层上是无效动作')
        self.assertEqual(editor.keys, [], '浮层在的时候不该按回车')
        self.assertEqual(douyin.last_send_detail.get('确认步骤'), '发送给 MK空白')

    def test_send_image_reports_failure_when_send_file_modal_unreachable(self):
        """点不到浮层的「发送」就如实失败，并且把浮层取消掉（否则输入框一直被盖住）。"""
        editor = _RecordingEditor()
        self.patch('driver', _ScriptedImageDriver())
        self.patch('_upload_image', lambda element, path: (True, None))
        _, calls = self._patch_send_file_modal(clicked=False)

        def boom(*a, **k):
            raise AssertionError('浮层都没点掉，不该去确认气泡')

        self.patch('_confirm_message_delivered', boom)
        douyin = backend.Douyin(backend.driver)

        out = douyin._send_image_in_chat(editor, 'MK空白', 'lq_17.jpg')

        self.assertFalse(out.is_bool)
        self.assertEqual(out.status, 'failed')
        self.assertIn('确认发送文件', out.string)
        self.assertIn('cancel', calls, '点不到发送时也要清场，不能留着模态层堵输入框')
        self.assertEqual(editor.keys, [])

    def test_send_image_cancels_leftover_modal_so_text_can_still_be_typed(self):
        """图没发出去但浮层还开着时，必须在返回前关掉它 —— 这是文字写不进输入框的直接原因。"""
        editor = _RecordingEditor()
        self.patch('driver', _ScriptedImageDriver(image_matches_before=1, image_check={
            'list': True, 'total': 2, 'matches': 1, 'untagged': 1, 'state': 'clean', 'side': 'self'}))
        self.patch('_upload_image', lambda element, path: (True, None))
        self.patch('_wait_image_preview', lambda element, timeout=0: True)
        self.patch('_save_failure_shot', lambda label: None)
        self.patch('IMAGE_CONFIRM_TIMEOUT', 0.05)
        # 点了「发送」但抖音没关浮层（closes=False）→ 走完确认流程后必须由我们取消
        state, calls = self._patch_send_file_modal(closes=False)
        douyin = backend.Douyin(backend.driver)

        out = douyin._send_image_in_chat(editor, 'MK空白', 'lq_17.jpg')

        self.assertFalse(out.is_bool)
        self.assertEqual(out.status, 'unknown')
        self.assertIn('cancel', calls, '留着浮层会让后续的文字发送必然失败')
        self.assertFalse(state['open'], '返回时浮层必须是关掉的')

    def test_send_image_still_presses_enter_when_no_modal_appears(self):
        """老版式没有浮层，回车发送这条路必须保留（否则图片永远发不出去）。"""
        editor = _RecordingEditor()
        self.patch('driver', _ScriptedImageDriver(image_matches_before=1))
        self.patch('_upload_image', lambda element, path: (True, None))
        self.patch('_wait_image_preview', lambda element, timeout=0: True)
        self._patch_send_file_modal(present=False)
        douyin = backend.Douyin(backend.driver)

        out = douyin._send_image_in_chat(editor, '小红', 'lq_17.jpg')

        self.assertTrue(out.is_bool, out.string)
        self.assertEqual(editor.keys, [backend.Keys.ENTER], '没有浮层时要按回车发送')

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


class EditorTypingTestCase(unittest.TestCase):
    """输入框写入。

    真机取证（2026-09-28）：新版抖音聊天框是 Ace 系编辑器（每行一个 div.ace-line），
    每行末尾都多一个零宽空格，于是「明明是写进去了」的严格子串比较必然为假；
    代码以为没写进去，换第二种方式再写一遍 —— 内容越写越乱，最后报「消息内容没有写进输入框」。
    这里把两条底线钉住：比较必须拉平、写入必须走真输入（且绝不按回车）。
    """

    TEXT = '第一行\n第二行'

    def setUp(self):
        self._originals = (backend.driver, backend._chat_editor_text)
        self.cdp_calls = []
        self.script_calls = []
        self.send_keys_calls = []

    def tearDown(self):
        backend.driver, backend._chat_editor_text = self._originals

    def _install(self, before='', after=None, cdp_error=False):
        """装一个假 driver。只有「真的写了内容」之后 _chat_editor_text 才返回 after。"""
        after = self.TEXT.replace('\n', '\u200b\n') + '\u200b' if after is None else after
        state = {'written': False}
        cdp_calls, script_calls = self.cdp_calls, self.script_calls

        class _Driver:
            def execute_script(self, script, *args):
                script_calls.append(script)
                if 'insertText' in script or 'textContent' in script:
                    state['written'] = True
                return None

            def execute_cdp_cmd(self, command, params):
                if cdp_error:
                    raise RuntimeError('CDP 不可用')
                cdp_calls.append((command, params))
                state['written'] = True
                return {}

        self.editor = types.SimpleNamespace(
            send_keys=lambda value: self.send_keys_calls.append(value))
        backend.driver = _Driver()
        backend._chat_editor_text = lambda element: after if state['written'] else before
        return state

    def test_editor_flatten_removes_zero_width_and_newlines(self):
        self.assertEqual(backend._editor_flatten('甲\u200b\n乙'), '甲乙')
        self.assertEqual(backend._editor_flatten(''), '')

    def test_editor_contains_survives_ace_zero_width_per_line(self):
        self._install(before='第一行\u200b\n第二行\u200b')
        self.assertTrue(backend._editor_contains(self.editor, self.TEXT))

    def test_editor_still_has_survives_ace_zero_width_per_line(self):
        self._install(before='第一行\u200b\n第二行\u200b')
        self.assertTrue(backend._editor_still_has(self.editor, self.TEXT))
        self.assertFalse(backend._editor_still_has(self.editor, '别的内容'))

    def test_type_into_editor_prefers_cdp_insert_text(self):
        self._install()
        self.assertTrue(backend._type_into_editor(self.editor, self.TEXT))
        self.assertEqual(self.cdp_calls, [('Input.insertText', {'text': self.TEXT})])
        self.assertEqual(self.send_keys_calls, [],
                         '绝不能用 send_keys：它碰到 \\n 会真按一次回车，把半条消息提前发出去')

    def test_type_into_editor_never_rewrites_content_already_there(self):
        self._install(before='第一行\u200b\n第二行\u200b')
        self.assertTrue(backend._type_into_editor(self.editor, self.TEXT))
        self.assertEqual(self.cdp_calls, [])
        self.assertEqual(self.script_calls, [])

    def test_type_into_editor_falls_back_to_exec_command_when_cdp_fails(self):
        self._install(cdp_error=True)
        self.assertTrue(backend._type_into_editor(self.editor, self.TEXT))
        self.assertEqual(self.cdp_calls, [])
        self.assertTrue(any('execCommand' in script for script in self.script_calls))


# ==================== 一言（hitokoto）内容来源 ====================
class HitokotoTestCase(unittest.TestCase):
    """任务的正文可以来自一言接口：取数、字段落盘、发送时解析与兜底、预览接口。

    全程不发真实网络请求：要么 patch backend.fetch_hitokoto，要么把 backend.requests
    换成返回给定响应的替身（_patch_get），连 HITOKOTO_URL 都指向本地桩地址。
    setUp 和助手直接借 ImageSendTestCase 的：继承会把它的用例在本类里再跑一遍，
    整个套件的耗时凭空翻倍（和 ImageSendTestCase 不继承 SchedulerTestCase 同一个理由）。
    """

    setUp = ImageSendTestCase.setUp
    tearDown = ImageSendTestCase.tearDown
    _patch_get = ImageSendTestCase._patch_get
    _capture_send = ImageSendTestCase._capture_send
    _recording_notify = ImageSendTestCase._recording_notify

    # ---------------- 取数 ----------------
    def _record_get(self, result):
        """把 backend.requests 换成会记录「请求了哪个地址、超时多少」的替身。"""
        calls = []

        def fake_get(url, *args, **kwargs):
            calls.append((url, kwargs.get('timeout')))
            if isinstance(result, Exception):
                raise result
            return result

        self.patch('requests', types.SimpleNamespace(get=fake_get))
        return calls

    def test_fetch_hitokoto_appends_the_source_and_author(self):
        """用户 m03008：发出去的要是『正文』—— 「来源 作者」，不再是光秃秃一句。"""
        self._patch_get(FakeResponse(payload={'hitokoto': '  今天也要好好吃饭  ',
                                              'from': '永远的七日之都', 'from_who': '璐璐'}))
        self.assertEqual(backend.fetch_hitokoto(),
                         '『今天也要好好吃饭』—— 「永远的七日之都 璐璐」')

    def test_fetch_hitokoto_handles_missing_source_or_author(self):
        """来源和作者都可能缺一个：缺谁不写谁，两个都缺就只发正文，不留空括号。"""
        self._patch_get(FakeResponse(payload={'hitokoto': '甲', 'from': '某作品', 'from_who': None}))
        self.assertEqual(backend.fetch_hitokoto(), '『甲』—— 「某作品」')
        self._patch_get(FakeResponse(payload={'hitokoto': '乙', 'from': None, 'from_who': '某人'}))
        self.assertEqual(backend.fetch_hitokoto(), '『乙』—— 「某人」')
        self._patch_get(FakeResponse(payload={'hitokoto': '丙'}))
        self.assertEqual(backend.fetch_hitokoto(), '丙',
                         '连来源都没有时只发正文，不能发出「—— 「」」')

    def test_fetch_hitokoto_uses_the_configured_url_with_a_short_timeout(self):
        """地址走环境变量、超时是短超时：第三方挂住时不能把线程池占死。"""
        self.patch('HITOKOTO_URL', 'http://127.0.0.1:9/stub-hitokoto')
        calls = self._record_get(FakeResponse(payload={'hitokoto': '你好'}))
        backend.fetch_hitokoto()
        self.assertEqual(calls, [('http://127.0.0.1:9/stub-hitokoto', backend.HITOKOTO_TIMEOUT)])

    def test_default_hitokoto_url_is_limited_to_the_philosophy_category(self):
        """用户要求一言用 ?c=k（哲学分类）：不带分类会随机到动画 / 游戏 / 抖机灵。

        这里断言的是**默认值**（测试环境没有设 SPARK_HITOKOTO_URL）；换分类走环境变量，
        不需要动代码。
        """
        self.assertIn('v1.hitokoto.cn', backend.HITOKOTO_URL)
        self.assertIn('c=k', backend.HITOKOTO_URL)

    def test_fetch_hitokoto_returns_none_instead_of_raising(self):
        self._patch_get(RuntimeError('connection reset by peer'))
        self.assertIsNone(backend.fetch_hitokoto(), '取不到必须安静地返回 None，不能抛给调用方')

    def test_fetch_hitokoto_returns_none_on_bad_status_or_empty_body(self):
        self._patch_get(FakeResponse(status_code=503, payload={'hitokoto': '这句话不该被用'}))
        self.assertIsNone(backend.fetch_hitokoto())
        self._patch_get(FakeResponse(payload={'hitokoto': '   '}))
        self.assertIsNone(backend.fetch_hitokoto())
        self._patch_get(FakeResponse(payload={}))
        self.assertIsNone(backend.fetch_hitokoto(), 'JSON 里没有可用正文时也算取不到')

    # ---------------- 任务上的「内容来源」字段 ----------------
    def test_register_task_defaults_to_text_source(self):
        task_id = backend._register_task('21:00', '小美', '早安')
        self.assertEqual(backend.task_meta[task_id]['source'], 'text')
        self.assertEqual(backend._task_view(task_id, backend.task_meta[task_id])['source'], 'text',
                         '列表要带 source，否则前端回填不出这条任务用的是什么来源')

    def test_register_task_stores_hitokoto_source(self):
        task_id = backend._register_task('21:00', '小美', '', source='hitokoto')
        self.assertEqual(backend.task_meta[task_id]['source'], 'hitokoto')
        self.assertEqual(backend._task_view(task_id, backend.task_meta[task_id])['source'], 'hitokoto')

    def test_register_task_normalises_unknown_source_to_text(self):
        task_id = backend._register_task('21:00', '小美', '早安', source='weibo')
        self.assertEqual(backend.task_meta[task_id]['source'], 'text')

    def test_restore_tasks_defaults_missing_source_to_text(self):
        backend.STATE.set('tasks', [{'task_id': 'legacy-1', 'time': '21:00',
                                     'name': '旧好友', 'text': ''}])
        self.assertEqual(backend.restore_tasks(), 1)
        self.assertEqual(backend.task_meta['legacy-1']['source'], 'text',
                         '老 state.json 没有 source 字段时要按 text 处理')

    def test_add_task_rejects_unknown_source(self):
        res = backend.add_time(payload={'time': '21:00', 'name': '小美',
                                        'text': '早安', 'source': 'weibo'})
        self.assertEqual(res.get('code'), 400, res)
        self.assertEqual(res.get('data'), '内容来源不支持，只支持 text、hitokoto 或 girlfriend')
        self.assertEqual(backend.task_meta, {}, '非法来源不该把任务建出来')

    def test_add_task_stores_hitokoto_source_and_exposes_it(self):
        res = backend.add_time(payload={'time': '21:00', 'name': '小美',
                                        'text': '', 'source': 'hitokoto'})
        self.assertEqual(res.get('code'), 200, res)
        task_id = res['task_id']
        self.assertEqual(backend.task_meta[task_id]['source'], 'hitokoto')
        listed = [t for t in backend.get_time_list()['data']['tasks'] if t['task_id'] == task_id]
        self.assertTrue(listed and listed[0]['source'] == 'hitokoto')

    def test_task_source_switch_persists_and_is_kept_when_omitted(self):
        res = backend.add_time(payload={'time': '21:00', 'name': '小美', 'text': '早安'})
        task_id = res['task_id']
        self.assertEqual(backend.task_meta[task_id]['source'], 'text')

        backend.edit_time(payload={'name': '小美', 'new_time': '21:00', 'source': 'hitokoto'})
        self.assertEqual(backend.task_meta[task_id]['source'], 'hitokoto')
        saved = [item for item in (backend.STATE.get('tasks') or [])
                 if item.get('task_id') == task_id]
        self.assertTrue(saved and saved[0].get('source') == 'hitokoto', '内容来源要落盘，重启后还在')

        # 没传 source（老前端不发这个字段）：不能把一言任务悄悄改回自己写的文案
        backend.edit_time(payload={'name': '小美', 'new_time': '22:00'})
        self.assertEqual(backend.task_meta[task_id]['source'], 'hitokoto',
                         '没传 source 时必须保持原状')

        # 显式传回 text 才是「改回我自己写」
        backend.edit_time(payload={'name': '小美', 'new_time': '22:00', 'source': 'text'})
        self.assertEqual(backend.task_meta[task_id]['source'], 'text')

    def test_edit_task_rejects_unknown_source(self):
        backend.add_time(payload={'time': '21:00', 'name': '小美', 'text': '早安'})
        res = backend.edit_time(payload={'name': '小美', 'new_time': '21:00', 'source': 'weibo'})
        self.assertEqual(res.get('code'), 400, res)
        self.assertEqual(res.get('data'), '内容来源不支持，只支持 text、hitokoto 或 girlfriend')
        self.assertEqual(list(backend.task_meta.values())[0]['source'], 'text',
                         '非法来源不该改动已有任务')

    # ---------------- 定时发送时解析内容 ----------------
    def _stub_send_env(self):
        captured = self._capture_send()
        self._recording_notify()
        self.patch('human_pause', lambda low, high: None)   # 真实实现会随机等 1~15 秒
        return captured

    def test_scheduled_send_uses_hitokoto_when_task_source_is_hitokoto(self):
        captured = self._stub_send_env()
        self.patch('fetch_hitokoto', lambda: '今天也要好好吃饭')
        task_id = backend._register_task('21:00', '小美', '早安', source='hitokoto')

        out = backend.run_scheduled_send('小美', '早安', task_id=task_id)
        self.assertEqual(out, 'success', out)
        self.assertEqual(captured['text'], '今天也要好好吃饭',
                         '任务选了「一言」就该用现取的那句，而不是它自己写的兜底文案')

    def test_scheduled_send_falls_back_to_task_text_when_hitokoto_fails(self):
        captured = self._stub_send_env()
        self.patch('fetch_hitokoto', lambda: None)
        task_id = backend._register_task('21:00', '小美', '我写的早安', source='hitokoto')

        out = backend.run_scheduled_send('小美', '我写的早安', task_id=task_id)
        self.assertEqual(out, 'success', out)
        self.assertEqual(captured['text'], '我写的早安')

    def test_scheduled_send_falls_back_to_local_message_when_everything_is_empty(self):
        captured = self._stub_send_env()
        self.patch('fetch_hitokoto', lambda: None)
        task_id = backend._register_task('21:00', '小美', '', source='hitokoto')

        out = backend.run_scheduled_send('小美', '', task_id=task_id)
        self.assertEqual(out, 'success', '一言挂了也必须把消息发出去（火花不能断）')
        self.assertIn(captured['text'], backend.FALLBACK_MESSAGES)

    def test_scheduled_send_with_text_source_never_calls_the_interface(self):
        captured = self._stub_send_env()

        def boom():
            raise AssertionError('任务没选一言来源时不该去请求一言接口')

        self.patch('fetch_hitokoto', boom)
        task_id = backend._register_task('21:00', '小美', '早安')

        out = backend.run_scheduled_send('小美', '早安', task_id=task_id)
        self.assertEqual(out, 'success', out)
        self.assertEqual(captured['text'], '早安')

    # ---------------- 手动发送 ----------------
    def test_manual_send_can_use_hitokoto(self):
        captured = self._capture_send()
        self.patch('fetch_hitokoto', lambda: '今天也要好好吃饭')

        res = backend.Send(payload={'name': '小红', 'text': '', 'source': 'hitokoto'})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(captured['text'], '今天也要好好吃饭')

    def test_manual_send_falls_back_when_hitokoto_fails(self):
        captured = self._capture_send()
        self.patch('fetch_hitokoto', lambda: None)

        res = backend.Send(payload={'name': '小红', 'text': '我自己写的', 'source': 'hitokoto'})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(captured['text'], '我自己写的')

    def test_manual_send_falls_back_to_local_message_when_nothing_else_is_left(self):
        captured = self._capture_send()
        self.patch('fetch_hitokoto', lambda: None)

        res = backend.Send(payload={'name': '小红', 'text': '', 'source': 'hitokoto'})
        self.assertEqual(res.get('code'), 200, res)
        self.assertIn(captured['text'], backend.FALLBACK_MESSAGES)

    def test_manual_send_ignores_unknown_source_and_stays_plain_text(self):
        captured = self._capture_send()

        def boom():
            raise AssertionError('非法 source 不该去请求一言接口')

        self.patch('fetch_hitokoto', boom)

        res = backend.Send(payload={'name': '小红', 'text': '早安', 'source': 'weibo'})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(captured['text'], '早安')

    # ---------------- 预览接口 ----------------
    def test_hitokoto_preview_requires_auth(self):
        self.patch('require_auth', _REAL_REQUIRE_AUTH)   # setUp 装的是「永远放行」的桩
        res = backend.HitokotoPreview()
        self.assertEqual(res.get('code'), 401, res)

    def test_hitokoto_preview_returns_a_sentence(self):
        self.patch('fetch_hitokoto', lambda: '今天也要好好吃饭')
        res = backend.HitokotoPreview()
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(res.get('data'), {'text': '今天也要好好吃饭'})

    def test_hitokoto_preview_reports_when_nothing_can_be_fetched(self):
        self.patch('fetch_hitokoto', lambda: None)
        res = backend.HitokotoPreview()
        self.assertEqual(res.get('code'), 400, res)
        self.assertIn('取不到', res.get('data') or '')

    def test_hitokoto_preview_route_is_not_serialized(self):
        """预览只发一个外网请求、不碰浏览器，不该被发送锁串行化挡住。

        这里同时钉住「不能挂 @serialized」和「源码里不能出现浏览器关键字」两条约束 ——
        后者是 test_every_browser_route_is_serialized 的反向扫描所依赖的判据。
        """
        source = open(os.path.join(REPO_ROOT, 'backend.py'), encoding='utf-8').read()
        blocks = _route_blocks(source)
        self.assertIn('/Api/Hitokoto/Preview', blocks)
        self.assertFalse(blocks['/Api/Hitokoto/Preview']['touches_browser'],
                         '预览接口不该出现 driver / douyin 关键字（会被反向扫描误判）')
        self.assertFalse(blocks['/Api/Hitokoto/Preview']['serialized'],
                         '预览接口不该挂 @serialized')


class ManualSendGuardTestCase(unittest.TestCase):
    """手动发送不再被任何重复检查拦住。

    用户 m02783 明确要求移除「已拦住一次重复发送」：那一下是人自己点的，再点一次就是
    想再发一次。定时链路（scope='task'）的「当天已发过就跳过」保持不变。
    """

    def setUp(self):
        backend.STATE.set('send_history', {})

    def test_manual_send_is_not_blocked_after_a_success(self):
        backend._history_mark('小美', '早安', 'success', kind='manual')
        self.assertIsNone(backend.send_guard('小美', '早安', scope='manual'))

    def test_manual_send_is_not_blocked_after_an_unknown_result(self):
        """这条正是用户遇到的场景：上一次结果未确认，马上重试必须放行。"""
        backend._history_mark('小美', '早安', 'unknown', kind='manual')
        self.assertIsNone(backend.send_guard('小美', '早安', scope='manual'))

    def test_task_scope_still_skips_the_same_friend_same_day(self):
        backend._history_mark('小美', '早安', 'success', kind='task')
        self.assertIsNotNone(backend.send_guard('小美', '早安', scope='task'))

    def test_backend_no_longer_defines_a_manual_cooldown(self):
        self.assertFalse(hasattr(backend, 'SEND_COOLDOWN_SECONDS'),
                         '手动冷却已移除，backend 不该再留这个常量（判定逻辑在 spark_core，给 task 用）')


class BubbleSideJsTestCase(unittest.TestCase):
    """发送方标记挂在气泡**内层** contentBox 上，只看祖先永远判不出 'self'。

    真机取证（2026-09-28）：我方的 contentBox class 是
    `messageMessageBoxcontentBox messageMessageBoxisFromMe`，对方只有
    `messageMessageBoxcontentBox`；而气泡行和列表一样宽（1052 vs 1064），
    所以旧的「只向上找祖先 + 整行中心点」两条路都判不出方向，一条已经送达的
    消息被判成「发送状态未确认」—— 这就是用户 m02783 的第 2 条。
    """

    def _code(self, name):
        raw = getattr(backend, name)
        return '\n'.join(line for line in raw.splitlines() if not line.strip().startswith('//'))

    def test_both_probes_look_into_the_content_box(self):
        for name in ('CHAT_OUTGOING_PROBE_JS', 'CHAT_IMAGE_PROBE_JS'):
            code = self._code(name)
            self.assertIn("el.querySelector('[class*=\"contentBox\"]", code, name)
            self.assertIn('box.width * 0.9', code, name)

    def test_content_box_lookup_runs_before_the_geometry_fallback(self):
        code = self._code('CHAT_OUTGOING_PROBE_JS')
        self.assertLess(code.index("el.querySelector('[class*=\"contentBox\"]"),
                        code.index('box.width * 0.9'),
                        '必须先用 contentBox 里的 isFromMe 判方向，再考虑位置兜底')

    def test_is_from_me_is_recognised_as_self(self):
        code = self._code('CHAT_OUTGOING_PROBE_JS')
        self.assertRegex(code, r'isFromMe[\s\S]{0,90}return \'self\'')

    def test_probes_pick_the_newest_bubble_by_position(self):
        """列表是 column-reverse：DOM 里最后一条匹配是最**旧**的，不能拿它当新消息。

        真机取证（2026-09-28 probe3）：idx=0（刚发的签文）top=2776 在视觉最下面，
        idx=15（03/21 的旧提醒）top=-397 在视觉最上面。图片探针原来取「DOM 最后一个
        匹配气泡」，实际取到的是那条旧系统消息（side=other），于是新图永远确认不了、
        每张图白等满 IMAGE_CONFIRM_TIMEOUT（25 秒）—— 这就是用户 m03672 说的「签文发得慢」。
        """
        for name in ('CHAT_OUTGOING_PROBE_JS', 'CHAT_IMAGE_PROBE_JS'):
            code = self._code(name)
            self.assertIn('newestTop', code, name)
            self.assertIn('getBoundingClientRect().top', code, name)
            self.assertRegex(code, r"top > newestTop[\s\S]{0,40}newest = items\[i\]", name)
            self.assertNotIn('untagged++; newest = items[i];', code,
                             '%s 不能按 DOM 顺序把最后一条当成新消息' % name)

    def test_photo_probe_never_counts_avatars(self):
        """头像同样是「大图」（36x36 渲染但 naturalWidth=168），必须先排除。

        真机实测 16 条气泡里 13 条被旧的 hasPhoto 算成「图片气泡」，新图根本挑不出来。
        """
        code = self._code('CHAT_IMAGE_PROBE_JS')
        self.assertIn('function isAvatarImage(el, node)', code)
        self.assertIn('/commonIMAvatar|avatarContainer/i', code)
        self.assertIn('if (isAvatarImage(el, img)) continue;', code)
        # 图片气泡自己带 messageMessageBoxhideAvatar，不能用 /avatar/i 一刀切
        self.assertNotIn('/avatar/i', code)


class GirlfriendTestCase(unittest.TestCase):
    """女朋友模式（早安 / 午安 / 晚安）：农历、时段、配置读写、天气渲染、接口与发送接入。

    一个用例都不许发真实网络请求：要么把 backend.requests 换成按 URL 分发的和风天气
    替身，要么直接 patch backend.render_girlfriend_text / backend.fetch_hitokoto。
    setUp / tearDown / 助手借 ImageSendTestCase 的，理由同 HitokotoTestCase ——
    继承会把父类的用例再跑一遍。
    """

    setUp = ImageSendTestCase.setUp
    tearDown = ImageSendTestCase.tearDown
    _capture_send = ImageSendTestCase._capture_send
    _recording_notify = ImageSendTestCase._recording_notify

    MOMENT = backend.datetime(2026, 9, 28, 8, 30)
    HOST = 'https://demo.qweatherapi.com'
    CURRENT = {
        'code': '200',
        'condition': {'text': '小雪', 'code': '401'},
        'temperature': {'value': -1, 'unit': '℃'},
        'humidity': 0.95,
        'wind': {'direction': {'degree': 135, 'compass': 'se'},
                 'speed': {'value': 12, 'unit': 'km/h'}, 'scale': '3'},
        'precipitation': {'type': ''},
    }
    DAILY = {
        'code': '200',
        'days': [{
            'fxDate': '2026-09-28',
            'temperatureMax': {'value': 3},
            'temperatureMin': {'value': -1},
            'daytime': {
                'condition': {'text': '小雪'},
                'wind': {'direction': {'compass': 'se'}, 'scale': '3'},
                'humidity': 0.95,
                'precipitation': {'type': 'snow', 'probability': 80},
            },
            'astro': {'sunrise': '06:06', 'sunset': '18:11'},
        }],
    }
    AIR = {'code': '200', 'indexes': [{'code': 'aqi', 'aqi': 123, 'category': '轻度污染'}]}
    QUOTE = '『今天也要好好吃饭』—— 「某来源 某人」'
    # 冻死的示例（农历那一行按真实算法修正：2026-09-28 是八月十八，不是八月初八；
    # 排版按用户给的「鱼崽小铃铛」卡片改成：城市进日期行 / 今日天气状况 / 冒号后空格 /
    # 时段词并进「哈喽哈喽」那句）
    EXPECTED = '\n'.join([
        '这是我们相识的第 304 天',
        '蚌埠 | 2026年09月28日 | 星期一',
        '农历 | 八月十八',
        '',
        '今日天气状况：',
        '天气： 小雪',
        '东南风： 3级',
        '温度： -1℃ ~ 3℃',
        '湿度： 95%',
        '空气： 轻度污染 | 123',
        '',
        '哈喽哈喽~早安呀，这里是来自小明的爱心提醒哦：',
        '今日最高温度仅为 3℃，可冷了~',
        '今天有雪，出门当心路滑~',
        '小美可要注意保暖哦~',
        '',
        'QUOTE',
    ]).replace('QUOTE', QUOTE)

    # ---------------- 工具 ----------------
    def _config(self, **kwargs):
        config = dict(backend.GF_DEFAULT_CONFIG)
        config.update(kwargs)
        backend.STATE.set('girlfriend', config)
        return config

    def _ready_config(self, **kwargs):
        """Host / Key / 城市都填好、坐标已缓存的配置：不会再打 GeoAPI。"""
        values = {'enabled': True, 'host': self.HOST, 'key': 'k-123', 'city': '蚌埠',
                  'lat': '32.92', 'lon': '117.39', 'tz': 'Asia/Shanghai'}
        values.update(kwargs)
        return self._config(**values)

    def _weather_data(self, **overrides):
        data = {'city': '蚌埠', 'current': self.CURRENT, 'daily': self.DAILY['days'][0],
                'air': (123, '轻度污染')}
        data.update(overrides)
        return data

    def _stub_qweather(self, current='ok', daily='ok', air='ok', geo='ok',
                       fail=None, status=None):
        """把 backend.requests 换成按 URL 分发的和风天气替身，返回请求记录列表。

        geo='empty' 模拟查不到城市；fail='weather'/'current'/'daily'/'air' 模拟对应
        接口报错；status=403 模拟 Key 与 Host 不配套；geo 传异常对象模拟网络层异常。
        """
        calls = []

        def fake_get(url, *args, **kwargs):
            calls.append({'url': url, 'params': kwargs.get('params') or {},
                          'headers': kwargs.get('headers') or {},
                          'timeout': kwargs.get('timeout')})
            if 'geo/v2/city/lookup' in url:
                if isinstance(geo, Exception):
                    raise geo
                if geo == 'empty':
                    return FakeResponse(payload={'code': '200', 'location': []})
                if geo == 'error':
                    return FakeResponse(payload={'code': '404', 'location': []})
                return FakeResponse(payload={'code': '200', 'location': [{
                    'name': '蚌埠', 'id': '101220201', 'lat': '32.92', 'lon': '117.39',
                    'adm1': '安徽省', 'adm2': '蚌埠', 'tz': 'Asia/Shanghai'}]})
            if '/airquality/v1/current/' in url:
                if fail in ('air', 'all') or status is not None:
                    return FakeResponse(status_code=(status or 500), payload={})
                return FakeResponse(payload=self.AIR if air == 'ok' else air)
            if '/weather/v1/current/' in url:
                if fail in ('weather', 'current', 'all') or status is not None:
                    return FakeResponse(status_code=(status or 500), payload={})
                return FakeResponse(payload=self.CURRENT if current == 'ok' else current)
            if '/weather/v1/daily/' in url:
                if fail in ('weather', 'daily', 'all') or status is not None:
                    return FakeResponse(status_code=(status or 500), payload={})
                return FakeResponse(payload=self.DAILY if daily == 'ok' else daily)
            raise AssertionError('不该请求的地址：%s' % url)

        class _RequestsStub:
            get = staticmethod(fake_get)

        self.patch('requests', _RequestsStub)
        return calls

    def _stub_qweather_raw(self, payload, status=200):
        """把 requests 换成「打哪个地址都返回同一份 payload」的替身。

        用来单测 `_gf_api_get` 对响应体的判定 —— 真实 v1 天气/空气接口没有顶层 code
        （见 task-5 的真机结论），这里是那段判定的回归防线。
        """
        class _RequestsStub:
            get = staticmethod(lambda url, *a, **kw: FakeResponse(status_code=status,
                                                                  payload=payload))
        self.patch('requests', _RequestsStub)

    # ---------------- 农历 ----------------
    def test_lunar_matches_the_two_anchors_from_the_spec(self):
        self.assertEqual(backend._lunar(2022, 1, 24), '腊月廿二')
        self.assertEqual(backend._lunar(2024, 2, 10), '正月初一')

    def test_lunar_matches_more_known_dates(self):
        known = {(2026, 2, 17): '正月初一', (2000, 2, 5): '正月初一',
                 (2050, 1, 23): '正月初一', (2023, 3, 22): '闰三月初一',
                 (2100, 12, 31): '腊月初一', (2026, 9, 28): '八月十八'}
        for day, expected in known.items():
            self.assertEqual(backend._lunar(*day), expected, day)

    def test_lunar_out_of_range_is_none_rather_than_wrong(self):
        self.assertIsNone(backend._lunar(1900, 1, 1), '表从 1900-01-31 才开始')
        self.assertIsNone(backend._lunar(1900, 1, 30))
        self.assertIsNone(backend._lunar(1800, 5, 5))
        self.assertIsNone(backend._lunar(2024, 13, 1), '非法日期不该抛异常')

    # ---------------- 相识天数 / 时段 ----------------
    def test_meet_days_counts_the_first_day_as_one(self):
        config = self._config(meet_date='2025-11-29')
        self.assertEqual(backend.girlfriend_meet_days(config, self.MOMENT), 304)

    def test_meet_days_is_none_when_the_date_is_missing_or_invalid(self):
        for value in ('', '   ', '2025/11/29', '2025-13-01', '2025-02-30'):
            config = self._config(meet_date=value)
            self.assertIsNone(backend.girlfriend_meet_days(config, self.MOMENT), value)

    def test_period_is_decided_by_the_hour(self):
        for hour, expected in ((0, 'morning'), (10, 'morning'), (11, 'noon'),
                               (16, 'noon'), (17, 'night'), (23, 'night')):
            moment = backend.datetime(2026, 9, 28, hour, 30)
            self.assertEqual(backend.girlfriend_period(moment), expected, hour)

    # ---------------- 配置读写 ----------------
    def test_config_view_hides_the_key_and_exposes_the_derived_fields(self):
        config = self._ready_config(meet_date='2025-11-29')
        view = backend._girlfriend_view(config)
        for field in backend.GF_CONFIG_FIELDS:
            self.assertIn(field, view, field)
        self.assertEqual(view['key'], '', 'Key 是凭据，接口不该回明文')
        self.assertTrue(view['key_set'])
        self.assertTrue(view['city_resolved'])
        self.assertEqual(view['meet_days'], backend.girlfriend_meet_days(config))

    def test_config_get_returns_defaults_when_nothing_is_saved(self):
        backend.STATE.set('girlfriend', {})
        res = backend.GetGirlfriendConfig()
        self.assertEqual(res.get('code'), 200, res)
        data = res.get('data') or {}
        self.assertEqual(data.get('host'), '')
        self.assertFalse(data.get('enabled'))
        self.assertFalse(data.get('key_set'))
        self.assertIsNone(data.get('meet_days'))
        for field in backend.GF_CONFIG_FIELDS:
            self.assertIn(field, data, field)

    def test_config_routes_require_auth(self):
        self.patch('require_auth', _REAL_REQUIRE_AUTH)   # setUp 装的是「永远放行」的桩
        for call in (lambda: backend.GetGirlfriendConfig(),
                     lambda: backend.SetGirlfriendConfig(payload={'city': '蚌埠'}),
                     lambda: backend.GirlfriendWeather(),
                     lambda: backend.GirlfriendPreview()):
            res = call()
            self.assertEqual(res.get('code'), 401, res)

    def test_config_set_saves_whitelisted_fields_and_ignores_everything_else(self):
        res = backend.SetGirlfriendConfig(payload={
            'enabled': True, 'host': '  ' + self.HOST + '  ', 'key': ' k-123 ',
            'city': ' 蚌埠 ', 'meet_date': ' 2025-11-29 ', 'her_name': ' 小美 ',
            'my_name': '小明', 'evil': 'ignore-me'})
        self.assertEqual(res.get('code'), 200, res)
        stored = backend.STATE.get('girlfriend')
        self.assertEqual(stored['host'], self.HOST)
        self.assertEqual(stored['city'], '蚌埠')
        self.assertEqual(stored['meet_date'], '2025-11-29')
        self.assertEqual(stored['her_name'], '小美')
        self.assertEqual(stored['key'], 'k-123')
        self.assertTrue(stored['enabled'])
        self.assertNotIn('evil', stored, '未知字段必须忽略')
        self.assertEqual(res['data']['key'], '', '回显也不给明文 Key')
        self.assertTrue(res['data']['key_set'])
        self.assertFalse(res['data']['city_resolved'], '还没解析过城市')

    def test_config_set_rejects_a_bad_meet_date_without_touching_the_old_one(self):
        self._config(meet_date='2025-11-29', city='蚌埠', key='k-123')
        for bad in ('2025/11/29', '2025-02-30', '20251129'):
            res = backend.SetGirlfriendConfig(payload={'meet_date': bad})
            self.assertEqual(res.get('code'), 400, (bad, res))
            self.assertEqual(res.get('data'), '相识日期格式应为 YYYY-MM-DD')
        self.assertEqual(backend.STATE.get('girlfriend')['meet_date'], '2025-11-29',
                         '日期不合法时不能把已存的配置改坏')

    def test_config_set_can_clear_the_meet_date(self):
        self._config(meet_date='2025-11-29')
        res = backend.SetGirlfriendConfig(payload={'meet_date': ''})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(backend.STATE.get('girlfriend')['meet_date'], '')
        self.assertIsNone(res['data']['meet_days'])

    def test_config_set_keeps_the_stored_key_when_the_panel_sends_it_empty(self):
        self._config(host=self.HOST, key='k-123')
        res = backend.SetGirlfriendConfig(payload={'key': '', 'city': '蚌埠'})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(backend.STATE.get('girlfriend')['key'], 'k-123',
                         '面板拿不到明文 Key，留空必须理解为「不修改」')
        self.assertTrue(res['data']['key_set'])

    def test_config_set_replaces_the_key_when_a_new_one_is_given(self):
        self._config(key='old-key')
        res = backend.SetGirlfriendConfig(payload={'key': ' new-key '})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(backend.STATE.get('girlfriend')['key'], 'new-key')

    def test_config_set_drops_the_cached_coordinates_when_the_city_changes(self):
        self._config(city='蚌埠', lat='32.92', lon='117.39', tz='Asia/Shanghai')
        res = backend.SetGirlfriendConfig(payload={'city': '上海'})
        self.assertEqual(res.get('code'), 200, res)
        stored = backend.STATE.get('girlfriend')
        for field in ('lat', 'lon', 'tz'):
            self.assertEqual(stored[field], '', field)
        self.assertFalse(res['data']['city_resolved'])

    def test_config_set_keeps_the_cached_coordinates_when_the_city_is_unchanged(self):
        self._config(city='蚌埠', lat='32.92', lon='117.39', tz='Asia/Shanghai')
        res = backend.SetGirlfriendConfig(payload={'city': '蚌埠', 'my_name': '小明'})
        self.assertEqual(res.get('code'), 200, res)
        stored = backend.STATE.get('girlfriend')
        self.assertEqual(stored['lat'], '32.92')
        self.assertEqual(stored['tz'], 'Asia/Shanghai')

    def test_config_view_shows_the_resolved_city_name_instead_of_a_bare_bool(self):
        """面板上「已解析城市：」要显示城市名，不是 True。

        只回 city_resolved 布尔时，Settings.vue 里 {{ girlfriendForm.city_resolved }}
        会直接渲染成 'true'（真机 UI 检查抓到的 bug）。
        """
        self._stub_qweather()
        self._ready_config(city='蚌埠')
        res = backend.GirlfriendWeather(refresh='1')
        self.assertEqual(res.get('code'), 200, res)
        view = backend._girlfriend_view()
        self.assertTrue(view['city_resolved'])
        self.assertEqual(view['city_resolved_text'], '蚌埠（安徽省 蚌埠）')
        self.assertIn('city_resolved_text', res['data'], '天气接口也要把解析出来的城市名回给面板')

    def test_resolved_city_text_falls_back_to_what_the_user_typed(self):
        self._config(city='义乌市')
        view = backend._girlfriend_view()
        self.assertFalse(view['city_resolved'])
        self.assertEqual(view['city_resolved_text'], '义乌市')

    def test_cached_coordinates_do_not_overwrite_the_resolved_city_name(self):
        """缓存命中时 location 里的 name/adm 只是回显，不能盖掉解析出来的正式名。"""
        self._stub_qweather()
        self._ready_config(city='义乌市廿三里', city_name='义乌', city_adm='浙江省 金华')
        res = backend.GirlfriendWeather()
        self.assertEqual(res.get('code'), 200, res)
        stored = backend.STATE.get('girlfriend')
        self.assertEqual(stored['city_name'], '义乌')
        self.assertEqual(stored['city_adm'], '浙江省 金华')
        self.assertEqual(res['data']['city_resolved_text'], '义乌（浙江省 金华）')

    def test_config_set_drops_the_resolved_city_name_when_the_city_changes(self):
        self._config(city='蚌埠', lat='32.92', lon='117.39', tz='Asia/Shanghai',
                     city_name='蚌埠', city_adm='安徽省 蚌埠')
        res = backend.SetGirlfriendConfig(payload={'city': '上海'})
        self.assertEqual(res.get('code'), 200, res)
        stored = backend.STATE.get('girlfriend')
        for field in ('lat', 'lon', 'tz', 'city_name', 'city_adm'):
            self.assertEqual(stored[field], '', field)
        self.assertEqual(res['data']['city_resolved_text'], '上海')

    def test_girlfriend_routes_do_not_take_the_browser_lock(self):
        """这四个接口不碰浏览器，所以不该挂 @serialized。

        @serialized 会占住全局 browser_lock，而这几个接口最坏要等 4 次外部 HTTP
        （geo / current / daily / airquality，每次最长 QWEATHER_TIMEOUT），真占住锁约 40 秒，
        会把正好撞上的定时发送挤成 409「浏览器正忙」。
        """
        source = open(os.path.join(REPO_ROOT, 'backend.py'), encoding='utf-8').read()
        blocks = _route_blocks(source)
        for path in ('/Api/Girlfriend/Config', '/Api/Girlfriend/Weather',
                     '/Api/Girlfriend/Preview'):
            self.assertIn(path, blocks, path)
            self.assertFalse(blocks[path]['serialized'],
                             '%s 不碰浏览器，不该挂 @serialized' % path)
            self.assertFalse(blocks[path]['touches_browser'],
                             '%s 不该出现 driver / douyin 关键字' % path)

    def test_task_source_now_accepts_girlfriend(self):
        for source in backend.TASK_SOURCES:
            self.assertIsNone(backend._task_source_error(source), source)
        self.assertEqual(backend._task_source_error('weibo'),
                         '内容来源不支持，只支持 text、hitokoto 或 girlfriend')

    # ---------------- 天气接口 ----------------
    def test_weather_reports_a_missing_host(self):
        self._config(key='k-123', city='蚌埠')
        res = backend.GirlfriendWeather()
        self.assertEqual(res.get('code'), 400, res)
        self.assertIn('Host', res.get('data') or '')

    def test_weather_reports_a_missing_key(self):
        self._config(host=self.HOST, city='蚌埠')
        res = backend.GirlfriendWeather()
        self.assertEqual(res.get('code'), 400, res)
        self.assertIn('Key', res.get('data') or '')

    def test_weather_returns_the_contract_shape(self):
        calls = self._stub_qweather()
        self._ready_config(meet_date='2025-11-29', my_name='小明', her_name='小美')
        self.patch('fetch_hitokoto', lambda: None)
        res = backend.GirlfriendWeather()
        self.assertEqual(res.get('code'), 200, res)
        data = res['data']
        self.assertEqual(sorted(data.keys()),
                         ['city', 'city_resolved', 'city_resolved_text', 'current',
                          'daily', 'lat', 'lon', 'text', 'tz'])
        self.assertEqual(data['city'], '蚌埠')
        self.assertEqual(data['lat'], '32.92')
        self.assertEqual(data['lon'], '117.39')
        self.assertEqual(data['tz'], 'Asia/Shanghai')
        self.assertEqual(data['current'], self.CURRENT)
        self.assertEqual(data['daily'], self.DAILY['days'][0])
        self.assertEqual([call['url'] for call in calls], [
            self.HOST + '/weather/v1/current/32.92/117.39',
            self.HOST + '/weather/v1/daily/32.92/117.39',
            self.HOST + '/airquality/v1/current/32.92/117.39'])
        for call in calls:
            self.assertEqual(call['headers'].get('X-QW-Api-Key'), 'k-123')
            self.assertNotIn('key', call['params'], '认证走请求头，不能再拼 ?key=')
            self.assertEqual(call['timeout'], backend.QWEATHER_TIMEOUT)
        text = data['text']
        self.assertIn('今日天气状况：', text)
        self.assertIn('天气： 小雪', text)
        self.assertIn('温度： -1℃ ~ 3℃', text)
        self.assertIn('湿度： 95%', text)
        self.assertIn('空气： 轻度污染 | 123', text)
        self.assertIn('哈喽哈喽~%s呀，这里是' % backend.GF_PERIOD_TEXT[backend.girlfriend_period()],
                      text)
        self.assertNotIn('今日天气（', text, '天气块标题不再带城市了')
        self.assertTrue(text.endswith('小美可要注意保暖哦~'), text)
        self.assertNotIn('点我有惊喜', text, '用户要求移除彩蛋尾巴')

    def test_weather_resolves_the_city_once_then_uses_the_cache(self):
        calls = self._stub_qweather()
        self._config(enabled=True, host=self.HOST, key='k-123', city='蚌埠')
        self.patch('fetch_hitokoto', lambda: None)
        res = backend.GirlfriendWeather(refresh='1')
        self.assertEqual(res.get('code'), 200, res)
        stored = backend.STATE.get('girlfriend')
        self.assertEqual((stored['lat'], stored['lon'], stored['tz']),
                         ('32.92', '117.39', 'Asia/Shanghai'), '坐标必须写回配置')
        self.assertEqual(calls[0]['url'], self.HOST + '/geo/v2/city/lookup')
        self.assertEqual(calls[0]['params'].get('location'), '蚌埠')
        self.assertEqual(calls[0]['params'].get('range'), 'cn')
        self.assertEqual(calls[0]['params'].get('lang'), 'zh')
        self.assertEqual(calls[0]['headers'].get('X-QW-Api-Key'), 'k-123')
        calls.clear()
        res = backend.GirlfriendWeather()
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual([call['url'] for call in calls], [
            self.HOST + '/weather/v1/current/32.92/117.39',
            self.HOST + '/weather/v1/daily/32.92/117.39',
            self.HOST + '/airquality/v1/current/32.92/117.39'],
            '坐标已缓存就不该再打 GeoAPI（免费额度有限）')

    def test_weather_reports_an_unknown_city(self):
        self._stub_qweather(geo='empty')
        self._config(host=self.HOST, key='k-123', city='不存在的城市')
        res = backend.GirlfriendWeather()
        self.assertEqual(res.get('code'), 400, res)
        self.assertIn('没有查到城市', res.get('data') or '')

    def test_weather_reports_a_qweather_error_code(self):
        self._stub_qweather(current={'code': '401', 'message': 'bad key'})
        self._ready_config()
        res = backend.GirlfriendWeather()
        self.assertEqual(res.get('code'), 400, res)
        self.assertIn('401', res.get('data') or '')

    def test_weather_reports_a_http_failure(self):
        self._stub_qweather(fail='weather')
        self._ready_config()
        res = backend.GirlfriendWeather()
        self.assertEqual(res.get('code'), 400, res)
        self.assertIn('HTTP 500', res.get('data') or '')

    def test_weather_explains_a_rejected_key(self):
        self._stub_qweather(status=403)
        self._ready_config()
        res = backend.GirlfriendWeather()
        self.assertEqual(res.get('code'), 400, res)
        self.assertIn('Host 与 Key', res.get('data') or '')

    def test_weather_reports_a_network_failure(self):
        self._stub_qweather(geo=RuntimeError('测试环境不联网'))
        self._config(host=self.HOST, key='k-123', city='蚌埠')
        res = backend.GirlfriendWeather()
        self.assertEqual(res.get('code'), 400, res)
        self.assertIn('连接和风天气失败', res.get('data') or '')

    def test_weather_skips_the_air_line_when_airquality_is_unavailable(self):
        self._stub_qweather(fail='air')
        self._ready_config(meet_date='2025-11-29')
        self.patch('fetch_hitokoto', lambda: None)
        res = backend.GirlfriendWeather()
        self.assertEqual(res.get('code'), 200, '空气取不到不该让整条问候语失败')
        self.assertIn('温度： -1℃ ~ 3℃', res['data']['text'])
        self.assertNotIn('空气：', res['data']['text'])

    # ---------------- _gf_api_get 的响应体判定（真实 v1 接口没有 code） ----------------
    def test_api_get_accepts_a_v1_payload_without_a_code_field(self):
        """真机 bug 的回归：/weather/v1/* 不返回顶层 code，字段不存在 ≠ 出错。"""
        payloads = (
            {'metadata': {'tag': 'x'}, 'condition': {'text': '多云'},
             'temperature': {'value': 27.7}, 'humidity': 0.81},
            {'metadata': {'tag': 'x'}, 'days': [{'temperatureMax': {'value': 33.66}}]},
            {'metadata': {'tag': 'x'},
             'indexes': [{'code': 'aqi', 'aqi': 41, 'category': '优'}]},
        )
        for payload in payloads:
            self._stub_qweather_raw(payload)
            got, error = backend._gf_api_get(self.HOST, 'k-123',
                                             '/weather/v1/current/29.3/120.07',
                                             {'lang': 'zh'})
            self.assertIsNone(error, error)
            self.assertEqual(got, payload)

    def test_api_get_rejects_a_real_error_code(self):
        for code in ('400', '404'):
            self._stub_qweather_raw({'code': code})
            got, error = backend._gf_api_get(self.HOST, 'k-123', '/geo/v2/city/lookup')
            self.assertIsNone(got)
            self.assertIn('和风天气返回错误码 %s' % code, error)

    def test_api_get_treats_a_missing_or_empty_code_as_success(self):
        for payload in ({'days': []}, {'code': ''}, {'code': None}, {'code': '   '}):
            self._stub_qweather_raw(dict(payload))
            got, error = backend._gf_api_get(self.HOST, 'k-123', '/weather/v1/daily/1/2')
            self.assertIsNone(error, '字段不存在/为空不该算错：%r' % (payload,))
            self.assertIsInstance(got, dict)

    def test_api_get_reports_the_v1_error_object(self):
        self._stub_qweather_raw({'error': {
            'status': 403,
            'type': 'https://dev.qweather.com/docs/resource/error-code/#invalid-host',
            'title': 'Invalid Host'}})
        got, error = backend._gf_api_get(self.HOST, 'k-123', '/geo/v2/city/lookup')
        self.assertIsNone(got)
        self.assertIn('和风天气返回错误：', error)
        self.assertIn('Invalid Host', error)
        self.assertIn('403', error)

    def test_air_index_still_reads_the_index_code_not_the_envelope(self):
        """indexes[].code 是空气质量指标自己的 code，跟接口返回码同名不同义。"""
        self._stub_qweather_raw({'metadata': {}, 'indexes': [
            {'code': 'pm2p5', 'aqi': 3, 'category': '良'},
            {'code': 'aqi', 'aqi': 41, 'category': '优'}]})
        got, error = backend._gf_api_get(self.HOST, 'k-123', '/airquality/v1/current/1/2')
        self.assertIsNone(error, error)
        self.assertEqual(backend._gf_air_index(got), (41, '优'))

    def test_air_index_prefers_the_cn_mee_standard(self):
        """真机给的是 code='cn-mee'（中国 MEE 标准），没有 'aqi' 这一项。"""
        self.assertEqual(backend._gf_air_index({'indexes': [
            {'code': 'cn-mee', 'name': 'AQI (CN)', 'aqi': 41, 'level': '1',
             'category': '优'}]}), (41, '优'))
        # 两个标准同时存在时按中国大陆口径优先 cn-mee
        self.assertEqual(backend._gf_air_index({'indexes': [
            {'code': 'usa-epa', 'aqi': 88, 'category': 'Moderate'},
            {'code': 'aqi', 'aqi': 77, 'category': '良'},
            {'code': 'cn-mee', 'aqi': 41, 'category': '优'}]}), (41, '优'))

    def test_air_index_falls_back_to_any_numeric_aqi(self):
        # 字符串数字也算数值；拿不到 'aqi' / 'cn-mee' 时用第一个有数值 aqi 的项
        self.assertEqual(backend._gf_air_index({'indexes': [
            {'code': 'qaqi', 'aqi': '41', 'category': '优'}]}), (41, '优'))
        self.assertEqual(backend._gf_air_index({'indexes': [
            {'code': 'pm2p5', 'aqi': 3, 'category': '良'},
            {'code': 'qaqi', 'aqi': 55, 'category': '良'}]}), (3, '良'))
        # 没有数值 aqi 的项被跳过，最后才放弃（少一行，不是报错）
        self.assertIsNone(backend._gf_air_index({'indexes': [
            {'code': 'cn-mee', 'name': 'AQI (CN)'}, {'code': 'pm2p5'}]}))
        self.assertIsNone(backend._gf_air_index({'indexes': []}))
        self.assertIsNone(backend._gf_air_index({}))
        self.assertIsNone(backend._gf_air_index(None))

    def test_air_index_keeps_a_category_only_item(self):
        self.assertEqual(backend._gf_air_index({'indexes': [
            {'code': 'cn-mee', 'category': '优'}]}), (None, '优'))

    def test_geo_lookup_still_demands_its_own_code(self):
        """别把 v1 的宽松规则放松到 GeoAPI：它一定带 code。"""
        self._stub_qweather_raw({'location': [{'name': '蚌埠', 'lat': '32.92',
                                               'lon': '117.39', 'tz': 'Asia/Shanghai'}]})
        location, error = backend._gf_resolve_location(
            {'host': self.HOST, 'key': 'k-123', 'city': '蚌埠', 'lat': '', 'lon': ''})
        self.assertIsNone(location)
        self.assertIn('和风天气返回错误码', error)

    def test_weather_works_with_real_v1_payloads_that_have_no_code(self):
        """端到端复现真机形状：v1 三个端点都没有顶层 code，必须照常出文案。"""
        current = dict(self.CURRENT)
        current.pop('code', None)
        air = {'indexes': [{'code': 'aqi', 'aqi': 41, 'category': '优'}]}
        self._stub_qweather(current=current, daily={'days': dict(self.DAILY)['days']}, air=air)
        self._ready_config(meet_date='2025-11-29')
        self.patch('fetch_hitokoto', lambda: None)
        res = backend.GirlfriendWeather()
        self.assertEqual(res.get('code'), 200, res)
        self.assertIn('今日天气状况：', res['data']['text'])
        self.assertIn('空气： 优 | 41', res['data']['text'])

    # ---------------- 文案渲染 ----------------
    def test_message_matches_the_frozen_example(self):
        text = backend.build_girlfriend_message(
            self._config(meet_date='2025-11-29', my_name='小明', her_name='小美'),
            self.MOMENT, 'morning', self._weather_data(), quote=self.QUOTE)
        self.assertEqual(text, self.EXPECTED)

    def test_full_render_matches_the_frozen_example(self):
        """端到端：只 stub 掉 requests，渲染结果必须逐字等于冻死的示例。"""
        self._stub_qweather()
        self._ready_config(meet_date='2025-11-29', my_name='小明', her_name='小美')
        self.patch('fetch_hitokoto', lambda: self.QUOTE)
        text, data, error = backend.girlfriend_render(period='morning', moment=self.MOMENT)
        self.assertIsNone(error, error)
        self.assertEqual(data.get('city'), '蚌埠')
        self.assertEqual(text, self.EXPECTED)
        self.assertEqual(
            backend.render_girlfriend_text(period='morning', moment=self.MOMENT),
            self.EXPECTED)

    def test_message_without_a_quote_has_no_hitokoto_line(self):
        text = backend.build_girlfriend_message(
            self._config(meet_date='2025-11-29'), self.MOMENT, 'morning',
            self._weather_data())
        self.assertNotIn('『', text, '取不到一言就整行不出现')
        self.assertTrue(text.endswith('今天有雪，出门当心路滑~'), text)
        self.assertNotIn('点我有惊喜', text, '用户要求移除彩蛋尾巴')

    def test_message_drops_every_line_whose_data_is_missing(self):
        text = backend.build_girlfriend_message(
            self._config(), backend.datetime(2022, 1, 24, 15, 0), 'noon',
            {'city': '蚌埠', 'current': {'code': '200'}, 'daily': {}, 'air': None})
        self.assertNotIn('这是我们相识的第', text, '没填相识日期就没有第一行')
        self.assertIn('蚌埠 | 2022年01月24日 | 星期一', text)
        self.assertIn('农历 | 腊月廿二', text)
        self.assertIn('今日天气状况：', text)
        for missing in ('天气：', '温度：', '湿度：', '空气：', '风', '今日最高'):
            self.assertNotIn(missing, text, missing)
        self.assertIn('哈喽哈喽~午安呀，这里是爱心提醒哦：', text)
        self.assertNotIn('点我有惊喜', text, '用户要求移除彩蛋尾巴')

    def test_message_without_a_city_has_no_stray_pipe(self):
        """城市为空时日期行退回「日期 | 星期X」，绝不能留下「 | 」前缀。"""
        for empty in ('', None, '   '):
            text = backend.build_girlfriend_message(
                self._config(city=empty or ''), self.MOMENT, 'morning',
                {'city': empty, 'current': {}, 'daily': {}})
            date_line = [line for line in text.split('\n')
                         if '星期' in line][0]
            self.assertEqual(date_line, '2026年09月28日 | 星期一', text)
            self.assertNotIn('| 2026年09月28日', text, text)
            # 天气块标题也不再带城市
            self.assertIn('今日天气状况：', text)

    def test_message_temperature_fallbacks_keep_the_colon_space(self):
        """只有一头温度时的兜底行也要守「冒号后一个空格」的排版。"""
        only_max = backend.build_girlfriend_message(
            self._config(), self.MOMENT, 'morning',
            {'current': {}, 'daily': {'temperatureMax': {'value': 7}}})
        self.assertIn('温度： 最高 7℃', only_max)
        self.assertNotIn('温度：最高', only_max)
        only_min = backend.build_girlfriend_message(
            self._config(), self.MOMENT, 'morning',
            {'current': {}, 'daily': {'temperatureMin': {'value': -3.5}}})
        self.assertIn('温度： 最低 -4℃', only_min)
        self.assertNotIn('温度：最低', only_min)

    def test_message_advice_buckets(self):
        # 「仅为」只属于真的冷的那两档：34℃ 说「仅为 34℃」是反话
        for high, expected in ((-5, '今日最高温度仅为 -5℃，冷得很~'),
                               (0, '今日最高温度仅为 0℃，冷得很~'),
                               (9, '今日最高温度仅为 9℃，可冷了~'),
                               (17, '今日最高温度 17℃，有点凉，记得加件外套'),
                               (25, '今日最高温度 25℃，温度刚好，出去走走吧'),
                               (30, '今日最高温度 30℃，有点热，记得多喝水')):
            text = backend.build_girlfriend_message(
                self._config(), self.MOMENT, 'morning',
                {'city': '', 'current': {}, 'daily': {'temperatureMax': {'value': high}}})
            self.assertIn(expected, text, high)
            if high >= 10:
                self.assertNotIn('仅为', text, high)

    def test_her_line_follows_the_temperature(self):
        """34℃ 不该提醒「注意保暖」：那一行也走同一套分档。"""
        cases = ((-2, '小美可要注意保暖哦~'), (5, '小美可要注意保暖哦~'),
                 (15, '小美记得添件衣服哦~'), (22, '小美今天天气不错，出去走走吧~'),
                 (34, '小美记得多喝水哦~'))
        for high, expected in cases:
            text = backend.build_girlfriend_message(
                self._config(her_name='小美'), self.MOMENT, 'morning',
                {'city': '', 'current': {},
                 'daily': {'temperatureMax': {'value': high}}})
            self.assertIn(expected, text, high)
        no_temp = backend.build_girlfriend_message(
            self._config(her_name='小美'), self.MOMENT, 'morning',
            {'city': '', 'current': {}, 'daily': {}})
        self.assertIn('小美记得照顾好自己哦~', no_temp)
        self.assertNotIn('可要注意保暖', no_temp, '拿不到温度时不许预设天冷')

    def test_her_line_absent_without_a_name(self):
        text = backend.build_girlfriend_message(
            self._config(her_name=''), self.MOMENT, 'morning',
            {'city': '', 'current': {}, 'daily': {'temperatureMax': {'value': 34}}})
        self.assertNotIn('记得多喝水哦~', text)
        self.assertNotIn('记得照顾好自己哦~', text)

    def test_city_comes_from_the_config_not_the_resolved_name(self):
        """天气接口拿到的是解析名（义乌市），预览走缓存拿不到 —— 一律用用户填的原文。"""
        text = backend.build_girlfriend_message(
            self._config(city='义乌市'), self.MOMENT, 'morning',
            {'city': '义乌', 'current': {}, 'daily': {}})
        self.assertIn('义乌市 | 2026年09月28日 | 星期一', text)
        self.assertNotIn('义乌 | 2026年09月28日', text)
        # 用户没填城市时才退回解析名
        text = backend.build_girlfriend_message(
            self._config(city=''), self.MOMENT, 'morning',
            {'city': '义乌', 'current': {}, 'daily': {}})
        self.assertIn('义乌 | 2026年09月28日 | 星期一', text)

    def test_message_rounds_temperatures_half_up(self):
        for value, expected in ((3.5, '4℃'), (2.5, '3℃'), (-1.5, '-2℃'), (-2.5, '-3℃')):
            text = backend.build_girlfriend_message(
                self._config(), self.MOMENT, 'morning',
                {'daily': {'temperatureMin': {'value': value},
                           'temperatureMax': {'value': value}}, 'current': {}})
            self.assertIn('温度： %s ~ %s' % (expected, expected), text, value)

    def test_message_wind_direction_mapping(self):
        for compass, expected in (('n', '北风： 3级'), ('nne', '东北风： 3级'),
                                  ('se', '东南风： 3级'), ('nnw', '北风： 3级'),
                                  ('w', '西风： 3级'), ('sw', '西南风： 3级'),
                                  ('none', None), ('vrb', None), ('', None),
                                  ('weird', None)):
            text = backend.build_girlfriend_message(
                self._config(), self.MOMENT, 'morning',
                {'daily': {'daytime': {'wind': {'direction': {'compass': compass},
                                                'scale': '3'}}}, 'current': {}})
            if expected:
                self.assertIn(expected, text, compass)
            else:
                # 方位不可知（none / vrb / 脏值）时只报风力，绝不编一个方位出来
                self.assertIn('风力： 3级', text, compass)
                for _code, name in backend.GF_WIND_DIRECTIONS:
                    self.assertNotIn(name + '风', text, (compass, name))

    def test_message_humidity_is_a_percentage(self):
        for value, expected in ((0.95, '湿度： 95%'), (0.955, '湿度： 96%'),
                                (88, '湿度： 88%'), (1, '湿度： 100%')):
            text = backend.build_girlfriend_message(
                self._config(), self.MOMENT, 'morning',
                {'current': {'humidity': value}, 'daily': {}})
            self.assertIn(expected, text, value)

    def test_message_never_prints_an_empty_name(self):
        text = backend.build_girlfriend_message(
            self._config(), self.MOMENT, 'night', self._weather_data())
        self.assertIn('哈喽哈喽~晚安呀，这里是爱心提醒哦：', text)
        self.assertNotIn('来自', text, '没填「我」的称呼时不能出现空的「来自」')
        self.assertNotIn('可要注意保暖', text)

        text = backend.build_girlfriend_message(
            self._config(my_name='小明'), self.MOMENT, 'night', self._weather_data())
        self.assertIn('哈喽哈喽~晚安呀，这里是来自小明的爱心提醒哦：', text)
        self.assertNotIn('来自的', text)

        # 时段词必须并进「哈喽哈喽」那句，不能再留一条独立的问候行
        self.assertNotIn('这是今天的爱心提醒：', text)
        self.assertNotIn('～', text, '新的问候口径里不该再出现波浪号问候')

    # ---------------- 预览接口 ----------------
    def test_preview_returns_the_period_and_the_text(self):
        self._stub_qweather()
        self._ready_config(meet_date='2025-11-29')
        self.patch('fetch_hitokoto', lambda: None)
        for period, period_text in (('morning', '早安'), ('noon', '午安'), ('night', '晚安')):
            res = backend.GirlfriendPreview(period=period)
            self.assertEqual(res.get('code'), 200, res)
            self.assertEqual(res['data']['period'], period)
            self.assertEqual(res['data']['period_text'], period_text)
            self.assertIn('哈喽哈喽~%s呀，这里是' % period_text, res['data']['text'])

    def test_preview_unknown_period_falls_back_to_the_current_hour(self):
        self._stub_qweather()
        self._ready_config()
        self.patch('fetch_hitokoto', lambda: None)
        res = backend.GirlfriendPreview(period='dawn')
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(res['data']['period'], backend.girlfriend_period())

    def test_preview_reports_a_chinese_reason_when_nothing_is_configured(self):
        self._config()
        res = backend.GirlfriendPreview(period='auto')
        self.assertEqual(res.get('code'), 400, res)
        self.assertIn('Host', res.get('data') or '')

    # ---------------- 发送路径接入 ----------------
    def _stub_send_env(self):
        captured = self._capture_send()
        self._recording_notify()
        self.patch('human_pause', lambda low, high: None)
        return captured

    def test_add_task_accepts_the_girlfriend_source(self):
        res = backend.add_time(payload={'time': '21:00', 'name': '小美', 'text': '',
                                        'source': 'girlfriend'})
        self.assertEqual(res.get('code'), 200, res)
        task_id = res.get('task_id')
        self.assertEqual(backend.task_meta[task_id]['source'], 'girlfriend')
        tasks = backend.get_time_list()['data']['tasks']
        self.assertEqual([t['source'] for t in tasks if t['task_id'] == task_id],
                         ['girlfriend'], '面板要靠 source 回填「女朋友模式」')

    def test_scheduled_send_renders_the_greeting_for_a_girlfriend_task(self):
        captured = self._stub_send_env()
        self.patch('render_girlfriend_text', lambda *a, **k: '早安呀～（渲染出来的问候）')
        task_id = backend._register_task('21:00', '小美', '我写的早安', source='girlfriend')

        out = backend.run_scheduled_send('小美', '我写的早安', task_id=task_id)
        self.assertEqual(out, 'success', out)
        self.assertEqual(captured['text'], '早安呀～（渲染出来的问候）',
                         '任务里写的文案只是取不到天气时的兜底')

    def test_scheduled_send_falls_back_to_the_task_text_when_rendering_fails(self):
        captured = self._stub_send_env()
        self.patch('render_girlfriend_text', lambda *a, **k: None)
        task_id = backend._register_task('21:00', '小美', '我写的早安', source='girlfriend')

        out = backend.run_scheduled_send('小美', '我写的早安', task_id=task_id)
        self.assertEqual(out, 'success', out)
        self.assertEqual(captured['text'], '我写的早安')

    def test_scheduled_send_falls_back_to_a_local_message_when_everything_is_empty(self):
        captured = self._stub_send_env()
        self.patch('render_girlfriend_text', lambda *a, **k: None)
        task_id = backend._register_task('21:00', '小美', '', source='girlfriend')

        out = backend.run_scheduled_send('小美', '', task_id=task_id)
        self.assertEqual(out, 'success', '天气挂了也必须把消息发出去（火花不能断）')
        self.assertIn(captured['text'], backend.FALLBACK_MESSAGES)

    def test_scheduled_send_logs_a_warning_when_it_falls_back(self):
        self._stub_send_env()
        self.patch('render_girlfriend_text', lambda *a, **k: None)
        events = []
        self.patch('log_event', lambda level, category, message, detail=None:
                   events.append((level, category, message)))
        task_id = backend._register_task('21:00', '小美', '我写的早安', source='girlfriend')

        backend.run_scheduled_send('小美', '我写的早安', task_id=task_id)
        self.assertTrue([e for e in events if e[0] == 'warn' and e[1] == '女朋友模式'],
                        events)

    def test_scheduled_send_with_text_source_never_renders_the_greeting(self):
        captured = self._stub_send_env()

        def boom(*args, **kwargs):
            raise AssertionError('任务没选女朋友模式时不该去渲染天气问候')

        self.patch('render_girlfriend_text', boom)
        task_id = backend._register_task('21:00', '小美', '早安')

        out = backend.run_scheduled_send('小美', '早安', task_id=task_id)
        self.assertEqual(out, 'success', out)
        self.assertEqual(captured['text'], '早安')

    def test_test_send_renders_the_greeting_for_a_girlfriend_task(self):
        captured = self._stub_send_env()
        self.patch('render_girlfriend_text', lambda *a, **k: '晚安～（渲染出来的问候）')
        task_id = backend._register_task('21:00', '小美', '我写的晚安', source='girlfriend')

        res = backend.test_task_send(payload={'task_id': task_id})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(captured['text'], '晚安～（渲染出来的问候）',
                         '试发必须和真发同样的内容，否则试了也白试')

    def test_manual_send_can_use_the_girlfriend_greeting(self):
        captured = self._capture_send()
        self.patch('render_girlfriend_text', lambda *a, **k: '午安～（天气问候）')

        res = backend.Send(payload={'name': '小红', 'text': '', 'source': 'girlfriend'})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(captured['text'], '午安～（天气问候）')

    def test_manual_send_falls_back_to_the_written_text(self):
        captured = self._capture_send()
        self.patch('render_girlfriend_text', lambda *a, **k: None)

        res = backend.Send(payload={'name': '小红', 'text': '我自己写的', 'source': 'girlfriend'})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(captured['text'], '我自己写的')

    def test_manual_send_falls_back_to_a_local_message_at_the_end(self):
        captured = self._capture_send()
        self.patch('render_girlfriend_text', lambda *a, **k: None)

        res = backend.Send(payload={'name': '小红', 'text': '', 'source': 'girlfriend'})
        self.assertEqual(res.get('code'), 200, res)
        self.assertIn(captured['text'], backend.FALLBACK_MESSAGES)


# ==================== 浏览器启动失败：清理残留 + 重试一次 ====================
class BrowserStartupRecoveryTestCase(unittest.TestCase):
    """用户 2026-09-30 遇到的 `session not created: Chrome instance exited`。

    现象：上游后端被强制结束（后台作业被杀），Chrome 被留下继续占着 chrome-profile；
    下一次启动时 chromedriver 起不来（连 DevToolsActivePort 都没写出来），/healthz 一直
    browser_ready=false，定时任务全发不出去。修法两件：Windows 也清理残留进程 +
    启动失败时清理后重试一次。这里不碰真浏览器，只验证这几条不会退化。
    """

    setUp = ImageSendTestCase.setUp
    tearDown = ImageSendTestCase.tearDown

    def test_windows_cleanup_never_kills_every_chrome(self):
        script = backend._windows_cleanup_script(r'C:\some\chrome-profile')
        self.assertIn(r'C:\some\chrome-profile', script)
        self.assertIn('chrome.exe', script)
        self.assertIn('chromedriver.exe', script)
        # 绝不能出现「按进程名一把杀」那种写法：用户自己的 Chrome 也在跑
        self.assertNotIn('taskkill', script.lower())
        self.assertNotIn('Stop-Process -Name', script)

    def test_cleanup_runs_powershell_and_removes_profile_locks(self):
        if os.name != 'nt':
            self.skipTest('这段清理只在 Windows 上跑')
        calls = []
        self.patch('subprocess', types.SimpleNamespace(
            DEVNULL=backend.subprocess.DEVNULL,
            run=lambda cmd, *a, **k: calls.append(cmd),
        ))
        locks = ('SingletonCookie', 'SingletonLock', 'SingletonSocket', 'DevToolsActivePort')
        os.makedirs(backend.CHROME_PROFILE_DIR, exist_ok=True)
        for name in locks:
            with open(os.path.join(backend.CHROME_PROFILE_DIR, name), 'w', encoding='utf-8') as handle:
                handle.write('x')

        backend.cleanup_stale_browser_processes()

        self.assertEqual(len(calls), 1, calls)
        self.assertIn('powershell', calls[0][0].lower(), calls[0])
        self.assertIn('-Command', calls[0])
        self.assertIn(backend.CHROME_PROFILE_DIR, ' '.join(calls[0]))
        for name in locks:
            self.assertFalse(os.path.lexists(os.path.join(backend.CHROME_PROFILE_DIR, name)),
                             '%s 没被清掉：留着它 chromedriver 会去连一个已经死掉的调试端口' % name)

    def test_startup_cleans_up_and_retries_once(self):
        attempts = []
        cleaned = []

        class _Bootable:
            """够 ensure_browser_ready 走完最小路径的 WebDriver 替身。"""

            capabilities = {'browserVersion': '153.0.8010.54'}
            current_url = 'https://www.douyin.com/chat?isPopup=1'

            def set_window_size(self, *a, **k):
                pass

            def execute_script(self, *a, **k):
                return 'complete'

            def get(self, *a, **k):
                pass

            def find_elements(self, *a, **k):
                return []

            def get_cookies(self):
                return []

        def _chrome(*a, **k):
            attempts.append(1)
            if len(attempts) == 1:
                raise backend.SessionNotCreatedException(
                    'session not created: Chrome instance exited. Examine ChromeDriver verbose log')
            return _Bootable()

        real_time = backend.time

        class _Time:
            def __getattr__(self, name):
                return getattr(real_time, name)

            def sleep(self, *_a):
                return None

        self.patch('webdriver', types.SimpleNamespace(
            Chrome=_chrome, ChromeOptions=backend.webdriver.ChromeOptions))
        self.patch('cleanup_stale_browser_processes', lambda: cleaned.append(1))
        self.patch('time', _Time())
        self.addCleanup(lambda: setattr(backend, 'init', False))

        result = backend.ensure_browser_ready()

        self.assertIsNone(result, result)
        self.assertEqual(len(attempts), 2, '第一次启动失败后必须重试一次')
        self.assertEqual(len(cleaned), 2, '进启动路径时清一次、重试前再清一次')
        self.assertTrue(backend.init)
        self.assertIsNotNone(backend.driver)

    def test_windows_chrome_options_disable_the_elevation_relaunch(self):
        """2026-09-30：面板以管理员身份运行时，Chrome 会「另起一个进程再让原进程退出」，
        chromedriver 监视的是退出的那个，于是报 session not created: Chrome instance
        exited。真机 A/B：不加 --do-not-de-elevate → 2.7 秒失败；加了 → 6.7 秒成功。"""
        arguments = backend.build_chrome_options().arguments
        if os.name == 'nt':
            self.assertIn('--do-not-de-elevate', arguments)
        self.assertIsInstance(backend._running_elevated(), bool)

    def test_the_elevation_relaunch_reason_is_written_down(self):
        """这条坑太容易复发：注释里必须写明「提权 → Chrome 自己重启 → 原进程退出」。"""
        source = open(backend.__file__, 'r', encoding='utf-8').read()
        self.assertIn("options.add_argument('--do-not-de-elevate')", source)
        self.assertIn('提权', source)
        self.assertIn('Chrome instance exited', source)


if __name__ == '__main__':
    unittest.main()
