import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useTaskFeed } from '@/entities/task/api/task.queries';
import { toast } from '@/shared/lib/toast';
import {
  completedSince,
  completionMessage,
  toSnapshotMap,
  type TaskSnapshot,
} from './taskCompletion';

/**
 * 生成完成的到达提醒（挂在 `shell.overlay`，跟页面无关）。
 *
 * ## 为什么需要它
 *
 * 视频任务要跑几十秒到几分钟，用户提交完通常会切走。回来时任务中心里只是
 * 多了一条记录 —— 没有「好了」的信号。toast 解决的就是这一段空白。
 *
 * ## 为什么不建通知表
 *
 * 通知的内容本来就在任务表里：`useTaskFeed` 已经会「有在跑的任务才轮询、
 * 全部落定就停」，这里只是在它的数据上做一次差集。用 feed 而不是 `/tasks`，
 * 是因为后者只回视频任务 —— 生图完成会永远没有提醒。落一张通知表意味着
 * 多一套未读、未读数、保留期、清理任务，而它要回答的问题，任务列表已经答了。
 *
 * ## 三个必须处理的边界
 *
 * 1. **首次挂载不弹**：第一次拿到的快照里，早就完成的历史任务不该再报一遍；
 * 2. **同一任务只弹一次**：轮询期间同一条会被反复读到；
 * 3. **标签页隐藏时不弹**：用户看不见，弹了也白弹；而且此时**不更新快照**，
 *    等他回来时才能把「离开期间完成的」补上（否则就永久丢了）。
 */
export function TaskCompletionWatcher() {
  const navigate = useNavigate();
  /* feed 而不是 /tasks：后者只有视频任务，生图完成会永远没有提醒 */
  const { data } = useTaskFeed({ scope: 'mine', pageSize: 50 });
  const previous = useRef<Map<string, TaskSnapshot> | null>(null);
  const notified = useRef<Set<string>>(new Set());
  /* 可见性要做成 state 而不只是当场读一次：人在后台时任务完成，此刻不弹也不推进
     基线；等他切回来，`data` 引用没变、effect 不会自己重跑 —— 必须靠这个 state
     的翻转把 effect 叫醒，把「离开期间完成的」补报上。 */
  const [visible, setVisible] = useState(
    () => typeof document === 'undefined' || document.visibilityState !== 'hidden',
  );

  useEffect(() => {
    const sync = () => setVisible(document.visibilityState !== 'hidden');
    document.addEventListener('visibilitychange', sync);
    return () => document.removeEventListener('visibilitychange', sync);
  }, []);

  useEffect(() => {
    const items = data?.items;
    if (!items) return;

    const current: TaskSnapshot[] = items.map((task) => ({
      key: task.key,
      group: task.group,
      title: task.title,
    }));

    const before = previous.current;
    if (before === null) {
      // 首次只记录基线
      previous.current = toSnapshotMap(current);
      return;
    }
    if (!visible) {
      // 不更新基线：人在后台时结束的任务，回来时要能补报
      return;
    }

    const done = completedSince(before, current).filter(
      (item) => !notified.current.has(item.key),
    );
    previous.current = toSnapshotMap(current);
    if (!done.length) return;

    for (const item of done) notified.current.add(item.key);

    const open = { label: '查看任务', onClick: () => navigate('/tasks') };
    if (done.length === 1) {
      const [one] = done;
      toast({
        key: `task:${one.key}`,
        tone: one.group === 'completed' ? 'success' : 'error',
        message: completionMessage(one),
        action: open,
      });
      return;
    }
    // 一次回来发现自己错过了三条：一条汇总就够，别糊满屏幕
    const failed = done.filter((item) => item.group === 'failed').length;
    toast({
      tone: failed ? 'warning' : 'success',
      message: failed
        ? `${done.length} 个任务已结束，其中 ${failed} 个失败`
        : `${done.length} 个任务已完成`,
      action: open,
    });
  }, [data, navigate, visible]);

  return null;
}
