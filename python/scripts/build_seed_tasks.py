"""Source-reviewed seed tasks; exact SHA/line anchors are checked, human review remains explicit."""

import hashlib
import json
from pathlib import Path

from repomentor.paths import data_root
from repomentor.repository import SnapshotStore

ROOT = Path(__file__).resolve().parents[1]
# repository, path, start/end lines, question, required facts; no teacher-generated answers.
SPEC = {
    "developit/mitt": (
        "src/index.ts",
        [
            (46, 59, "mitt 没有传入 all 时，监听器保存在哪里？", "创建一个 Map，并通过返回对象的 all 暴露。"),
            (46, 58, "传入已有 Map 后，mitt 会复制它吗？", "不会；all = all || new Map() 复用传入的 Map。"),
            (66, 73, "同一种事件已有监听器时，on 如何添加新监听器？", "查找数组并 push，新监听器排在末尾。"),
            (
                66,
                73,
                "第一次注册某类事件时，on 会做什么？",
                "使用 all.set(type, [handler]) 创建该事件的数组。",
            ),
            (
                82,
                91,
                "off(type) 省略 handler 会删除什么？",
                "将该 type 的处理器列表置为空数组，Map 的键仍保留。",
            ),
            (
                82,
                91,
                "off 传入从未注册的 handler 为什么不会误删最后一个？",
                "indexOf 返回 -1；无符号右移转换成大整数，splice 不会删除末项。",
            ),
            (
                103,
                121,
                "emit 时普通事件处理器和通配符处理器哪个先执行？",
                "先执行 type 对应的处理器，之后读取并执行 '*' 列表。",
            ),
            (
                103,
                121,
                "emit 为什么在 map 调用前使用 slice？",
                "迭代当前数组的副本，避免迭代时增删同一列表改变当前轮的副本。",
            ),
            (113, 120, "通配符处理器会收到哪些参数？", "先传入事件 type，再传入 evt。"),
            (
                103,
                120,
                "没有注册普通事件监听器时，emit 会直接报错吗？",
                "handlers 为 falsy 时跳过普通监听器；后续仍可能调用通配符。",
            ),
            (
                103,
                121,
                "某监听器抛异常，emit 是否在内部捕获并继续？",
                "这里没有 try/catch；同步异常会中断本次调用，不能保证后续处理器执行。",
            ),
            (1, 10, "mitt 的事件类型是否只能是字符串？", "EventType 定义为 string | symbol。"),
            (
                103,
                121,
                "监听器返回 Promise 时，emit 会 await 它吗？",
                "不会；代码同步调用处理器，并没有 await 或 Promise 聚合。",
            ),
            (
                52,
                58,
                "为什么调用 emitter.all.clear() 可以清空所有监听器？",
                "返回对象暴露保存监听器的同一个 Map，clear 直接清空该存储。",
            ),
            (
                103,
                121,
                "只看此 emit 实现，能证明它会把事件持久化到数据库吗？",
                "不能；可见代码只调用内存 Map 中的处理器，没有数据库写入依据。",
            ),
        ],
    ),
    "ai/nanoid": (
        "index.js",
        [
            (96, 103, "Node 入口 customAlphabet 的默认 ID 长度是多少？", "defaultSize 默认为 21。"),
            (177, 177, "Node 入口导出的 nanoid 是如何创建的？", "由 customAlphabet(urlAlphabet) 生成。"),
            (
                133,
                138,
                "Node 入口 nanoid 的快速路径遇到负数或零长度如何处理？",
                "size 先按位转整数；负数抛 RangeError，零返回空字符串。",
            ),
            (
                5,
                15,
                "fillRandom 为什么分块调用 crypto.getRandomValues？",
                "单次请求限制 65536 字节；按 GET_RANDOM_LIMIT 切 subarray 分块填充。",
            ),
            (
                18,
                26,
                "random(bytes) 是否使用 ID 字符串池？",
                "不使用；创建 Buffer 后直接调用 fillRandom 并返回 buffer。",
            ),
            (
                29,
                40,
                "customRandom 为什么拒绝部分随机字节？",
                "通过 safeByteCutoff=256-(256%alphabet.length) 避免取模导致的分布偏差。",
            ),
            (
                42,
                59,
                "字母表长度为二的幂时 customRandom 用什么快速映射？",
                "safeByteCutoff 为 256 时，以 alphabet.length-1 为 mask，按位与映射字节。",
            ),
            (
                96,
                110,
                "字母表中包含字符码大于 255 的字符，会走字符串池快速路径吗？",
                "不会；回退到 customRandom(alphabet, defaultSize, random)。",
            ),
            (
                139,
                173,
                "customAlphabet 快速路径的字符串池如何被消费？",
                "不足时填充 buffer 并转 latin1 字符串；增加 poolOffset，用 substring 返回当前 ID。",
            ),
            (
                93,
                142,
                "字符串池下一次大小如何增长，增长上限在哪里定义？",
                "poolNext 按 target*16 增长并限制到 POOL_MAX；target 还至少覆盖当前 size。",
            ),
            (
                150,
                167,
                "非二次幂字母表如何避免越界映射？",
                "对随机字节按 mask 取索引，只有 index < alphabetLen 才接受并写入。",
            ),
            (
                9,
                15,
                "底层 crypto.getRandomValues 抛错时 fillRandom 会吞掉异常吗？",
                "不会；该函数没有捕获异常。",
            ),
            (
                46,
                59,
                "customRandom 在二次幂分支按何种次序读取随机数组？",
                "i 从 size 开始递减，按 bytes[size-1] 到 bytes[0] 读取。",
            ),
            (
                68,
                85,
                "拒绝采样的 step 为什么比目标长度大？",
                "按接收比例和经验系数 1.6 预取额外随机字节，以减少额外随机调用。",
            ),
            (
                133,
                173,
                "仅凭当前 nanoid 实现能保证生成的 ID 永不碰撞吗？",
                "不能；这是基于随机字节的生成流程，不存在全局唯一性存储或冲突检测证明。",
            ),
        ],
    ),
    "dbader/schedule": (
        "schedule/__init__.py",
        [
            (
                89,
                101,
                "run_pending 是否补跑所有错过的调度次数？",
                "不会；本次只对 should_run 的任务执行一次，文档明确不补跑错过的每一轮。",
            ),
            (
                99,
                101,
                "run_pending 怎样选择并排序待运行的任务？",
                "筛选 job.should_run，然后 sorted(runnable_jobs) 后逐个 _run_job。",
            ),
            (
                103,
                120,
                "run_all 与 run_pending 的任务选择有何不同？",
                "run_all 遍历 self.jobs[:]，不检查 should_run；每次执行后 sleep(delay_seconds)。",
            ),
            (
                122,
                133,
                "get_jobs() 不传 tag 返回内部列表本身吗？",
                "返回 self.jobs[:] 的浅拷贝；传 tag 则按 job.tags 过滤。",
            ),
            (
                135,
                148,
                "clear(tag) 如何保留不应删除的任务？",
                "使用切片赋值保留 tag 不在 job.tags 的任务，维持列表对象本身。",
            ),
            (
                150,
                160,
                "cancel_job 删除未注册的任务会向调用方抛 ValueError 吗？",
                "不会；捕获 remove 的 ValueError 并记录日志。",
            ),
            (
                162,
                170,
                "Scheduler.every 的默认 interval 是多少，立即注册任务了吗？",
                "默认 1；返回绑定 Scheduler 的 Job，every 本身没有 append 到 jobs。",
            ),
            (
                172,
                175,
                "任务返回 CancelJob 类或实例能否自动取消？",
                "两种都可：isinstance(ret, CancelJob) 或 ret is CancelJob 时调用 cancel_job。",
            ),
            (
                177,
                193,
                "get_next_run 在没有任务或过滤后无任务时返回什么？",
                "返回 None；有任务时返回 min(jobs_filtered).next_run。",
            ),
            (
                197,
                206,
                "idle_seconds 的值怎样计算？",
                "没有 next_run 返回 None，否则返回 next_run 减当前本地时间的 total_seconds。",
            ),
            (
                644,
                664,
                "Job.do 怎样保存函数及参数，并注册到调度器？",
                "functools.partial 绑定参数，update_wrapper，计算 next_run，再追加到 scheduler.jobs。",
            ),
            (
                655,
                664,
                "Job 未绑定 Scheduler 时调用 do 会怎样？",
                "检查 scheduler 为 None 后抛 ScheduleError，不会成功注册。",
            ),
            (
                666,
                672,
                "should_run 在什么条件下返回 True？",
                "先要求 next_run 已设定，然后判断 datetime.now() >= next_run。",
            ),
            (
                674,
                698,
                "Job.run 遇到执行前已经过期的任务是否仍调用 job_func？",
                "不会；_is_overdue(now) 成立立即返回 CancelJob。",
            ),
            (
                686,
                698,
                "job_func 抛异常后，Job.run 还会更新 last_run 和 next_run 吗？",
                "这段实现不捕获异常；调用抛错会中断，后续 last_run 赋值和重调度不会执行。",
            ),
        ],
    ),
    "more-itertools/more-itertools": (
        "more_itertools/recipes.py",
        [
            (
                113,
                126,
                "take(n, iterable) 返回迭代器还是列表？",
                "list(islice(...)) 返回列表，输入不足 n 时返回现有项。",
            ),
            (
                129,
                144,
                "tabulate 不传 start 时从哪里开始，如何迭代？",
                "从 0 开始，通过 map(function, count(start)) 逐个计算。",
            ),
            (147, 163, "tail 的 n 为负数时会怎样？", "抛 ValueError，要求 n 至少为 0。"),
            (
                158,
                163,
                "tail 遇到没有 len 的迭代器如何保存最后 n 项？",
                "捕获 TypeError，使用 deque(iterable,maxlen=n)，再返回其迭代器。",
            ),
            (166, 203, "consume 不传 n 时如何耗尽迭代器而不保留元素？", "使用 deque(iterator,maxlen=0)。"),
            (
                198,
                203,
                "consume 指定 n 时是怎样推进迭代器的？",
                "使用 next(islice(iterator,n,n),None)，推进到空切片起点。",
            ),
            (206, 216, "nth 访问不到第 n 项时返回什么？", "返回 default，未指定 default 时为 None。"),
            (
                219,
                242,
                "all_equal 对空迭代器返回什么？",
                "返回 True；groupby 没有产生分组时直接落到最后的 return True。",
            ),
            (
                245,
                252,
                "quantify 默认用什么谓词，如何计数？",
                "默认 bool，以 sum(map(pred, iterable)) 计数。",
            ),
            (
                255,
                269,
                "pad_none 在原迭代器耗尽之后做什么？",
                "通过 chain(iterable, repeat(None)) 无限产生 None；padnone 是别名。",
            ),
            (
                272,
                279,
                "ncycles 为什么能够重复一次性迭代器？",
                "先将输入物化为 tuple，再 repeat 同一 tuple n 次并展开。",
            ),
            (302, 311, "flatten 会递归展平任意层嵌套吗？", "不会；chain.from_iterable 只展开一层。"),
            (
                314,
                338,
                "repeatfunc 在 times=None 时会自动停止吗？",
                "不会由次数限制停止；starmap(function,repeat(args)) 使用无限 repeat。",
            ),
            (
                379,
                389,
                "grouper 的 fill、strict、ignore 分别如何处理尾部不足一组？",
                "fill 使用 zip_longest 补值；strict 使用 zip(strict=True) 抛错；ignore 普通 zip 丢弃不足部分。",
            ),
            (
                379,
                389,
                "grouper 的 incomplete 参数传入未知值会怎样？",
                "默认匹配分支抛 ValueError，要求 fill、strict 或 ignore。",
            ),
        ],
    ),
}


