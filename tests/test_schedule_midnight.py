"""跨午夜定时任务 + 接口输入校验的回归测试。

为什么单独写这一批：
  1. 线上默认 SPARK_JITTER_MINUTES=40（随机窗口），而 tests/test_backend_scheduler.py
     在文件头把它设成 0（为了让时间可断言），所以「计划时刻跨过午夜」这条路径
     以前完全没有测试覆盖 —— 而它真的会让任务连着好几天不发（见下面第一个用例）。
  2. /Time/add 与 /Time/edit 的「空昵称 / 非法时间」以前是静默接受或静默替换成 22:00。

和另一个测试文件一样：往 sys.modules 里塞最小桩，并把数据目录指到临时目录，
所以只依赖标准库 + spark_core / state_store / notifier，不需要 selenium / Chrome。
"""

import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

# ---------------- 必须在 import backend 之前把环境隔离好 ----------------
_TMP = tempfile.mkdtemp(prefix='spark-test-midnight-')
os.environ['SPARK_DATA_DIR'] = os.path.join(_TMP, 'data')
os.environ['SPARK_LOG_DIR'] = os.path.join(_TMP, 'logs')
os.environ['CHROME_PROFILE_DIR'] = os.path.join(_TMP, 'chrome-profile')
# 刻意用「线上默认值」40 分钟：跨午夜正是这个窗口造成的
os.environ['SPARK_JITTER_MINUTES'] = '40'
os.environ['SPARK_CATCHUP_GRACE_MINUTES'] = '360'
os.environ['SPARK_RETRY_AFTER_MINUTES'] = '45'


def _install_stubs():
    """装上 selenium / fastapi / requests / uvicorn 的最小桩（与另一个测试文件同款）。"""
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
        raise RuntimeError('测试环境不联网')

    requests.get = _no_network
    sys.modules['requests'] = requests

    uvicorn = types.ModuleType('uvicorn')
    uvicorn.run = lambda *a, **k: None
    sys.modules['uvicorn'] = uvicorn


_install_stubs()

import backend  # noqa: E402  （必须在装完桩之后导入）
from spark_core import planned_run_at  # noqa: E402

# 找一个「基准 23:50 + 默认 40 分钟窗口」下计划时刻确实会跨到次日的固定任务ID。
# 用固定ID而不是随机ID，是为了让测试结果可复现（抖动由 (task_id, 日期) 决定）。
CROSS_MIDNIGHT_TASK_ID = None
for _i in range(500):
    _candidate = 'task-%d' % _i
    _planned = planned_run_at('23:50', datetime(2026, 3, 1).date(), _candidate, 40)
    if _planned.date() != datetime(2026, 3, 1).date():
        CROSS_MIDNIGHT_TASK_ID = _candidate
        break


class FakeDouyin:
    def __init__(self, found=True):
        self.found = found

    def Find_Friends(self, name):
        return backend.TrueString(self.found, None if self.found else '没有这位好友')


