#!/usr/bin/env python
"""快速创建测试数据库"""
import sqlite3
import os

# 读取schema
with open('database_schema.sql', 'r', encoding='utf-8') as f:
    schema_sql = f.read()

# 创建数据库
db_path = 'spark.db'
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# 执行schema（分批执行，因为有多个语句）
for statement in schema_sql.split(';'):
    statement = statement.strip()
    if statement:
        try:
            cursor.execute(statement)
        except Exception as e:
            print(f"执行失败: {statement[:50]}... | 错误: {e}")

conn.commit()
conn.close()

print(f"✅ 测试数据库创建成功: {db_path}")
print(f"📊 文件大小: {os.path.getsize(db_path)} 字节")
