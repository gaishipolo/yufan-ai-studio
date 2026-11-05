"""业务模块包。

每个子包都是一个自包含的垂直切片：
    models.py    ORM 实体
    schema.py    出入参（camelCase 契约）
    service.py   业务规则
    router.py    HTTP 接口
    __init__.py  导出 ModuleSpec 供注册表装配

新增模块 = 复制一个目录 + 在 `api/registry.py` 的 MODULES 里加一行。
"""