class MidnightScheduleTestCase(unittest.TestCase):
    """计划时刻跨过午夜时，任务必须「每天照发一次」，而且不能重复发。"""

    def setUp(self):
        self.enqueued = []
        with backend._task_lock:
            backend.task_meta.clear()
        backend.STATE.set('tasks', [])
        backend.STATE.set('password_hash', backend.hash_password('test-password-123'))
        backend.douyin = FakeDouyin()
        # 只替换「要碰浏览器 / 鉴权 / 真的发消息」的部分，被测的调度逻辑保持真实实现
        backend.require_browser_session = lambda: None
        backend._browser_ready_for_send = lambda: True
        backend._enqueue_send = self._fake_enqueue
        while not backend._send_queue.empty():
            backend._send_queue.get_nowait()

    def _fake_enqueue(self, name, text, kind=None, task_id=None):
        self.enqueued.append({'name': name, 'kind': kind, 'task_id': task_id})

    def _register(self, play_time, name='小明', plan_day=None):
        task_id = 'task-under-test'
        mark_today = plan_day is not None
        backend._register_task(play_time, name, '早安', task_id=task_id, mark_today=mark_today)
        if plan_day is not None:
            with backend._task_lock:
                backend.task_meta[task_id]['last_planned_date'] = plan_day
        return task_id

    def test_planned_time_crossing_midnight_fires_every_day(self):
        """核心回归：23:50 + 40 分钟窗口，连续 8 天每天都要发出去。

        修复前的行为：只有「当日计划时刻恰好落在午夜之前」的那几天才会发
        （实测 8 天只发 2 次，其余 6 天在次日 00:xx 那一跳被算成「次日 23:50 还没到」
        而永久跳过）。这条用例就是钉住那个行为。
        """
        self.assertIsNotNone(CROSS_MIDNIGHT_TASK_ID, '测试自身的前提不成立：找不到会跨午夜的 task_id')
        task_id = CROSS_MIDNIGHT_TASK_ID
        with backend._task_lock:
            backend.task_meta[task_id] = {'time': '23:50', 'name': '小明', 'text': '早安',
                                          'last_run_date': None, 'last_planned_date': None}

        fired_days = []
        crossed = 0
        for offset in range(8):
            plan_day = datetime(2026, 3, 1).date() + timedelta(days=offset)
            planned = planned_run_at('23:50', plan_day, task_id, 40)
            if planned.date() != plan_day:
                crossed += 1
            # 模拟 ticker：在计划时刻后一分钟检查一次
            backend.check_due_tasks(now=planned + timedelta(minutes=1))
            fired_days.append(len(self.enqueued))

        self.assertGreater(crossed, 0, '测试自身的前提不成立：这 8 天里没有跨午夜的计划时刻')
        self.assertEqual(len(self.enqueued), 8,
                         '8 天应当发出 8 次，实际 %d 次（每次检查后的累计：%s）'
                         % (len(self.enqueued), fired_days))
        # 每次都必须是「补跑/正常」之一，并且每天只发一次
        for offset in range(8):
            self.assertEqual(fired_days[offset], offset + 1,
                             '第 %d 天之后累计应当是 %d 次' % (offset + 1, offset + 1))

    def test_same_occurrence_never_fires_twice(self):
        """同一次（同一天）的计划时刻，ticker 反复检查也只能入队一次。"""
        task_id = self._register('21:00')
        planned = planned_run_at('21:00', datetime(2026, 4, 1).date(), task_id, 40)
        for extra in (1, 2, 3, 4, 5):
            backend.check_due_tasks(now=planned + timedelta(minutes=extra))
        self.assertEqual(len(self.enqueued), 1, '同一次发送被重复入队：%s' % self.enqueued)

    def test_normal_task_still_fires_once_per_day(self):
        """不跨午夜的普通任务：8 天 8 次，不被新逻辑改成两天一次。"""
        task_id = self._register('21:00')
        for offset in range(8):
            plan_day = datetime(2026, 4, 1).date() + timedelta(days=offset)
            planned = planned_run_at('21:00', plan_day, task_id, 40)
            backend.check_due_tasks(now=planned + timedelta(minutes=1))
            # 同一天再检查几次，确认不会重复
            backend.check_due_tasks(now=planned + timedelta(minutes=2))
        self.assertEqual(len(self.enqueued), 8, '普通任务 8 天应当发 8 次：%d' % len(self.enqueued))

    def test_task_added_after_todays_time_does_not_fire_today(self):
        """新建任务时今天的时间点已经过去 -> 记为今天已跑，不能立刻补发。"""
        today = datetime(2026, 5, 1).date()
        # 21:00 的任务在 23:00 才创建：今天不该发
        backend._register_task('21:00', '小明', '早安', task_id='task-late', mark_today=True)
        with backend._task_lock:
            backend.task_meta['task-late']['last_planned_date'] = today.isoformat()
        backend.check_due_tasks(now=datetime(2026, 5, 1, 23, 0))
        self.assertEqual(self.enqueued, [], '今天不该补发一条刚建的任务')

    def test_legacy_state_without_last_planned_date_is_respected(self):
        """老 state.json 没有 last_planned_date：也要按 last_run_date 认出「今天已发」。

        这是升级路径的保护：如果新字段缺失时直接判定「没发过」，
        所有老用户升级后当天都会重复收到一条消息。
        """
        self._register('21:00')
        today = datetime(2026, 4, 2).date()
        with backend._task_lock:
            backend.task_meta['task-under-test']['last_run_date'] = today.isoformat()
            backend.task_meta['task-under-test']['last_planned_date'] = None
        planned = planned_run_at('21:00', today, 'task-under-test', 40)
        backend.check_due_tasks(now=planned + timedelta(minutes=1))
        self.assertEqual(self.enqueued, [], '老记录（只有 last_run_date）被误判成没发过')

    def test_missed_occurrence_outside_grace_window_is_skipped(self):
        """超出补跑窗口的那一次不补（避免半夜突然补发好几条）。"""
        self._register('21:00')
        # 次日 05:00：昨天的 21:xx 已经过去 8 小时，超过 6 小时窗口；今天的 21:xx 还没到
        backend.check_due_tasks(now=datetime(2026, 4, 3, 5, 0))
        self.assertEqual(self.enqueued, [], '超过补跑窗口不该补发')


