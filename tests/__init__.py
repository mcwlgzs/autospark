"""测试包。

为什么要有这个文件（而不是靠命名空间包）：
  `python -m unittest discover -s tests -t .` 要求 tests 是一个真正的包，
  否则 unittest 会因为「起始目录和顶层目录不一致」而拒绝导入用例；
  加上这个文件后，两种收集方式都稳：
    python -m unittest discover -s tests -t .
    python -m pytest -q

这里刻意不放任何公共夹具：测试全部只依赖标准库，
任何额外的共享状态都会让「无 pip 的机器也能跑」这条前提变脆。
"""
