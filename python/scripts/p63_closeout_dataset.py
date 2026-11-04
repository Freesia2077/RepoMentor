"""Source-reviewed same-repository confirmation; evaluation references never enter the agent."""

import hashlib

from p63_closeout import OUT, ROOT, freeze, load

from repomentor.paths import data_root
from repomentor.repository import SnapshotStore

CONFIRM = [
    ("developit/mitt", "behavior", "mitt.emit 触发未注册的事件类型时，会创建该类型的 Map 键吗？通配符处理器还会收到什么参数？",
     "不会创建键；type 对应 handlers 为空时跳过，仍获取 '*' 并调用 handler(type, evt)。",
     [("src/index.ts", 103, 121)]),
    ("developit/mitt", "behavior", "mitt 的 on 返回 emitter 支持链式调用吗？结合方法体说明注册后的返回值。",
     "不支持；on 只执行 push/set，没有 return，返回 undefined。",
     [("src/index.ts", 66, 73)]),
    ("ai/nanoid", "behavior", "Node 入口导出的 random(-1) 与 random(0) 分别怎样处理？请区分报错与返回的数据类型。",
     "负数经过整数转换后抛 RangeError；0 分配并返回零长度 Buffer，fillRandom 的循环不执行。",
     [("index.js", 9, 26)]),
    ("ai/nanoid", "entry", "index.browser.js 的 nanoid 使用哪个随机源？源码是否包含 crypto 不可用时退回 Math.random 的路径？",
     "直接用 crypto.getRandomValues 与 Uint8Array；该函数没有 Math.random 回退。",
     [("index.browser.js", 75, 84)]),
    ("dbader/schedule", "behavior", "Scheduler.run_all 与 run_pending 在挑选和排序任务上有何区别？run_all 遍历原始列表还是副本？",
     "run_pending 挑 should_run 并 sorted；run_all 遍历 self.jobs[:] 副本，不按应运行时间筛选排序，每次任务后 sleep。",
     [("schedule/__init__.py", 89, 120)]),
    ("dbader/schedule", "cross_function", "repeat(job, *args, **kwargs) 创建的装饰器返回原始函数还是新的执行包装器？如何登记任务与附加参数？",
     "装饰器调用 job.do(decorated_function,*args,**kwargs)，然后返回原 decorated_function；repeat 返回这个装饰器。",
     [("schedule/__init__.py", 899, 913)]),
    ("more-itertools/more-itertools", "cross_function", "nth_or_last([10, 20], 8) 为什么返回 20？空输入且不提供 default 时的错误由哪个辅助函数产生？",
     "对 islice(iterable,n+1) 调 last；不足 n+1 时返回已有最后项；空时 last 识别 _marker 抛 ValueError。",
     [("more_itertools/more.py", 280, 319)]),
    ("more-itertools/more-itertools", "behavior", "recipes.consume(iterator, n=None) 和指定 n 时分别怎样推进迭代器？剩余不足 n 项时会返回已消费元素的列表吗？",
     "None 用零容量 deque 耗尽；指定 n 用 next(islice(iterator,n,n),None) 推进，耗尽也不返回元素列表，函数隐式 None。",
     [("more_itertools/recipes.py", 166, 203)]),
    ("pallets/flask", "cross_function", "JSONProvider.load(fp) 如何复用 loads？若子类覆盖 loads，load 会绕过它直接调用 json.load 吗？",
     "先 fp.read 再 self.loads(...,**kwargs)，会分派子类覆盖方法；默认提供者 loads 才调用 json.loads。",
     [("src/flask/json/provider.py", 59, 73), ("src/flask/json/provider.py", 181, 187)]),
    ("pallets/flask", "cross_function", "DefaultJSONProvider.response 如何按 compact 与 debug 选择 indent 或 separators？最终响应体会附加换行吗？",
     "compact None 且debug或compact False时indent2，否则separators紧凑；调用self.dumps并在响应体末尾加换行。",
     [("src/flask/json/provider.py", 189, 215)]),
    ("psf/requests", "cross_function", "merge_hooks 如何处理 request_hooks 或 session_hooks 的 response=[]？两边均有钩子时通过哪个函数合并，谁优先？",
     "先判session None或空response则返回request；否则request None或空返回session；其余merge_setting，以request覆盖session。",
     [("src/requests/sessions.py", 61, 103)]),
    ("psf/requests", "behavior", "SessionRedirectMixin.rebuild_method 对 HEAD、POST、PUT 遇到 301/302/303 时怎样改变请求方法？是否所有重定向都改成 GET？",
     "303/302除HEAD改GET；301仅POST改GET，HEAD/PUT保持；不是所有重定向都改GET。",
     [("src/requests/sessions.py", 333, 353)]),
]


def prepare_dataset():
    known = load(ROOT / "experiments/p63/confirmation.json")
    store = SnapshotStore(data_root() / "corpus")
    tasks, references = [], []
    for i, (repo, kind, question, expected, ranges) in enumerate(CONFIRM, 1):
        source = next(t for t in known if t["repository"] == repo)
        task = {"task_id": f"close-confirm-{i:02}", "repository": repo, "snapshot_id": source["snapshot_id"],
                "commit_sha": source["commit_sha"], "split": "same-repository-confirmation", "kind": kind,
                "input": {"question": question}, "training_eligible": False}
        tasks.append(task)
        refs = []
        reader = store.open(task["snapshot_id"])
        for path, first, last in ranges:
            lines = reader.read(path, 500_000)[0].splitlines()
            assert 1 <= first <= last <= len(lines)
            text = "\n".join(lines[first-1:last])
            refs.append({"path": path, "start_line": first, "end_line": last,
                         "content": text, "content_sha256": hashlib.sha256(text.encode()).hexdigest()})
        references.append({"task_id": task["task_id"], "expected": expected, "references": refs,
                           "reviewer": "Codex source-reviewed before generation", "human_approved": False,
                           "training_eligible": False})
    freeze(OUT / "confirmation.json", tasks)
    freeze(OUT / "evaluation-only/confirmation.json", references)


if __name__ == "__main__":
    prepare_dataset()
