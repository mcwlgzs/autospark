#!/usr/bin/env python
"""
AutoSpark v2.2 功能验证脚本
快速测试新增模块是否正常工作
"""

import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(__file__))

print("=" * 70)
print("AutoSpark v2.2 功能验证")
print("=" * 70)

# 测试1：设备指纹模块
print("\n[1/4] 测试设备指纹模块...")
try:
    from device_fingerprint import DeviceFingerprint, FingerprintManager

    # 测试稳定指纹生成
    fp1 = DeviceFingerprint.generate_stable_fp("test_seed")
    fp2 = DeviceFingerprint.generate_stable_fp("test_seed")
    assert fp1 == fp2, "相同seed应生成相同指纹"
    print(f"  ✅ 稳定指纹生成: {fp1[:16]}...")

    # 测试抖音风格指纹
    dy_fp = DeviceFingerprint.generate_douyin_fp()
    assert dy_fp.startswith("verify_"), "应以verify_开头"
    print(f"  ✅ 抖音指纹生成: {dy_fp}")

    # 测试管理器
    manager = FingerprintManager()
    fp = manager.get_or_create_fp(account_id=999, seed="test_999")
    print(f"  ✅ 指纹管理器: 已缓存 {len(manager.cache)} 个指纹")

    print("  ✅ 设备指纹模块正常")

except Exception as e:
    print(f"  ❌ 设备指纹模块异常: {e}")
    import traceback
    traceback.print_exc()

# 测试2：Cookie监控模块
print("\n[2/4] 测试Cookie监控模块...")
print("  [跳过] cookie_monitor.py 已随 v3 多用户代码移除")

# 测试3：浏览器进程管理模块
print("\n[3/4] 测试浏览器进程管理模块...")
try:
    from browser_manager import BrowserProcessManager, BrowserProcessInfo
    import asyncio

    # 创建管理器
    manager = BrowserProcessManager()
    print(f"  ✅ 进程管理器创建成功")

    # 测试统计
    stats = manager.get_statistics()
    print(f"  ✅ 统计信息: 活跃进程={stats['total_count']}, "
          f"总内存={stats['total_memory_mb']:.2f}MB")

    print("  ✅ 浏览器进程管理模块正常")

except Exception as e:
    print(f"  ❌ 浏览器进程管理模块异常: {e}")
    import traceback
    traceback.print_exc()

# 测试4：行为模拟增强
print("\n[4/4] 测试行为模拟增强...")
try:
    from selenium_stealth import HumanBehaviorSimulator

    # 测试阅读时间计算
    content = "这是一段测试文本" * 50  # 约500字
    reading_time = HumanBehaviorSimulator.simulate_reading_time(len(content))
    print(f"  ✅ 阅读时间计算: {len(content)}字 → {reading_time:.2f}秒")

    # 测试人类延迟
    import time
    start = time.time()
    HumanBehaviorSimulator.simulate_human_delay(0.1, 0.2)
    elapsed = time.time() - start
    print(f"  ✅ 人类延迟: {elapsed:.3f}秒")

    # 测试打字错误模拟
    text = "测试文本输入"
    result = HumanBehaviorSimulator.add_random_typos(text, typo_rate=0.5)
    print(f"  ✅ 打字错误模拟: '{text}' → '{result}'")

    print("  ✅ 行为模拟增强模块正常")

except Exception as e:
    print(f"  ❌ 行为模拟增强模块异常: {e}")
    import traceback
    traceback.print_exc()

# 测试5：依赖检查
print("\n[5/5] 检查依赖...")
try:
    import psutil
    print(f"  ✅ psutil {psutil.__version__}")
except ImportError:
    print(f"  ⚠️  psutil 未安装 (运行: pip install psutil)")

try:
    import aiosqlite
    print(f"  ✅ aiosqlite 已安装")
except ImportError:
    print(f"  ⚠️  aiosqlite 未安装 (异步数据库需要)")

try:
    from selenium import webdriver
    print(f"  ✅ selenium 已安装")
except ImportError:
    print(f"  ❌ selenium 未安装")

try:
    import undetected_chromedriver as uc
    print(f"  ✅ undetected-chromedriver 已安装")
except ImportError:
    print(f"  ⚠️  undetected-chromedriver 未安装 (推荐)")

# 总结
print("\n" + "=" * 70)
print("验证完成！")
print("=" * 70)
print("\n📝 后续步骤:")
print("  1. 如有模块异常，检查导入路径")
print("  2. 安装缺失的依赖: pip install psutil aiosqlite")
print("  3. 执行数据库升级: 运行 V2.2_SUMMARY.md 中的SQL")
print("  4. 参考 INTEGRATION_GUIDE.md 集成到现有代码")
print("  5. 查看 UPGRADE_V2.2.md 了解详细功能")
print("\n🎉 AutoSpark v2.2 准备就绪！")
