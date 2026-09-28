"""spark_core 的纯逻辑单元测试。

为什么用标准库的 unittest 而不是 pytest 风格：
  1. spark_core 只依赖标准库，所以这套用例必须在「CI」和「一台没装任何包的机器」
     上都能直接跑 —— `python -m unittest discover -s tests -t .` 是零依赖的；
  2. pytest 也能收集 unittest.TestCase，所以不必二选一：
       python -m unittest discover -s tests -t .
       python -m pytest -q

硬约束：这里绝对不能 import backend.py，也不能 import selenium / fastapi / requests。
  一旦引入，没有浏览器和依赖的环境就跑不了测试，这套用例存在的意义（脱离环境验证
  最关键的「发不发、什么时候发、要不要重发」规则）就没了。

关于测试方法名：用 ASCII 命名（IDE、`pytest -k`、各种日志系统都稳），
  具体场景写在紧跟方法的中文 docstring 里。
"""

import ast
import hashlib
import os
import sys
import unittest
from datetime import date, datetime, timedelta

# 保证从任意工作目录执行 `python -m unittest discover` 或 `pytest` 都能 import spark_core：
# 把仓库根目录（本文件的上一级）放进 sys.path 的最前面。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import spark_core  # noqa: E402  （必须在调整 sys.path 之后再导入）
from spark_core import (  # noqa: E402
    DEFAULT_SEND_TIME,
    ERROR_AUTH,
    ERROR_PERMANENT,
    ERROR_RATE_LIMIT,
    ERROR_TRANSIENT,
    HISTORY_KEEP_DAYS,
    PBKDF2_ITERATIONS,
    PBKDF2_PREFIX,
    SEND_COOLDOWN_SECONDS,
    STATUS_FAILED,
    STATUS_SUCCESS,
    STATUS_UNKNOWN,
    TokenBook,
    as_date,
    classify_error,
    describe_plan,
    format_time,
    guard_decision,
    hash_password,
    history_clean,
    history_key,
    is_placeholder_only,
    jitter_seconds,
    make_history_record,
    next_planned_run,
    parse_hhmm,
    parse_message_pool,
    password_policy_error,
    planned_run_at,
    render_message,
    retry_delay,
    retry_due,
    retryable,
    run_due,
    send_status_from_reason,
    should_stop_round,
    today_str,
    verify_password,
)

# 一套固定的时间基准：所有用例都不许依赖「现在几点」，否则测试会在某些时刻偶发失败。
DAY = '2026-01-15'                      # 2026-01-15 是周四
DAY_DATE = date(2026, 1, 15)
AT_22 = datetime(2026, 1, 15, 22, 0)
FAR_FUTURE = datetime(2030, 1, 1, 0, 0)


def _first(pool):
    """固定的 chooser：永远取第一条，让 render_message 的结果可断言。"""
    return pool[0]


def _boom(pool):  # pragma: no cover - 只作为「不该被调用」的哨兵
    raise AssertionError('文案池为空时不应该调用 chooser')


def _legacy_sha256(password):
    """复刻旧版的无盐 SHA-256 实现，用来造「历史遗留」的存储值。

    刻意跟 spark_core._legacy_sha256 写成一样：这个测试要验证的是
    「老数据能被认出来并要求升级」，所以必须由测试自己独立算出旧哈希，
    而不是去调被测模块的同名私有函数（那样就成了自证）。
    """
    return hashlib.sha256(str(password).encode('utf-8')).hexdigest()


class TestStdlibOnly(unittest.TestCase):
    """守住 spark_core 的「只用标准库」约定。"""

    def test_module_has_no_third_party_imports(self):
        """spark_core 一旦引入第三方库，这套「无依赖也能跑」的测试就废了。"""
        with open(spark_core.__file__, encoding='utf-8') as handle:
            tree = ast.parse(handle.read())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split('.')[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split('.')[0])
        forbidden = imported & {'selenium', 'fastapi', 'requests', 'uvicorn',
                                 'starlette', 'pydantic', 'schedule'}
        self.assertEqual(forbidden, set(), 'spark_core 不该依赖：%s' % sorted(forbidden))
        # sys.stdlib_module_names 是 3.10+ 才有的，正好也顺带验证了最低版本要求。
        self.assertTrue(imported <= set(sys.stdlib_module_names),
                        '出现了非标准库的 import：%s' % sorted(imported - set(sys.stdlib_module_names)))


class TestFormatTime(unittest.TestCase):
    """时间字符串归一化。"""

    def test_补零到两位(self):
        """'9:23' / '9:5' 这类写法要补齐成 HH:MM。"""
        self.assertEqual(format_time('9:23'), '09:23')
        self.assertEqual(format_time('9:5'), '09:05')
        self.assertEqual(format_time('09:23'), '09:23')
        self.assertEqual(format_time('0:0'), '00:00')

    def test_中文冒号也认(self):
        """用户从聊天框里复制时间，经常是中文冒号。"""
        self.assertEqual(format_time('9：23'), '09:23')
        self.assertEqual(format_time('22：00'), '22:00')

    def test_空值回落到默认时间(self):
        """没填时间就用默认的 22:00，绝不能抛异常。"""
        for empty in (None, '', '   '):
            self.assertEqual(format_time(empty), DEFAULT_SEND_TIME)

    def test_非法值回落到默认时间(self):
        """各种脏输入都要能兜住，而不是把异常抛到接口层。"""
        for bad in ('abc', '9', '9:23:45', 'x:y', 923, '2 2:00'):
            self.assertEqual(format_time(bad), DEFAULT_SEND_TIME)

    def test_越界值回落到默认时间(self):
        """24 点、60 分、负数都是无效时间。"""
        for out_of_range in ('25:00', '24:00', '12:60', '-1:00', '9:-5'):
            self.assertEqual(format_time(out_of_range), DEFAULT_SEND_TIME)

    def test_parse_hhmm(self):
        """HH:MM -> (时, 分)，解析失败按默认时间算。"""
        self.assertEqual(parse_hhmm('22:00'), (22, 0))
        self.assertEqual(parse_hhmm('9:5'), (9, 5))
        self.assertEqual(parse_hhmm('乱写'), (22, 0))
        self.assertEqual(parse_hhmm(None), (22, 0))


