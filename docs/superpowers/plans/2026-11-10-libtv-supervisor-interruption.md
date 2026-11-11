# LibTV 监督进程中断修复计划

**目标：** 安全守卫阻止锁清理时，保留真实进程状态和操作回执，明确说明查询中断的原因。

**方案：** 保留 Node 文件锁及 Python OS 租约。Node 退出后先持久化 `finished`，再清理确实存在的遗留锁；清理异常不得导致状态仍为 `running`。监督入口通过 `finally` 保存失败回执，但继续传播终止信号。启动方读取本次独立日志，识别安全守卫的明确错误标记，停止自动重试并提示处理服务环境后恢复原任务。

**边界：** 不改变删除守卫，不绕过拦截，不重试被拒绝的删除，不改变提交/计费逻辑，不修改生产任务或清理生产锁。普通进程崩溃仍保留原错误类型；无法读取诊断日志时不覆盖原故障。

**涉及文件：** `api/src/infra/ai/libtv_process.py`、`api/src/infra/ai/libtv.py`、`api/tests/test_libtv_executor_failures.py`。

- [x] 添加回归：清理时 SystemExit 前已经保存 finished，进程终止仍有操作回执，不吞掉终止信号。
- [x] 添加回归：缺少回执或已有失败回执时，安全守卫日志映射为明确且不可自动重试的执行环境故障；未知崩溃仍保留诊断路径。
- [x] 添加回归：锁不存在时不做重复清理；清理被拒绝只尝试一次，保留锁文件。
- [x] 运行新增测试确认当前实现失败，再实现上述最小修改。原实现 6 项失败，修改后通过。
- [x] 运行 LibTV 协议、故障、计费及任务恢复相关回归和 Ruff 检查，检查 diff。

**验收：** 中断场景可诊断、有回执、无自动重提；新测试先失败后通过。真实环境的删除守卫仍会阻止未获批准的操作，恢复生产任务需先处理服务环境。

验证：`test_libtv_executor_failures`、`test_libtv_protocol`、`test_libtv_billing`、`test_replacement_recovery`、`test_replacement_guards`、`test_replacement_timing`、`test_generation_recovery`、`test_generation_state_machine`、`test_startup_recovery` 共 103 项通过；修改的三个 Python 文件 Ruff 检查及格式检查通过。存在既有 pytest-asyncio 和 Starlette 依赖弃用告警。独立代码审查未发现需处理的问题。

## 运行环境处理

本次修改没有重启 API，也没有修改共享盘任务。部署新代码后，若仍出现 `EXECUTOR_ENVIRONMENT_BLOCKED`，先核对 API 服务的启动方式、运行账户和删除操作授权，按运行环境的正常授权流程处理；不清除守卫、不更换删除 API 来绕过拦截。核实原 Node 已退出后，再恢复原任务的查询。已有 `task_id` 不应重新创建生成任务。

`finally` 能保留 Python 异常和 SystemExit 场景的回执，无法保证在强制杀进程或操作系统直接终止时执行；这种场景仍由启动方依照本次独立日志诊断。若日志中没有明确守卫标记，保留普通崩溃类型，不推测为安全守卫错误。
