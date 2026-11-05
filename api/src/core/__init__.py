"""`core` —— 与业务无关的基础设施。

子模块：
- config     配置装载（环境变量 / .env）
- logging    结构化日志
- context    request_id、当前用户等请求级上下文
- exceptions 业务错误码与异常体系
- schema     Pydantic 基座（camelCase 契约）
- response   统一响应包装与分页原语
- security   JWT / 口令哈希
"""