class TestJitterSeconds(unittest.TestCase):
    """随机偏移：同一天同一任务必须稳定，否则任务会被无限推迟。"""

    def test_same_task_same_day_is_stable(self):
        """同样参数连续调用两次必须完全相等（这是最容易写错的地方）。"""
        first = jitter_seconds('task-1', DAY, 40)
        second = jitter_seconds('task-1', DAY, 40)
        self.assertEqual(first, second)
        self.assertEqual(first, jitter_seconds('task-1', DAY, 40))

    def test_different_days_differ(self):
        """跨天要换一个偏移，否则每天都固定在同一分钟，是个机器特征。"""
        values = {jitter_seconds('task-1', '2026-01-%02d' % day, 40) for day in range(1, 8)}
        self.assertGreater(len(values), 1)

    def test_different_tasks_differ(self):
        """同一天不同任务不应该撞到同一个偏移。"""
        values = {jitter_seconds('task-%d' % index, DAY, 40) for index in range(7)}
        self.assertGreater(len(values), 1)

    def test_date_object_and_string_agree(self):
        """传 date 对象和传 'YYYY-MM-DD' 字符串要得到同样的结果。"""
        self.assertEqual(jitter_seconds('task-1', DAY_DATE, 40), jitter_seconds('task-1', DAY, 40))

    def test_zero_or_negative_window_returns_zero(self):
        """窗口为 0（或不合法）时不该有偏移，等价于「就在基准时间发」。"""
        self.assertEqual(jitter_seconds('task-1', DAY, 0), 0)
        self.assertEqual(jitter_seconds('task-1', DAY, None), 0)
        self.assertEqual(jitter_seconds('task-1', DAY, -5), 0)

    def test_result_stays_inside_window(self):
        """偏移必须落在 [0, window*60) 内，否则会跑到计划时间之外。"""
        for window in (1, 40, 1440):
            for day in ('2026-01-15', '2026-06-01', '2027-02-28', '2026-12-31'):
                value = jitter_seconds('task-1', day, window)
                self.assertGreaterEqual(value, 0)
                self.assertLess(value, window * 60)


class TestPlannedRunAt(unittest.TestCase):
    """计划触发时刻 = 基准时间 + 当天偏移。"""

    def test_without_window_equals_base_time(self):
        """窗口 0 时就应该正好等于基准时间。"""
        self.assertEqual(planned_run_at('21:00', DAY_DATE, 'task-1', 0), datetime(2026, 1, 15, 21, 0))

    def test_string_date_is_accepted(self):
        """面板传来的日期是字符串，必须能直接吃下去。"""
        self.assertEqual(planned_run_at('21:00', DAY, 'task-1', 0), datetime(2026, 1, 15, 21, 0))

    def test_window_shifts_within_range(self):
        """加了窗口后要落在 [基准, 基准+窗口) 内，且偏移量就是 jitter_seconds。"""
        base = datetime(2026, 1, 15, 21, 0)
        planned = planned_run_at('21:00', DAY, 'task-1', 40)
        self.assertGreaterEqual(planned, base)
        self.assertLess(planned, base + timedelta(minutes=40))
        self.assertEqual((planned - base).total_seconds(), jitter_seconds('task-1', DAY, 40))

    def test_invalid_base_time_falls_back(self):
        """基准时间写坏了就用默认 22:00，不能崩。"""
        self.assertEqual(planned_run_at('乱写', DAY, 'task-1', 0), datetime(2026, 1, 15, 22, 0))