class InputValidationTestCase(unittest.TestCase):
    """接口层的输入校验（空昵称 / 非法时间 / 默认密码守卫）。"""

    def setUp(self):
        with backend._task_lock:
            backend.task_meta.clear()
        backend.STATE.set('tasks', [])
        backend.douyin = FakeDouyin()
        backend.STATE.set('password_hash', backend.hash_password('test-password-123'))
        backend.require_auth = lambda authorization=None: None
        backend.require_browser_session = lambda: None
        self.added = []
        # 保存真实实现，tearDown 里还原（不用 importlib.reload：那会把整个模块
        # 重新执行一遍，其他测试类持有的 backend 引用会指向旧模块对象，很难查）
        self._real_register_task = backend._register_task
        backend._register_task = self._capture_register

    def _capture_register(self, play_time, name, text, task_id=None, mark_today=False,
                          sign=False, source=None):
        self.added.append({'time': play_time, 'name': name, 'text': text, 'sign': sign,
                           'source': source})
        return task_id or 'fake-task-id'

    def tearDown(self):
        backend._register_task = self._real_register_task

    def test_blank_name_rejected(self):
        res = backend.add_time(payload={'time': '21:00', 'name': '   ', 'text': 'hi'})
        self.assertEqual(res.get('code'), 400, res)
        self.assertIn('好友', res.get('data') or '')
        self.assertEqual(self.added, [], '空昵称不该真的建出任务')

    def test_invalid_time_rejected(self):
        for bad in (None, '', 'zzz', '99:99', '25:00', '21:70', '21-30'):
            res = backend.add_time(payload={'time': bad, 'name': '小明', 'text': 'hi'})
            self.assertEqual(res.get('code'), 400,
                             '非法时间 %r 应当被拒绝（旧实现静默换成 22:00），实际：%s' % (bad, res))
        self.assertEqual(self.added, [], '非法时间不该建出任务')

    def test_valid_time_accepted_and_normalised(self):
        res = backend.add_time(payload={'time': '9:5', 'name': '小明', 'text': 'hi'})
        self.assertEqual(res.get('code'), 200, res)
        self.assertEqual(self.added[0]['time'], '09:05', '时间应被归一化成 HH:MM')

    def test_edit_invalid_time_rejected(self):
        backend._register_task('21:00', '小明', '早', task_id='task-edit')
        with backend._task_lock:
            backend.task_meta['task-edit'] = {'time': '21:00', 'name': '小明', 'text': '早',
                                              'last_run_date': None, 'last_planned_date': None}
        res = backend.edit_time(payload={'name': '小明', 'new_time': 'zzz'})
        self.assertEqual(res.get('code'), 400, res)
        self.assertEqual(backend.task_meta['task-edit']['time'], '21:00', '被拒绝的修改不该改到任务')

    def test_setup_guard_blocks_until_password_changed(self):
        """默认密码状态下，改密码之外的敏感动作一律 403（防止面板被接管）。"""
        backend.STATE.set('password_hash', None)   # 回到「全新安装」
        try:
            for name, res in (
                ('/Api/Notify/Set', backend.SetNotify(enabled=True, url='https://evil.example.com/hook')),
                ('/Time/add', backend.add_time(payload={'time': '21:00', 'name': '小明'})),
            ):
                self.assertEqual(res.get('code'), 403, '%s 在默认密码状态下应当被拦截：%s' % (name, res))
            self.assertEqual(self.added, [], '被拦截时不该建出任务')
        finally:
            backend.STATE.set('password_hash', backend.hash_password('test-password-123'))


