"""LibTV 浏览器执行器（独立子进程 + Node 桥接 + 固定版适配器）。

这个包**不是驱动**：`drivers/` 只扫描一级 .py 找 META/MODELS/Driver，
放这里的监督进程与状态模型不会被自动载入。进程入口是
`python -m modules.model_driver.libtv.process`，由 `executor.execute_stage()` 拉起。
"""
