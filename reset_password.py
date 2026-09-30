"""
面板密码重置工具（单用户版）。

用法：
    python reset_password.py                    # 交互式设置新密码（推荐，输入不回显）
    python reset_password.py --password 新密码   # 直接指定（给脚本/无人值守用）
    python reset_password.py --show             # 只看当前密码状态，不改任何东西

为什么需要它：
    面板只有一个账号，用户名固定是 admin，密码只以哈希形式存在
    data/state.json 的 password_hash 里。没有邮箱、没有短信、没有密保问题 ——
    本地单用户程序不该有那些东西，代价就是「忘了密码」在界面上没有任何入口。
    这个脚本就是那个入口。

安全性：
    - 改之前先把 state.json 原样备份成 state.json.bak-<时间戳>，改坏了可以直接换回来；
    - 用 spark_core.hash_password（加盐 PBKDF2）写入，和登录校验是同一套函数，
      不会出现「脚本说改成功了但登录还是失败」；
    - 沿用面板自己的强度规则（至少 8 位、不能是纯数字，spark_core.password_policy_error），
      免得设一个下次在设置页改密码时反而被拒的弱口令；
    - 写完立刻重新读盘、用 verify_password 回读校验一遍。
"""

from __future__ import annotations

import argparse
import getpass
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import spark_core                   # noqa: E402  （必须先加 sys.path 再导入）
from state_store import StateStore  # noqa: E402

# 这两个路径必须和 backend.py 完全一致，否则会去改另一个文件：
#   backend.py:74   DATA_DIR = os.getenv('SPARK_DATA_DIR', os.path.join(BASE_DIR, 'data'))
#   backend.py:2574 STATE_FILE = os.path.join(DATA_DIR, 'state.json')
DATA_DIR = os.getenv('SPARK_DATA_DIR') or os.path.join(BASE_DIR, 'data')
STATE_FILE = os.path.join(DATA_DIR, 'state.json')

DEFAULT_PASSWORD = '123456'   # backend.py:2604 的内置兜底密码


def load_store():
    """读状态文件；读不到或读坏了就返回 None（绝不覆盖一份我们没读懂的文件）。"""
    if not os.path.exists(STATE_FILE):
        print('[!] 找不到状态文件：%s' % STATE_FILE)
        print('    这个文件由后端第一次启动时创建。请先启动一次后端')
        print('    （python backend.py，或 .\\start-backend.ps1 / ./start-backend.sh），')
        print('    再回来重置密码 —— 否则现在写下的哈希会被随后新建的默认状态盖掉。')
        return None

    store = StateStore(STATE_FILE)
    if store.load_error:
        print('[!] 状态文件读取失败：%s' % store.load_error)
        print('    为避免把剩下的内容也一起覆盖掉，这里不做任何修改。')
        print('    请先检查 %s（内容坏了的话，同目录下会有一份 .corrupt-<时间戳> 备份）。' % STATE_FILE)
        return None
    return store


def show_status() -> int:
    """只报告现状，方便判断「到底是忘了密码，还是用户名填错了」。"""
    store = load_store()
    if store is None:
        return 1

    stored = store.get('password_hash')
    print('状态文件 ：%s' % STATE_FILE)
    if not stored:
        print('当前密码 ：没有设置过 —— 内置默认密码 %s 可以直接登录' % DEFAULT_PASSWORD)
    else:
        legacy = not str(stored).startswith(spark_core.PBKDF2_PREFIX)
        kind = '旧版无盐 SHA-256（下次登录成功会自动升级）' if legacy else '加盐 PBKDF2'
        print('当前密码 ：已改过，是自定义密码（%s）' % kind)
        print('           默认密码 %s 已经无效了；忘了的话用本脚本重置。' % DEFAULT_PASSWORD)
    print('记住用户名：admin（固定，不能改）')
    print('定时任务 ：%d 个' % len(store.get('tasks') or []))
    return 0


def backup_state_file() -> str:
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    backup = '%s.bak-%s' % (STATE_FILE, stamp)
    shutil.copy2(STATE_FILE, backup)
    return backup


