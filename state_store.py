"""面板状态持久化。

只解决一件事：**重启不要丢配置**。之前的实现里定时任务和登录密码都只在内存里，
服务一重启（部署、崩溃、机器重启）就全部还原成默认值。

存 data/state.json：
    password_hash  登录密码哈希
    tasks          定时任务原始字段（内存里的 schedule.Job 无法序列化）
    notify         消息通知配置（推送地址、开关）
    send_history   发送记账（用于防重复发送）

写盘用「临时文件 + os.replace」原子替换：进程被判死或断电时，
要么是旧的完整文件，要么是新的完整文件，不会出现半截 JSON 导致状态全丢。
"""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime


class StateStore:
    """小体量状态存储：整体读入内存，改动即原子落盘。"""

    def __init__(self, path, defaults=None):
        self.path = path
        self.defaults = dict(defaults or {})
        self.load_error = None
        self.last_error = None      # 最近一次落盘失败的原因（None = 一切正常）
        self._lock = threading.RLock()
        self._data = dict(self.defaults)
        self.load()

    def load(self):
        """读取状态文件。文件损坏时备份并重建，绝不让程序起不来。"""
        with self._lock:
            try:
                with open(self.path, 'r', encoding='utf-8') as handle:
                    data = json.load(handle)
            except FileNotFoundError:
                self._data = dict(self.defaults)
                return
            except Exception as exc:
                backup = '%s.corrupt-%s' % (self.path, datetime.now().strftime('%Y%m%d-%H%M%S'))
                try:
                    os.replace(self.path, backup)
                    self.load_error = '%s（原文件已备份为 %s）' % (exc, os.path.basename(backup))
                except Exception:
                    self.load_error = str(exc)
                self._data = dict(self.defaults)
                return
            if not isinstance(data, dict):
                # 顶层不是对象（被写成数组/字符串）：备份 + 提示，和「半截 JSON」分支保持一致。
                # 旧实现只改内存不备份，用户配好的密码和通知配置会凭空消失且无从恢复。
                backup = '%s.corrupt-%s' % (self.path, datetime.now().strftime('%Y%m%d-%H%M%S'))
                try:
                    os.replace(self.path, backup)
                    self.load_error = '状态文件格式不对（顶层不是对象，原文件已备份为 %s），已按默认值启动' % os.path.basename(backup)
                except Exception as exc:
                    self.load_error = '状态文件格式不对（顶层不是对象）：%s' % exc
                self._data = dict(self.defaults)
                return
            merged = dict(self.defaults)
            merged.update(data)
            self._data = merged

    def get(self, key, default=None):
        with self._lock:
            value = self._data.get(key, default)
            return value

    def set(self, key, value):
        with self._lock:
            self._data[key] = value
            return self.save()

    def update(self, **items):
        with self._lock:
            self._data.update(items)
            return self.save()

    def snapshot(self):
        with self._lock:
            return dict(self._data)

    def save(self):
        """原子写入：先写同目录临时文件，再整体替换。

        返回是否写成功。旧实现把异常整个吞掉（`except Exception: return False`），
        而调用方全部忽略返回值 —— 磁盘满或目录没权限时，改密码、增删任务、
        改通知配置都会「提示成功但根本没落盘」，重启才发现在骗人。
        现在失败时记在 last_error 里，并让调用方能拿到 False。
        """
        with self._lock:
            payload = self._data
            temporary = None
            try:
                directory = os.path.dirname(self.path) or '.'
                os.makedirs(directory, exist_ok=True)
                # 临时文件名必须带随机后缀：只带 pid 的话，同一个进程的两个 StateStore
                # 实例（或先前的残留文件）会撞名，出现「拒绝访问 / 写入互相覆盖」。
                temporary = '%s.tmp-%d-%s' % (self.path, os.getpid(), os.urandom(4).hex())
                with open(temporary, 'w', encoding='utf-8') as handle:
                    json.dump(payload, handle, ensure_ascii=False, indent=2)
                    handle.flush()
                    os.fsync(handle.fileno())
                # 里面存着密码哈希和推送 token，尽量只让属主可读（Windows 上是空操作）
                try:
                    os.chmod(temporary, 0o600)
                except OSError:
                    pass
                os.replace(temporary, self.path)
                self.last_error = None
                return True
            except Exception as exc:
                self.last_error = str(exc)
                # 失败时清掉半截临时文件：否则每次序列化失败都在 data/ 里留一份垃圾，
                # 而且那份半截 JSON 里可能带着刚写的敏感字段。
                if temporary:
                    try:
                        os.remove(temporary)
                    except OSError:
                        pass
                return False