class TestRunDue(unittest.TestCase):
    """到点该不该跑（含错过后的补跑判定）。"""

    def test_not_due_before_planned_time(self):
        """还没到点就不跑。"""
        self.assertFalse(run_due(AT_22, datetime(2026, 1, 15, 21, 59, 59), None, 360))

    def test_due_when_not_sent_today(self):
        """到点且今天没发过 -> 跑。"""
        self.assertTrue(run_due(AT_22, AT_22, None, 360))
        self.assertTrue(run_due(AT_22, datetime(2026, 1, 15, 23, 30), None, 360))

    def test_not_due_when_already_sent_today(self):
        """今天已经跑过 -> 不跑（一天只发一次，绝不重复打扰对方）。"""
        self.assertFalse(run_due(AT_22, datetime(2026, 1, 15, 22, 30), DAY, 360))

    def test_expired_grace_window_is_skipped(self):
        """超过宽限窗口就不补跑了，避免半夜突然补发一条。"""
        self.assertFalse(run_due(AT_22, datetime(2026, 1, 16, 4, 0, 1), None, 360))

    def test_grace_window_boundary(self):
        """正好卡在宽限窗口边界上仍算「来得及」。"""
        self.assertTrue(run_due(AT_22, AT_22 + timedelta(minutes=360), None, 360))
        self.assertFalse(run_due(AT_22, AT_22 + timedelta(minutes=360, seconds=1), None, 360))

    def test_catchup_inside_grace_window(self):
        """机器重启导致错过那一分钟，宽限窗口内要补跑。"""
        self.assertTrue(run_due(AT_22, AT_22 + timedelta(minutes=120), None, 360))

    def test_grace_none_means_no_limit(self):
        """grace_minutes 传 None 表示不限制（配置项里的「关闭」语义）。"""
        self.assertTrue(run_due(AT_22, AT_22 + timedelta(days=1), None, None))

    def test_yesterday_leftover_is_not_run(self):
        """昨天遗留的计划时间不该在今天被触发。"""
        self.assertFalse(run_due(AT_22, datetime(2026, 1, 16, 10, 0), DAY, 360))


class TestRetryDue(unittest.TestCase):
    """当日补发队列的到点判定。"""

    def setUp(self):
        self.retry_at = datetime(2026, 1, 15, 23, 0)

    def test_not_due_before_retry_time(self):
        self.assertFalse(retry_due(self.retry_at, datetime(2026, 1, 15, 22, 59), False))

    def test_due_at_and_after_retry_time(self):
        self.assertTrue(retry_due(self.retry_at, self.retry_at, False))
        self.assertTrue(retry_due(self.retry_at, self.retry_at + timedelta(minutes=5), False))

    def test_done_is_never_retried(self):
        """已经补发成功的不许再来一次。"""
        self.assertFalse(retry_due(self.retry_at, self.retry_at + timedelta(hours=6), True))

    def test_missing_retry_time_is_not_due(self):
        """没有排队时间表示没排过补发。"""
        self.assertFalse(retry_due(None, FAR_FUTURE, False))


class TestMessagePool(unittest.TestCase):
    """文案池解析。"""

    def test_multiline_and_blank_lines(self):
        """一行一条，空行（含 CRLF 残留）要忽略，首尾空格要清掉。"""
        raw = '第一条\n\n  第二条  \n\r\n第三条\r\n'
        self.assertEqual(parse_message_pool(raw), ['第一条', '第二条', '第三条'])

    def test_list_input(self):
        """面板可能直接给数组，也要支持。"""
        self.assertEqual(parse_message_pool(['a', ' b ', '']), ['a', 'b'])

    def test_empty_input_gives_empty_pool(self):
        for empty in (None, '', '  \n \n'):
            self.assertEqual(parse_message_pool(empty), [])

    def test_whitespace_only_lines_are_dropped(self):
        self.assertEqual(parse_message_pool('   \n\t\n真话\n'), ['真话'])


class TestRenderMessage(unittest.TestCase):
    """从池子里挑一条并填占位符。"""

    def setUp(self):
        # 2026-01-15 是周四，month=1、day=15，方便断言。
        self.moment = datetime(2026, 1, 15, 9, 5)

    def test_date_and_weekday_placeholders(self):
        text = render_message('{date} 是 {weekday}', now=self.moment, chooser=_first)
        self.assertEqual(text, '2026-01-15 是 周四')

    def test_month_and_day_placeholders(self):
        text = render_message('{month}月{day}日', now=self.moment, chooser=_first)
        self.assertEqual(text, '1月15日')

    def test_no_placeholder_left_behind(self):
        """占位符必须被替换干净，不能把 '{date}' 原样发出去。"""
        text = render_message('{date}{weekday}{month}{day}', now=self.moment, chooser=_first)
        self.assertNotIn('{', text)

    def test_fixed_chooser_selects_one_line(self):
        """多行池子里，chooser 决定选哪一条。"""
        raw = 'A\nB\nC'
        self.assertEqual(render_message(raw, now=self.moment, chooser=_first), 'A')
        self.assertEqual(render_message(raw, now=self.moment, chooser=lambda pool: pool[2]), 'C')

    def test_empty_pool_returns_empty_string(self):
        """空池返回空串，由调用方决定兜底文案；连 chooser 都不该被调用。"""
        self.assertEqual(render_message('', chooser=_boom), '')
        self.assertEqual(render_message(None, chooser=_boom), '')
        self.assertEqual(render_message('  \n \n', chooser=_boom), '')

    def test_placeholder_only_pool_is_detected(self):
        """纯占位符文案每天都不一样，去重要按好友算。"""
        self.assertTrue(is_placeholder_only('{date}'))
        self.assertTrue(is_placeholder_only('早安，今天是{date}，{weekday}'))
        self.assertTrue(is_placeholder_only('{date}\n{weekday}'))

    def test_plain_text_is_not_placeholder_only(self):
        """只要有一条是普通文案，整体就不算「纯占位符」。"""
        self.assertFalse(is_placeholder_only('早上好'))
        self.assertFalse(is_placeholder_only('早上好\n{date}'))
        self.assertFalse(is_placeholder_only(''))
        self.assertFalse(is_placeholder_only(None))