class NotifyConfigTestCase(unittest.TestCase):
    """消息通知配置接口的回归测试（用户 2026-09-28 报「检查下消息通知有没有 bug」）。

    这里兜住三个真机复现过的问题：
      1. 保存本机 / 内网 webhook 时 SetNotify 没带 allow_private，环境变量
         SPARK_ALLOW_PRIVATE_PUSH=1 根本不生效 —— 而报错文案恰恰是让用户去设这个变量，
         本机 webhook（ntfy / Uptime Kuma / 内网脚本）永远存不进来。
      2. 总开关开着、推送地址和邮箱都没配，接口照样返回 200：notify() 在第一道门禁就
         静默 return False，面板上却显示「通知已开启」，真出事时一条都收不到。
      3. 信息日志页的分类下拉 = APP_LOG_CATEGORIES，只有 8 个，而日志实际用到 19 个，
         「通知」这一类的日志在页面上根本筛不出来。
    """

    def setUp(self):
        backend.STATE.set('tasks', [])
        backend.STATE.set('password_hash', backend.hash_password('test-password-123'))
        backend.require_auth = lambda authorization=None: None
        self._saved_notify = dict(backend._notify_config(force=True))
        backend.STATE.set('notify', dict(backend.DEFAULT_STATE['notify']))
        self._saved_switch = backend.ALLOW_PRIVATE_PUSH
        backend.ALLOW_PRIVATE_PUSH = False
        self.addCleanup(self._restore)

    def _restore(self):
        backend.ALLOW_PRIVATE_PUSH = self._saved_switch
        backend.STATE.set('notify', self._saved_notify)

    def test_private_push_url_follows_the_switch(self):
        res = backend.SetNotify(enabled=True, url='http://127.0.0.1:9865/hook')
        self.assertEqual(res.get('code'), 400, '默认必须拒绝本机地址：%s' % (res,))
        self.assertIn('内网', res.get('data') or '')
        backend.ALLOW_PRIVATE_PUSH = True
        res = backend.SetNotify(enabled=True, url='http://127.0.0.1:9865/hook')
        self.assertEqual(res.get('code'), 200, '开关打开后应当能存本机地址：%s' % (res,))
        self.assertEqual(backend._notify_config()['url'], 'http://127.0.0.1:9865/hook')

    def test_enabling_without_any_channel_is_rejected(self):
        res = backend.SetNotify(enabled=True, url='')
        self.assertEqual(res.get('code'), 400, res)
        self.assertIn('推送地址', res.get('data') or '')
        self.assertFalse(backend._notify_config().get('enabled'),
                         '被拒绝时不能把总开关落盘（否则面板显示已开启却发不出通知）')

    def test_enabling_with_a_saved_email_channel_is_accepted(self):
        """拦住的是「两个通道都没有」，不是「没填推送地址」。"""
        res = backend.SetNotify(enabled=True, url='', email={
            'enabled': True, 'host': 'smtp.example.com', 'port': 465,
            'username': 'me@example.com', 'password': 'secret-pass',
            'to': 'you@example.com', 'ssl': True})
        self.assertEqual(res.get('code'), 200, res)
        self.assertTrue(backend._notify_config().get('enabled'))

    def test_every_log_category_is_selectable(self):
        """分类下拉取的就是 APP_LOG_CATEGORIES，必须覆盖所有字面量分类。"""
        import ast
        with open(backend.__file__, 'r', encoding='utf-8') as fh:
            tree = ast.parse(fh.read())
        used = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Name) and func.id == 'log_event'):
                continue
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                    and isinstance(node.args[1].value, str):
                used.add(node.args[1].value)
        self.assertTrue(used, '没扫到任何 log_event 调用，说明测试本身失效了')
        missing = sorted(used - set(backend.APP_LOG_CATEGORIES))
        self.assertEqual(missing, [],
                         '这些分类在信息日志页的分类下拉里选不到：%s' % missing)

    def test_notify_worker_logs_unexpected_exceptions(self):
        """通知线程体必须兜住异常并写日志。

        以前 push() 对 `[]` 这种「合法 JSON 但不是对象」的返回体会抛 AttributeError，
        而 notify() 直接把裸函数丢进 daemon 线程：线程静默死掉、一条日志都没有，
        用户永远不知道通知其实没发出去。
        """
        logged = []

        def fake_log(level, category, message, detail=None):
            logged.append({'level': level, 'category': category, 'message': message})

        def boom(*args, **kwargs):
            raise AttributeError("'list' object has no attribute 'get'")

        original_log = backend.log_event
        original_push = backend.push
        backend.log_event = fake_log
        backend.push = boom
        try:
            backend._notify_worker({'url': 'https://push.example.com/hook'}, True, False, '标题', '内容')
        finally:
            backend.log_event = original_log
            backend.push = original_push
        self.assertTrue(any(row['level'] == 'error' and '通知发送异常' in row['message']
                            for row in logged), logged)


if __name__ == '__main__':
    unittest.main()