def main():
    manifest = json.loads((ROOT / "experiments/p0/corpus.json").read_text(encoding="utf-8"))
    repos = {r["repository"]: r for r in manifest["repositories"]}
    store = SnapshotStore(data_root() / "corpus")
    records = []
    for name, (path, specs) in SPEC.items():
        repo = repos[name]
        assert repo["split"] == "validation"
        reader = store.open(repo["snapshot_id"])
        text, truncated = reader.read(path, 500_000)
        assert not truncated
        lines = text.splitlines()
        for i, (start, end, question, answer) in enumerate(specs, 1):
            assert 1 <= start <= end <= len(lines)
            # Fix the evidence view and maintain a provenance hash, without putting answers into runtime inputs.
            snippet = "\n".join(lines[start - 1 : end])
            records.append(
                {
                    "task_id": name.replace("/", "--") + f"-{i:02d}",
                    "repository": name,
                    "snapshot_id": repo["snapshot_id"],
                    "commit_sha": repo["commit_sha"],
                    "split": "validation",
                    "task_type": "insufficient_evidence"
                    if "能保证" in question or "能证明" in question
                    else "code_explanation",
                    "input": {"question": question, "history": []},
                    "gold": {
                        "required_facts": [answer],
                        "evidence": [
                            {
                                "path": path,
                                "start_line": start,
                                "end_line": end,
                                "content_sha256": hashlib.sha256(snippet.encode()).hexdigest(),
                            }
                        ],
                    },
                    "review": {
                        "method": "Codex inspected pinned source and authored required facts; no target code executed",
                        "source_reviewed": True,
                        "human_approved": False,
                    },
                    "usage": "development seed; not an independent final benchmark",
                }
            )
    assert len(records) == 60 and len({r["task_id"] for r in records}) == 60
    output = ROOT / "experiments/p0/validation-seed.jsonl"
    output.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
    print(f"Wrote {len(records)} source-reviewed tasks; human approval remains explicitly unclaimed")


if __name__ == "__main__":
    main()