class TestHistoryKey(unittest.TestCase):
    """发送记账键 —— 这里曾有一个「同一天重复发送」的 bug。"""

    def test_same_day_same_friend_same_key_regardless_of_text(self):
        """回归测试：旧实现把文案哈希拼进键里，换文案就能绕过当天去重。

        现在键只看 (日期, 好友)，文案只是记录内容。
        """
        keys = {history_key(DAY, '张三') for _text in ('早上好', '晚上好', '{date} 打卡')}
        self.assertEqual(len(keys), 1)

    def test_key_is_day_pipe_name(self):
        self.assertEqual(history_key(DAY, '张三'), '2026-01-15|张三')

    def test_different_day_or_friend_is_different_key(self):
        self.assertNotEqual(history_key(DAY, '张三'), history_key('2026-01-16', '张三'))
        self.assertNotEqual(history_key(DAY, '张三'), history_key(DAY, '李四'))

    def test_empty_name_still_produces_key(self):
        self.assertEqual(history_key(DAY, None), '2026-01-15|')
        self.assertEqual(history_key(DAY, ''), '2026-01-15|')


class TestHistoryClean(unittest.TestCase):
    """记账记录过期清理。"""

    def setUp(self):
        self.history = {
            history_key('2026-01-15', 'A'): 'today',
            history_key('2026-01-09', 'A'): 'six-days-ago',
            history_key('2026-01-08', 'A'): 'seven-days-ago',
            history_key('2026-01-07', 'A'): 'eight-days-ago',
        }

    def test_keeps_recent_and_drops_expired(self):
        """保留期内留着，超过保留期丢掉（否则 state.json 会无限膨胀）。"""
        kept = history_clean(self.history, '2026-01-15', keep_days=7)
        self.assertIn(history_key('2026-01-15', 'A'), kept)
        self.assertIn(history_key('2026-01-09', 'A'), kept)
        self.assertNotIn(history_key('2026-01-07', 'A'), kept)

    def test_boundary_day_is_kept(self):
        """正好等于保留期的那天算「还在期内」，边界要一致。"""
        kept = history_clean(self.history, '2026-01-15', keep_days=7)
        self.assertIn(history_key('2026-01-08', 'A'), kept)

    def test_accepts_datetime_as_today(self):
        """面板传进来的可能是 datetime，结果必须一致。"""
        as_string = history_clean(self.history, '2026-01-15', keep_days=7)
        as_datetime = history_clean(self.history, datetime(2026, 1, 15, 10, 0), keep_days=7)
        self.assertEqual(as_string, as_datetime)

    def test_empty_history(self):
        self.assertEqual(history_clean(None, '2026-01-15'), {})
        self.assertEqual(history_clean({}, '2026-01-15'), {})

    def test_default_keep_days(self):
        self.assertEqual(HISTORY_KEEP_DAYS, 7)