def reset(new_password: str, skip_policy: bool = False) -> int:
    store = load_store()
    if store is None:
        return 1

    if not skip_policy:
        error = spark_core.password_policy_error(new_password)
        if error:
            print('[x] 新密码不合规：%s' % error)
            print('    如果只是想恢复到内置默认密码，加上 --allow-weak 参数。')
            return 1

    backup = backup_state_file()
    print('已备份原状态文件 -> %s' % backup)

    # 恢复成内置默认密码时必须写 None，而不是写 "123456" 的哈希。
    # backend.py:2607 的 _stored_password_hash() 只在 password_hash 为 None 时才算
    # 「还在用默认密码」，据此 backend.py 有三道保护：
    #   * using_default_password() -> True，登录响应带 must_change_password
    #   * setup_guard() 拦住绑定账号 / 发消息 / 建定时任务 / 改通知
    #   * check_due_tasks() 暂停定时发送
    # 如果这里存了 "123456" 的哈希，这三道保护会全部静默失效：面板照样接受
    # 123456，却再也不会要求改密 —— 等于把「还没改默认密码」这件事掩盖掉了。
    if new_password == DEFAULT_PASSWORD:
        stored_value = None
    else:
        stored_value = spark_core.hash_password(new_password)

    if not store.set('password_hash', stored_value):
        print('[x] 写入失败：%s' % store.last_error)
        print('    原文件已备份，没有损坏。')
        return 1

    # 回读校验：重新从磁盘读一遍，确认写下去的东西真的是我们要的状态。
    # 不信任内存里的对象，这样连「写成功了但落到别的文件」这种错也能抓到。
    reloaded = StateStore(STATE_FILE)
    stored_now = reloaded.get('password_hash')
    if new_password == DEFAULT_PASSWORD:
        # 这一路没有哈希可校验，要校验的是「确实清空了」，之后 backend.py 会用
        # 内置的 _password（backend.py:2604）兜底比对 123456。
        ok = stored_now is None
    else:
        ok, _needs_upgrade = spark_core.verify_password(new_password, stored_now)
    if not ok:
        print('[x] 回读校验失败：写入的状态不是预期值。')
        print('    请把 %s 换回来，并把这个问题反馈出来。' % backup)
        return 1

    if new_password == DEFAULT_PASSWORD:
        print('[√] 已恢复成内置默认密码（password_hash 已清空），并通过回读校验。')
        print('    注意：登录后发消息 / 建任务 / 绑定账号仍会被拦，')
        print('    要先去「设置」页把它改成自己的密码，才能正常使用。')
    else:
        print('[√] 密码已重置，并通过回读校验。')
    print('    用户名：admin')
    print('    现在回面板用新密码登录即可。')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description='重置抖音火花助手面板的登录密码（用户名固定为 admin）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='不带参数运行会进入交互式输入，建议优先用这种方式：密码不会留在命令行历史里。',
    )
    parser.add_argument('--password', help='直接指定新密码（会留在 shell 历史和进程列表里，慎用）')
    parser.add_argument('--show', action='store_true', help='只显示当前密码状态，不做任何修改')
    parser.add_argument('--allow-weak', action='store_true',
                        help='跳过强度校验（只用来恢复成内置默认密码 %s）' % DEFAULT_PASSWORD)
    args = parser.parse_args()

    print('=' * 56)
    print('  抖音火花助手 · 面板密码重置')
    print('=' * 56)

    if args.show:
        return show_status()

    if args.password:
        return reset(args.password, skip_policy=args.allow_weak)

    print('用户名固定为 admin。')
    print('新密码要求：至少 8 位，且不能是纯数字。')
    print()
    try:
        first = getpass.getpass('请输入新密码：')
        second = getpass.getpass('请再输入一次：')
    except (KeyboardInterrupt, EOFError):
        print('\n已取消。')
        return 1

    if first != second:
        print('[x] 两次输入不一致。')
        return 1
    if not first:
        print('[x] 密码不能为空。')
        return 1
    return reset(first)


if __name__ == '__main__':
    sys.exit(main())
