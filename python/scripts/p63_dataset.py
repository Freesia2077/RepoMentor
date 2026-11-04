"""Frozen P6.3 inputs and source-reviewed evaluation references; never imported by the agent."""

import json
from pathlib import Path

from repomentor.paths import data_root
from repomentor.repository import SnapshotStore
from repomentor.runtimes.chat import fingerprint

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/p63"


def ref(path, start, end):
    return {"path": path, "start_line": start, "end_line": end}


# Questions are distinct from the old generation cases. Same-repository confirmation, not unseen repositories.
CONFIRM = {
    "developit/mitt": [
        ("连续两次 on('x', handler) 后 off('x', handler) 一次，再 emit('x')，handler 会调用几次？", "behavior", "一次；on 允许重复，off 仅 splice 一项。", [ref("src/index.ts", 66, 91)]),
        ("off('x') 不传 handler 会删除 Map 的键吗？与 off('x', missingHandler) 有什么不同？", "behavior", "前者保留键并设置空数组；后者 indexOf=-1 经无符号位移后不删除其他处理器。", [ref("src/index.ts", 82, 91)]),
        ("emit 如何调用普通处理器和通配符处理器？它们接收的参数和顺序有何差异？", "cross_function", "先普通 handler(evt)，后通配符 handler(type,evt)，两组均 slice 后遍历。", [ref("src/index.ts", 103, 121)]),
        ("执行 mitt(existingMap) 后 emitter.all 与 existingMap 是否同一对象？注册事件会修改传入的 Map 吗？", "cross_function", "同一对象，all=all||new Map；on 通过 set/push 修改共享容器。", [ref("src/index.ts", 46, 73)]),
    ],
    "ai/nanoid": [
        ("Node 默认 nanoid(0) 会分配新的随机字节池吗？沿默认导出定位返回路径。", "cross_function", "默认导出 customAlphabet(urlAlphabet)；size=0 在分配/补池前直接返回空串。", [ref("index.js", 133, 148), ref("index.js", 177, 177)]),
        ("Node customAlphabet 遇到字符码大于 255 的 alphabet，会继续使用 latin1 快速路径吗？", "cross_function", "不会；循环检查字符码并委派 customRandom。", [ref("index.js", 96, 115)]),
        ("non-secure 的 customAlphabet 与默认 nanoid 分别怎样使用 Math.random 选择字符？", "comparison", "自定义用 alphabet.length，默认用 urlAlphabet 与 64；均按位取整索引。", [ref("non-secure/index.js", 5, 29)]),
        ("仅依据仓库源码，能确定我线上服务今天生成的所有 nanoid 都没有碰撞吗？", "unanswerable", "不能；缺少线上数据及检测结果，随机实现不证明实际无碰撞。", []),
    ],
    "dbader/schedule": [
        ("Scheduler.clear(tag) 如何保留未打该标签的任务？它是否替换 self.jobs 列表对象？", "behavior", "切片赋值保留列表对象，只留下 tag 不在 job.tags 的任务。", [ref("schedule/__init__.py", 135, 148)]),
        ("schedule.cancel_job(job) 取消一个不在列表中的任务会向调用者抛 ValueError 吗？追到 Scheduler 的实现。", "cross_function", "模块包装转发到默认 scheduler；remove 的 ValueError 被捕获，仅记录日志。", [ref("schedule/__init__.py", 150, 160), ref("schedule/__init__.py", 878, 882)]),
        ("任务函数返回 CancelJob 类与返回 CancelJob() 实例，Scheduler._run_job 的处理相同吗？", "cross_function", "isinstance 或 ret is CancelJob 均调用 cancel_job 删除任务。", [ref("schedule/__init__.py", 150, 175)]),
        ("Scheduler.idle_seconds 在没有任务和任务已经过期两种情况下分别返回什么？它会把负数截为零吗？", "cross_function", "无任务 next_run 为 None；否则计算时间差，可为负，不截零。", [ref("schedule/__init__.py", 177, 206)]),
    ],
    "more-itertools/more-itertools": [
        ("chunked([1, 2, 3], 2, strict=True) 在创建迭代器时立即报错吗？第一个分组能否先被取出？", "behavior", "长度不整除的错误在迭代 ret 时发生，先 yield [1,2]，末组之前 ValueError。", [ref("more_itertools/more.py", 215, 250)]),
        ("first([], None) 和 first([]) 有什么区别？为何显式 None 不等同于未提供 default？", "behavior", "前者返回 None；后者 default 是 _marker 抛 ValueError，用身份判断区分。", [ref("more_itertools/more.py", 253, 277)]),
        ("chunked 是如何借助 recipes.take 消费输入的？输入不足 n 个时会用 None 填满吗？", "cross_function", "partial(take,n,iter(iterable))，迭代至 []；take=list(islice(...)) 不补齐。", [ref("more_itertools/more.py", 233, 250), ref("more_itertools/recipes.py", 113, 126)]),
        ("仓库能否告诉我团队昨天调用 chunked 处理了一共多少条生产日志？", "unanswerable", "不能；需要未提供的生产遥测。", []),
    ],
    "pallets/flask": [
        ("DefaultJSONProvider.dumps 显式传 sort_keys=False 时，类属性 sort_keys=True 会覆盖它吗？", "behavior", "不会；kwargs.setdefault 仅填缺省，显式 False 传给 json.dumps。", [ref("src/flask/json/provider.py", 150, 179)]),
        ("JSONProvider.response 同时收到位置参数和关键字参数时会怎样？哪个辅助函数确定这个行为？", "cross_function", "_prepare_response_obj 检查两者同时非空并抛 TypeError，response 调用它。", [ref("src/flask/json/provider.py", 75, 106)]),
        ("Blueprint.send_static_file 没有配置 static_folder 时是否继续调用 send_from_directory？有配置时 max_age 从哪里来？", "cross_function", "未配置先抛 RuntimeError；有配置调用 self.get_send_file_max_age 后传 max_age。", [ref("src/flask/blueprints.py", 55, 102)]),
        ("JSONProvider.dump 和 dumps 的返回与写入行为有何不同？dump 是否重用子类的 dumps？", "cross_function", "dump 执行 fp.write(self.dumps(...))，返回 None；dumps 返回字符串，基类未实现由子类实现。", [ref("src/flask/json/provider.py", 41, 57)]),
    ],
    "psf/requests": [
        ("merge_setting 合并两个字典时哪个优先？请求字典中值为 None 的键会保留还是删除？", "behavior", "先会话再请求覆盖；合并后删除所有值为 None 的键。", [ref("src/requests/sessions.py", 61, 88)]),
        ("Session.mount 与 get_adapter 如何共同确保更长 URL 前缀优先？比较前缀时区分大小写吗？", "cross_function", "mount 按前缀长度降序；get_adapter 顺序匹配首个，url/prefix 均 lower。", [ref("src/requests/sessions.py", 781, 808)]),
        ("get_encoding_from_headers 在没有 charset 的 text/plain 和 application/json 下分别选什么编码？缺少 content-type 呢？", "behavior", "text 返回 ISO-8859-1，application/json 返回 utf-8；缺 content-type 返回 None。", [ref("src/requests/utils.py", 529, 551)]),
        ("get_adapter 没有找到适配器抛出的异常，能同时被 ValueError 和 RequestException 捕获吗？结合异常声明说明。", "cross_function", "抛 InvalidSchema，声明多重继承 RequestException 和 ValueError，两种 except 均可捕获。", [ref("src/requests/sessions.py", 781, 792), ref("src/requests/exceptions.py", 103, 104)]),
    ],
}