class TestMakeHistoryRecord(unittest.TestCase):
    """记账记录的结构。"""

    def test_minimal_record(self):
        record = make_history_record('success', at='2026-01-15 22:00:00')
        self.assertEqual(record['status'], 'success')
        self.assertEqual(record['at'], '2026-01-15 22:00:00')
        self.assertNotIn('text', record)
        self.assertNotIn('detail', record)

    def test_text_and_detail_are_truncated(self):
        """记录会被写进 state.json，超长内容必须截断。"""
        record = make_history_record('failed', text='x' * 500, detail='y' * 500)
        self.assertEqual(len(record['text']), 200)
        self.assertEqual(len(record['detail']), 200)

    def test_default_time_is_now_formatted(self):
        record = make_history_record('unknown')
        self.assertRegex(record['at'], r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')


class TestGuardDecision(unittest.TestCase):
    """发送前的重复检查。"""

    def setUp(self):
        self.success = make_history_record('success', at='2026-01-15 22:00:00')
        self.unknown = make_history_record('unknown', at='2026-01-15 22:00:00')
        self.failed = make_history_record('failed', at='2026-01-15 22:00:00')

    def test_empty_record_always_passes(self):
        """没记录过 = 没发过，放行。"""
        for scope in ('task', 'manual'):
            self.assertIsNone(guard_decision(None, scope))
            self.assertIsNone(guard_decision({}, scope))

    def test_task_scope_blocks_success(self):
        """定时/补发：当天已经发成功过就跳过。"""
        self.assertIsNotNone(guard_decision(self.success, 'task'))

    def test_task_scope_blocks_unknown(self):
        """结果不确定（可能已送达）也要拦，绝不冒重复发送的风险。"""
        self.assertIsNotNone(guard_decision(self.unknown, 'task'))

    def test_task_scope_allows_failed_for_catchup(self):
        """失败必须放行，否则「当日补发」永远补不上。"""
        self.assertIsNone(guard_decision(self.failed, 'task'))

    def test_manual_scope_blocks_within_cooldown(self):
        """手动发送只做短冷却，防手滑连点。"""
        just_now = datetime(2026, 1, 15, 22, 1, 0)
        self.assertIsNotNone(guard_decision(self.success, 'manual', now=just_now))

    def test_manual_scope_allows_after_cooldown(self):
        later = datetime(2026, 1, 15, 22, 2, 0)
        self.assertIsNone(guard_decision(self.success, 'manual', now=later))

    def test_manual_cooldown_ignores_status(self):
        """冷却期内即使是失败的记录也要拦住连点。"""
        just_now = datetime(2026, 1, 15, 22, 0, 10)
        self.assertIsNotNone(guard_decision(self.failed, 'manual', now=just_now))

    def test_manual_scope_tolerates_broken_timestamp(self):
        """记录里的时间格式坏掉时按放行处理，不能把接口卡死。"""
        broken = {'status': 'success', 'at': '坏时间'}
        self.assertIsNone(guard_decision(broken, 'manual', now=AT_22))

    def test_manual_scope_honours_custom_cooldown(self):
        now = datetime(2026, 1, 15, 22, 0, 30)
        self.assertIsNotNone(guard_decision(self.success, 'manual', now=now, cooldown_seconds=60))
        self.assertIsNone(guard_decision(self.success, 'manual', now=now, cooldown_seconds=10))

    def test_default_cooldown_constant(self):
        self.assertEqual(SEND_COOLDOWN_SECONDS, 90)


class TestSendStatusFromReason(unittest.TestCase):
    """把发送失败文案归类成 success / failed / unknown。

    这个分类直接决定「当天要不要再补发一条」，而两种误判的代价不对称：
      * unknown 判成 failed  -> 会给对方发第二条（打扰真人，也是风控特征）
      * failed 判成 unknown  -> 当天少发一条（火花有风险，但不会重复打扰）
    所以这里把"没能确认"的各种说法都钉住。
    """

    def test_unconfirmed_wording_is_unknown(self):
        """回归：曾经调用方只匹配 '未确认'，而实际文案是「未能确认…」。

        于是"结果不确定"被记成 failed，当天就会给对方再发一条 ——
        而那条消息可能其实已经送达了。
        """
        for reason in (
            '发送状态未确认：无法读取消息列表，未能确认这条消息是否送达',
            '未能确认这条消息是否送达',
            '无法确认是否已送达',
            '发送状态未确认：消息列表里没有出现这条新消息',
        ):
            self.assertEqual(send_status_from_reason(reason), STATUS_UNKNOWN, reason)

    def test_definite_failure_is_failed(self):
        """页面明确提示失败、或好友根本不存在，属于确定失败，可以走补发。"""
        for reason in ('抖音提示这条消息发送失败（消息旁有重试标记）',
                       '会话列表里没有「小红」'):
            self.assertEqual(send_status_from_reason(reason), STATUS_FAILED, reason)

    def test_empty_reason_is_failed(self):
        self.assertEqual(send_status_from_reason(None), STATUS_FAILED)
        self.assertEqual(send_status_from_reason(''), STATUS_FAILED)

    def test_status_constants_match_history_values(self):
        """记账里写的字符串必须和 guard_decision 认的值一致。"""
        self.assertEqual(STATUS_SUCCESS, 'success')
        self.assertEqual(STATUS_FAILED, 'failed')
        self.assertEqual(STATUS_UNKNOWN, 'unknown')
        record = {'status': STATUS_UNKNOWN, 'at': '2026-09-20 22:00:00'}
        self.assertIsNotNone(guard_decision(record, 'task'),
                             'unknown 必须能挡住当天的重复发送')


class TestClassifyError(unittest.TestCase):
    """失败文案分级：决定要不要重试、要不要停轮、要不要补发。"""

    def test_risk_control_is_rate_limit(self):
        """风控/安全验证属于限流，要停下来等，不能硬撞。"""
        for message in ('检测到风控，请稍后再试', '需要完成验证', '操作频繁'):
            self.assertEqual(classify_error(message), ERROR_RATE_LIMIT)

    def test_login_lost_is_authentication(self):
        for message in ('登录状态已失效，请重新登录', '未登录'):
            self.assertEqual(classify_error(message), ERROR_AUTH)

    def test_permanent_messages(self):
        """这些错误重试一万次也没用。"""
        for message in ('会话列表里没有该好友', '好友名为空', '消息内容为空', '任务不存在'):
            self.assertEqual(classify_error(message), ERROR_PERMANENT)

    def test_transient_messages(self):
        """页面结构/超时类的抖动才值得重试。"""
        for message in ('等待元素超时', '结构发生变化', '没找到发送按钮', 'timeout waiting', '暂时无法操作'):
            self.assertEqual(classify_error(message), ERROR_TRANSIENT)

    def test_unknown_message_defaults_to_transient(self):
        """认不出来的错误按临时处理（重试代价小于漏发代价）。"""
        self.assertEqual(classify_error('说不清的报错'), ERROR_TRANSIENT)
        self.assertEqual(classify_error(None), ERROR_TRANSIENT)
        self.assertEqual(classify_error(''), ERROR_TRANSIENT)

    def test_should_stop_round(self):
        """认证失效和风控要立刻停掉本轮全部发送。"""
        self.assertTrue(should_stop_round('检测到风控'))
        self.assertTrue(should_stop_round('登录状态已失效，请重新登录'))
        self.assertFalse(should_stop_round('等待元素超时'))
        self.assertFalse(should_stop_round('好友名为空'))

    def test_only_transient_goes_to_catchup_queue(self):
        """只有临时故障才补发：不确定的和永久的补发等于给同一个人发两遍。"""
        self.assertTrue(retryable('等待元素超时'))
        self.assertFalse(retryable('好友名为空'))
        self.assertFalse(retryable('检测到风控'))
        self.assertFalse(retryable('登录状态已失效，请重新登录'))


class TestRetryPolicy(unittest.TestCase):
    """重试退避。"""

    def test_transient_backoff_sequence(self):
        """临时故障按 3.0 -> 4.5 -> 6.75 退避，之后不再重试。"""
        self.assertEqual(retry_delay(ERROR_TRANSIENT, 0), 3.0)
        self.assertEqual(retry_delay(ERROR_TRANSIENT, 1), 4.5)
        self.assertAlmostEqual(retry_delay(ERROR_TRANSIENT, 2), 6.75)

    def test_returns_none_when_attempts_exhausted(self):
        """超过最大重试次数返回 None，调用方据此停止重试。"""
        self.assertIsNone(retry_delay(ERROR_TRANSIENT, 3))
        self.assertIsNone(retry_delay(ERROR_TRANSIENT, 99))

    def test_permanent_and_auth_never_retry(self):
        """永久性错误和认证失效一次都不重试。"""
        for attempt in (0, 1, 5):
            self.assertIsNone(retry_delay(ERROR_PERMANENT, attempt))
            self.assertIsNone(retry_delay(ERROR_AUTH, attempt))

    def test_rate_limit_retries_once_with_long_wait(self):
        """限流只给一次机会，而且要等得久一点。"""
        self.assertEqual(retry_delay(ERROR_RATE_LIMIT, 0), 30.0)
        self.assertIsNone(retry_delay(ERROR_RATE_LIMIT, 1))

    def test_unknown_category_behaves_like_transient(self):
        self.assertEqual(retry_delay('不存在的分类', 0), retry_delay(ERROR_TRANSIENT, 0))


class TestPasswordHashing(unittest.TestCase):
    """密码哈希与校验（含旧格式升级）。"""

    def test_hash_format_has_prefix_and_salt(self):
        """格式：pbkdf2_sha256$迭代次数$盐$摘要，共 4 段。"""
        stored = hash_password('正确的密码123')
        self.assertTrue(stored.startswith(PBKDF2_PREFIX + '$'))
        self.assertEqual(len(stored.split('$')), 4)

    def test_hash_records_iteration_count(self):
        stored = hash_password('正确的密码123')
        self.assertEqual(int(stored.split('$')[1]), PBKDF2_ITERATIONS)

    def test_same_password_hashes_differently(self):
        """加盐的核心价值：同一个密码两次哈希必须不同（挡彩虹表）。"""
        self.assertNotEqual(hash_password('同一个密码'), hash_password('同一个密码'))

    def test_correct_password_verifies(self):
        stored = hash_password('正确的密码123')
        self.assertEqual(verify_password('正确的密码123', stored), (True, False))

    def test_wrong_password_rejected(self):
        stored = hash_password('正确的密码123')
        self.assertEqual(verify_password('错的密码', stored), (False, False))
        self.assertEqual(verify_password('', stored), (False, False))
        self.assertEqual(verify_password(None, stored), (False, False))

    def test_low_iteration_hash_requests_upgrade(self):
        """迭代次数低于当前标准的旧哈希，校验通过但要标记升级。"""
        stored = hash_password('正确的密码123', salt='固定盐', iterations=1000)
        self.assertEqual(verify_password('正确的密码123', stored), (True, True))

    def test_legacy_sha256_verifies_and_requests_upgrade(self):
        """兼容历史遗留的无盐 SHA-256：老用户登录一次就顺手升级，不被锁在门外。"""
        legacy = _legacy_sha256('123456')
        self.assertEqual(verify_password('123456', legacy), (True, True))

    def test_legacy_sha256_wrong_password(self):
        legacy = _legacy_sha256('123456')
        self.assertEqual(verify_password('654321', legacy), (False, False))

    def test_broken_stored_format_does_not_raise(self):
        """state.json 被改坏时应该返回「密码不对」，而不是把接口打成 500。"""
        broken = ('', None, 'pbkdf2_sha256$', 'pbkdf2_sha256$abc',
                  'pbkdf2_sha256$abc$salt$digest', 'pbkdf2_sha256$1000$salt$digest$extra',
                  'deadbeef' * 8, 'not-a-hash-at-all')
        for stored in broken:
            result = verify_password('whatever', stored)
            self.assertIsInstance(result, tuple)
            self.assertEqual(len(result), 2)
            self.assertFalse(result[0], '这串存储值不该被当成正确密码：%r' % (stored,))

    def test_non_ascii_stored_value_does_not_raise(self):
        """存储值含非 ASCII 时也不能抛异常，只能回「密码不对」。

        这里曾经是个真缺陷：spark_core 用 `hmac.compare_digest(str, str)` 比较，
        而它要求两边都是纯 ASCII，否则抛
        TypeError: comparing strings with non-ASCII characters is not supported。
        触发场景很真实：state.json 被手工改坏、或某次迁移把密码字段写成了中文，
        登录接口就会 500 而不是回「密码错误」。
        现已改成按 bytes 比较，所以这条用例是正常的断言。
        """
        self.assertEqual(verify_password('whatever', '随便一段乱码'), (False, False))
        self.assertEqual(verify_password('whatever', 'pbkdf2_sha256$1000$盐$摘要'), (False, False))


class TestPasswordPolicy(unittest.TestCase):
    """新密码强度校验。"""

    def test_empty_password_rejected(self):
        for empty in (None, '', '   '):
            self.assertIsNotNone(password_policy_error(empty))

    def test_too_short_rejected(self):
        self.assertIsNotNone(password_policy_error('abc123'))
        self.assertIsNotNone(password_policy_error('1234567'))

    def test_all_digits_rejected(self):
        self.assertIsNotNone(password_policy_error('12345678'))

    def test_valid_password_passes(self):
        self.assertIsNone(password_policy_error('abcd1234'))
        self.assertIsNone(password_policy_error('一个很长的中文密码'))
        self.assertIsNone(password_policy_error('12345678a'))


class TestTokenBook(unittest.TestCase):
    """面板登录 token 表。"""

    def test_issue_then_verify(self):
        book = TokenBook()
        token = book.issue()
        self.assertIsInstance(token, str)
        self.assertEqual(len(token), 64)   # token_hex(32)
        self.assertTrue(book.verify(token))
        self.assertEqual(len(book), 1)

    def test_unknown_token_rejected(self):
        book = TokenBook()
        book.issue()
        self.assertFalse(book.verify('不存在的token'))
        self.assertFalse(book.verify(None))
        self.assertFalse(book.verify(''))

    def test_idle_timeout_expires_token(self):
        """空闲超过 TTL 就失效；期间一直用则不会掉线。"""
        book = TokenBook(ttl_hours=1)
        token = book.issue(now=0.0)
        self.assertTrue(book.verify(token, now=3599.0))
        self.assertTrue(book.verify(token, now=7198.0))     # 距上次使用 3599 秒，仍有效
        self.assertFalse(book.verify(token, now=10799.0))   # 距上次使用 3601 秒，过期

    def test_no_refresh_uses_issue_time(self):
        """refresh=False 时不续期，过期时间按签发时刻算。"""
        book = TokenBook(ttl_hours=1)
        token = book.issue(now=0.0)
        self.assertTrue(book.verify(token, now=100.0, refresh=False))
        self.assertFalse(book.verify(token, now=3601.0))

    def test_zero_ttl_never_expires(self):
        """TTL=0 表示永不过期，此时 prune 不做任何事。"""
        book = TokenBook(ttl_hours=0)
        token = book.issue(now=0.0)
        self.assertTrue(book.verify(token, now=10 ** 9))
        self.assertEqual(book.prune(now=10 ** 9), 0)
        self.assertEqual(len(book), 1)

    def test_revoke_single_token(self):
        book = TokenBook()
        first, second = book.issue(), book.issue()
        book.revoke(first)
        self.assertEqual(len(book), 1)
        self.assertFalse(book.verify(first))
        self.assertTrue(book.verify(second))

    def test_revoke_all(self):
        """改密码 / 退出登录时要能把其它浏览器一起踢下线。"""
        book = TokenBook()
        tokens = [book.issue() for _ in range(3)]
        self.assertEqual(book.revoke_all(), 3)
        self.assertEqual(len(book), 0)
        for token in tokens:
            self.assertFalse(book.verify(token))

    def test_revoke_all_on_empty_book(self):
        self.assertEqual(TokenBook().revoke_all(), 0)

    def test_prune_removes_only_expired(self):
        """长期运行靠 prune 控制内存，不能误删还在用的 token。"""
        book = TokenBook(ttl_hours=1)
        stale = book.issue(now=0.0)
        fresh = book.issue(now=7200.0)
        self.assertEqual(book.prune(now=7200.0), 1)
        self.assertEqual(len(book), 1)
        self.assertFalse(book.verify(stale, now=7200.0))
        self.assertTrue(book.verify(fresh, now=7200.0))


class TestPlanHelpers(unittest.TestCase):
    """给面板展示用的计划相关小工具。"""

    def test_describe_plan_without_window(self):
        self.assertEqual(describe_plan('21:00', DAY, 'task-1', 0), '21:00')

    def test_describe_plan_with_window_annotates(self):
        """带随机窗口时要标出来，否则用户看到的「21:27」会显得莫名其妙。"""
        text = describe_plan('21:00', DAY, 'task-1', 40)
        self.assertTrue(text.endswith('（含 40 分钟随机窗口）'))
        hhmm = text.split('（')[0]
        self.assertRegex(hhmm, r'^\d{2}:\d{2}$')
        self.assertGreaterEqual(hhmm, '21:00')
        self.assertLess(hhmm, '21:40')

    def test_next_planned_run_today_when_not_passed(self):
        now = datetime(2026, 1, 15, 20, 0)
        self.assertEqual(next_planned_run('21:00', now, 'task-1', 0), datetime(2026, 1, 15, 21, 0))

    def test_next_planned_run_tomorrow_when_passed(self):
        now = datetime(2026, 1, 15, 23, 0)
        self.assertEqual(next_planned_run('21:00', now, 'task-1', 0), datetime(2026, 1, 16, 21, 0))

    def test_as_date_accepts_multiple_types(self):
        self.assertEqual(as_date(DAY), DAY_DATE)
        self.assertEqual(as_date(DAY_DATE), DAY_DATE)
        self.assertEqual(as_date(datetime(2026, 1, 15, 10, 30)), DAY_DATE)

    def test_as_date_returns_none_for_garbage(self):
        self.assertIsNone(as_date(None))
        self.assertIsNone(as_date('不是日期'))
        self.assertIsNone(as_date(''))

    def test_today_str(self):
        self.assertEqual(today_str(datetime(2026, 1, 15, 23, 59)), DAY)


class TestImagePayload(unittest.TestCase):
    """下载到的图片「到底是不是图片」的判定（决定能不能交给浏览器上传）。"""

    def test_known_magic_numbers_are_recognised(self):
        jpg = b'\xff\xd8\xff\xe0' + b'x' * 32
        png = b'\x89PNG\r\n\x1a\n' + b'x' * 32
        gif87 = b'GIF87a' + b'x' * 32
        gif89 = b'GIF89a' + b'x' * 32
        # WEBP 是容器：前 4 字节 RIFF + 长度 + 第 8~12 字节 WEBP
        webp = b'RIFF' + b'\x00\x00\x00\x00' + b'WEBP' + b'x' * 32
        self.assertEqual(spark_core.image_kind(jpg), ('.jpg', 'image/jpeg'))
        self.assertEqual(spark_core.image_kind(png), ('.png', 'image/png'))
        self.assertEqual(spark_core.image_kind(gif87), ('.gif', 'image/gif'))
        self.assertEqual(spark_core.image_kind(gif89), ('.gif', 'image/gif'))
        self.assertEqual(spark_core.image_kind(webp), ('.webp', 'image/webp'))

    def test_content_decides_not_the_url_suffix(self):
        """接口直链写着 .jpg，内容其实是 HTML 错误页 —— 必须按内容否掉。"""
        html = b'<html><body>404 not found</body></html>'
        self.assertEqual(spark_core.image_kind(html), (None, None))
        self.assertIn('不是可识别的图片',
                      spark_core.image_payload_error(html, 'image/jpeg'))
        self.assertEqual(spark_core.image_kind(b''), (None, None))
        self.assertEqual(spark_core.image_kind(None), (None, None))

    def test_empty_oversize_and_wrong_content_type(self):
        jpg = b'\xff\xd8\xff' + b'x' * 2048
        self.assertEqual(spark_core.image_payload_error(b''), '图片内容为空')
        self.assertIn('上限', spark_core.image_payload_error(jpg, 'image/jpeg', max_bytes=1024))
        self.assertIn('不是图片', spark_core.image_payload_error(jpg, 'text/html'))
        # 图床返回的 Content-Type 常带 charset，不能因此把它当成非图片
        self.assertIsNone(spark_core.image_payload_error(jpg, 'image/jpeg; charset=binary'))
        # 第三方没给 Content-Type 时只按内容判
        self.assertIsNone(spark_core.image_payload_error(jpg, ''))

    def test_default_limit_is_five_megabytes(self):
        self.assertEqual(spark_core.DEFAULT_IMAGE_MAX_BYTES, 5 * 1024 * 1024)


class TestSignText(unittest.TestCase):
    """文昌帝君灵签 → 私信文本。"""

    SIGN = {'title': '乙庚下下签',
            'poem': '田园价贯好商量\t事到公庭彼此伤\t纵使机关图得胜\t定为后世子孙殃',
            'content': '工作升迁方面，宜守不宜攻。',
            'pic': 'https://cdn.example.com/lq_17.jpg'}

    def test_tab_separated_poem_becomes_lines(self):
        """接口的 poem 用制表符分隔四句，原样发出去在手机上是一整行。"""
        text = spark_core.render_sign_text(self.SIGN)
        lines = text.split('\n')
        self.assertEqual(lines[0], '【乙庚下下签】')
        self.assertEqual(lines[1], '田园价贯好商量')
        self.assertEqual(lines[4], '定为后世子孙殃')
        self.assertEqual(lines[5], '工作升迁方面，宜守不宜攻。')
        self.assertNotIn('\t', text)

    def test_newline_separated_poem_also_supported(self):
        sign = dict(self.SIGN, poem='一句\n二句')
        self.assertEqual(spark_core.render_sign_text(sign), '【乙庚下下签】\n一句\n二句\n工作升迁方面，宜守不宜攻。')

    def test_missing_fields_never_crash_and_never_come_out_empty(self):
        self.assertEqual(spark_core.render_sign_text(dict(self.SIGN, title='', content='')),
                         '田园价贯好商量\n事到公庭彼此伤\n纵使机关图得胜\n定为后世子孙殃')
        self.assertEqual(spark_core.render_sign_text({'title': '甲子签'}), '【甲子签】')
        self.assertEqual(spark_core.render_sign_text({}), '')
        self.assertEqual(spark_core.render_sign_text(None), '')

    def test_summary_is_one_line(self):
        self.assertEqual(spark_core.sign_summary(self.SIGN), '文昌帝君灵签 · 乙庚下下签')
        self.assertEqual(spark_core.sign_summary({}), '文昌帝君灵签')
        self.assertNotIn('\n', spark_core.sign_summary(self.SIGN))

    def test_record_text_marks_the_image_and_is_never_empty(self):
        """记账键只看日期+好友，但记录文本要能一眼看出这条是图文。"""
        labeled = spark_core.image_record_text('正文', '文昌帝君灵签 · 乙庚下下签')
        self.assertIn('[图片]', labeled)
        self.assertIn('正文', labeled)
        self.assertEqual(spark_core.image_record_text('正文'), '正文')
        self.assertEqual(spark_core.image_record_text('', '文昌帝君灵签'), '[图片] 文昌帝君灵签')
        # 极端情况（既没文字也没标签）也不能返回空串：空记录会让 send_guard 的去重失去依据
        self.assertTrue(spark_core.image_record_text('', ''))


if __name__ == '__main__':
    # 允许直接 `python tests/test_core.py` 跑，不用记 discover 参数。
    unittest.main(verbosity=2)