def prepare():
    from run_rag_quality import frozen, sha

    from repomentor.sdk_agent import sdk_version

    if (OUT / "protocol.json").exists():
        return json.loads((OUT / "protocol.json").read_text(encoding="utf-8"))
    corpus = json.loads((ROOT / "experiments/p0/corpus.json").read_text(encoding="utf-8"))
    repos = {r["repository"]: r for r in corpus["repositories"] if r["split"] == "validation"}
    ext = json.loads((ROOT / "experiments/p6-d-external/tasks.json").read_text(encoding="utf-8"))
    repos.update({r["repository"]: r for r in ext})
    store = SnapshotStore(data_root() / "corpus")
    tasks, gold = [], {}
    for repo, rows in CONFIRM.items():
        meta = repos[repo]
        reader = store.open(meta["snapshot_id"])
        for i, (q, kind, answer, refs) in enumerate(rows, 1):
            tid = repo.split("/")[-1] + f"-confirm-{i:02}"
            for source in refs:
                content, truncated = reader.read(source["path"], 500_000)
                assert not truncated and source["end_line"] <= len(content.splitlines())
            tasks.append({"task_id": tid, "repository": repo, "snapshot_id": meta["snapshot_id"],
                "commit_sha": meta["commit_sha"], "split": "confirmation-development", "kind": kind,
                "input": {"question": q}, "question_sha256": fingerprint(q)})
            gold[tid] = {"reference_answer": answer, "sources": refs, "answerable": bool(refs)}
    frozen(OUT / "confirmation.json", tasks)
    frozen(OUT / "evaluation-only/confirmation.json", gold)
    # Development questions include six external failures and the historical six scenarios below.
    development = [{**t, "kind": "external"} for t in ext]
    historical = [
        ("ai/nanoid", "Node nanoid 负数长度如何处理，test/index.test.js 如何验证？"),
        ("ai/nanoid", "那么它是返回空字符串还是抛错？请沿用刚才的入口，并说明实现依据。"),
        ("ai/nanoid", "切换到 non-secure 入口，nanoid(-10) 的结果与 Node 默认入口一样吗？"),
        ("developit/mitt", "mitt 的 on 会把事件名统一转成小写吗？结合实现与 should NOT normalize case 测试说明。"),
        ("dbader/schedule", "get_jobs() 不传 tag 返回内部列表本身吗？"),
        ("ai/nanoid", "我们公司生产环境昨天用 nanoid 生成了多少个 ID，碰撞次数是多少？"),
    ]
    for i, (repo, q) in enumerate(historical, 1):
        meta = repos[repo]
        development.append({"task_id": f"history-dev-{i:02}", "repository": repo,
            "snapshot_id": meta["snapshot_id"], "commit_sha": meta["commit_sha"], "split": "validation",
            "kind": "followup" if i == 2 else "historical", "input": {"question": q},
            "question_sha256": fingerprint(q), "depends_on": "history-dev-01" if i == 2 else None})
    frozen(OUT / "development.json", development)
    protocol = {"version": "p63-v1", "baseline_sdk": sdk_version(), "max_local_requests": 600,
        "paid_requests_allowed": 0, "context_window": 8192, "final_output_tokens": 1536,
        "research_output_tokens": 1024, "max_decisions": 8, "max_tools": 6,
        "confirmation_policy": "Same-repository new questions, never used for policy selection; not blind or heldout benchmark.",
        "review": "owner-selected Codex nonblind model review", "human_approved": False,
        "training_eligible": False, "automatic_retry": False,
        "tasks_sha256": {n: sha(OUT / n) for n in ("confirmation.json", "development.json", "evaluation-only/confirmation.json")},
        "protected_files": {p.relative_to(ROOT).as_posix(): sha(p) for p in (ROOT / "experiments").rglob("*")
                            if p.is_file() and OUT not in p.parents}}
    frozen(OUT / "protocol.json", protocol)
    print(json.dumps({"development": len(development), "confirmation": len(tasks),
                      "protected_files": len(protocol["protected_files"])}))
    return protocol


if __name__ == "__main__":
    prepare()
