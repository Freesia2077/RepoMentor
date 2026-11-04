"""Compact, pinned RAG expansion: one dataset, one result stream, one reviewed summary."""

import argparse
import asyncio
import hashlib
import json
import random
import re
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from repomentor.config import load_chat_settings, write_json
from repomentor.evaluation import ANSWER_SCHEMA
from repomentor.experiment_ledger import RequestLedger
from repomentor.interview_baselines import prepare_input, select_sources
from repomentor.paths import data_root, runtime_dir
from repomentor.rag import SYSTEM, citation_validity, messages_for, rag_version, verify_spans
from repomentor.rag_evaluation import coverage
from repomentor.repository import SnapshotStore, is_source
from repomentor.retrieval_factory import build_index
from repomentor.runtimes.chat import ChatCompletionsRuntime, ChatSettings, fingerprint
from repomentor.semantic_review import quality_tiers, validate_judgment
from repomentor.source_windows import source_span
from repomentor.token_budget import input_budget

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "experiments/rag-expansion"
PRIVATE = runtime_dir() / "rag-expansion"
CONDITIONS = ("lexical", "code_rag")
CAP = 27


def specs():
    # Four retrieval-development questions then two precommitted generation questions per repo.
    return {
        "theskumar/python-dotenv": [
            ("_load_dotenv_disabled 如何处理 PYTHON_DOTENV_DISABLED='YeS' 与 '0'？", ["前者True后者False；casefold后匹配固定真值集合"], [("src/dotenv/main.py", 22, 29)]),
            ("DotEnv._get_stream 同时拿到存在的 dotenv_path 和 stream 时优先哪个？没有二者时返回什么？", ["有效文件优先，其次stream，否则空StringIO"], [("src/dotenv/main.py", 64, 77)]),
            ("dotenv_values 返回字典前使用哪个 override 值？它是否调用 set_as_environment_variables？", ["构造DotEnv传override=True；调用dict而非写环境方法"], [("src/dotenv/main.py", 507, 517)]),
            ("仅凭 python-dotenv 源码，能知道我公司生产进程现在 DATABASE_URL 的值吗？", ["不能，需要实际进程配置；不能从库源码推出私有环境值"], []),
            ("DotEnv.set_as_environment_variables 在 override=False 且环境已有同名键时会怎样？解析值为 None 与空字符串时是否都跳过？", ["已有键且不覆盖时continue", "None跳过，空字符串仍写入；非空字典处理后返回True"], [("src/dotenv/main.py", 101, 114)]),
            ("resolve_variables 中，override=True 和 False 时 os.environ 与先前解析的 new_values 哪个优先？请按 env.update 顺序解释。", ["True时new_values后更新并覆盖环境", "False时os.environ后更新并优先；不是修改os.environ"], [("src/dotenv/main.py", 333, 355)]),
        ],
        "tkem/cachetools": [
            ("Cache.__setitem__ 的 getsizeof 返回负数或超过 maxsize 时分别怎样处理？", ["负数和超限分别抛ValueError，发生在写数据前"], [("src/cachetools/__init__.py", 77, 96)]),
            ("Cache.pop 缺少键时，不传 default 与传 default=None 有什么区别？", ["未给default用私有哨兵并抛KeyError；显式None返回None"], [("src/cachetools/__init__.py", 127, 135)]),
            ("cachetools.keys.methodkey 会把 self 作为缓存键的一部分吗？它如何委派 hashkey？", ["不含self，只转发args和kwargs"], [("src/cachetools/keys.py", 37, 48)]),
            ("仅凭 cachetools 仓库能确定我线上服务昨天的缓存命中率吗？", ["不能，需要实际运行计数或遥测"], []),
            ("LRUCache 对已有键执行 __getitem__ 与仅执行 key in cache，都会更新最近使用顺序吗？结合 __touch 和继承的 __contains__ 说明。", ["getitem成功且键存在时touch", "contains只查底层字典不touch；touch将键移到OrderedDict末尾"], [("src/cachetools/__init__.py", 103, 104), ("src/cachetools/__init__.py", 294, 298), ("src/cachetools/__init__.py", 321, 326)]),
            ("TTLCache.__contains__ 在 timer() 恰好等于 link.expires 时返回什么？这个成员检查会删除过期项或调整LRU顺序吗？", ["严格小于才有效，相等返回False", "只查link和时间，不删除、不重排"], [("src/cachetools/__init__.py", 476, 482)]),
        ],
        "msiemens/tinydb": [
            ("Table.insert 接收非 Mapping 时怎样处理？Document 自带的 doc_id 是否被忽略？", ["非Mapping抛ValueError；Document复用doc_id并重置_next_id"], [("tinydb/table.py", 147, 177)]),
            ("Table.truncate 除了清空表，还会怎样处理文档ID计数？", ["更新表clear，并把_next_id置None"], [("tinydb/table.py", 698, 707)]),
            ("JSONStorage.read 遇到空文件会返回空字典还是 None？它如何检查文件大小？", ["seek末尾tell，大小零返回None"], [("tinydb/storages.py", 125, 140)]),
            ("仅凭 TinyDB 源码能知道我本机未提供的数据库中有多少客户文档吗？", ["不能，缺少实际数据库内容"], []),
            ("Table.search 命中 _query_cache 后返回的是原列表吗？修改返回列表的长度与修改其中Document的字段，对后续相同查询有什么区别？", ["cached_results[:]新浅拷贝列表", "列表增删不改缓存列表，Document对象共享，字段修改会影响后续缓存结果"], [("tinydb/table.py", 249, 267), ("tinydb/table.py", 291, 297)]),
            ("CachingMiddleware.write 会每次直接写底层存储吗？修改次数达到 WRITE_CACHE_SIZE 及调用 close 时分别怎样刷新，重复 close 又如何？", ["先更新缓存并计数，达到阈值flush", "flush在脏计数大于零时写底层并清零；close先flush再关闭，已关闭直接返回"], [("tinydb/middlewares.py", 108, 142)]),
        ],
        "debug-js/debug": [
            ("debug 的 src/index.js 在 Electron renderer 中选择 browser.js 还是 node.js？条件如何判断？", ["process.type==='renderer'走browser；其他browser相关条件也走browser，否则node"], [("src/index.js", 1, 10)]),
            ("createDebug 创建的 debug 函数在 enabled 为 false 时，会更新时间差并输出日志吗？", ["开头直接return，早于时间更新和日志调用"], [("src/common.js", 66, 80)]),
            ("coerce 遇到 Error 实例如何处理？没有 stack 时使用什么？", ["返回stack或message，其他值原样返回"], [("src/common.js", 272, 277)]),
            ("debug 源码能告诉我生产系统今天实际输出了多少条日志吗？", ["不能，需要生产日志或遥测"], []),
            ("enable('app:*, -app:db') 后 enabled('app:db') 和 enabled('app:web') 各返回什么？请解释 skips 与 names 的检查优先级。", ["db为False，web为True", "先检查skips并提前返回False，才检查names"], [("src/common.js", 162, 182), ("src/common.js", 249, 263)]),
            ("debug.extend('child') 默认怎样拼接namespace？显式 delimiter='' 时怎样拼接？新实例的 log 来自哪里？", ["默认用冒号，空delimiter不回退冒号", "新实例log赋为父实例this.log"], [("src/common.js", 149, 153)]),
        ],
        "chalk/chalk": [
            ("assertValidLevel 接受 1.5、-1、4 吗？允许范围与整数条件是什么？", ["均拒绝，必须safe integer且0到3闭区间"], [("source/index.js", 17, 21)]),
            ("Chalk 的 applyOptions 在 options.level 显式为0时会改用自动检测的 colorLevel 吗？", ["不会，仅undefined用检测值，显式0保留"], [("source/index.js", 35, 43)]),
            ("createBuilder 对单个参数 null 与两个参数 null、undefined 的字符串转换相同吗？", ["单参数隐式拼接得到'null'；双参数nullish变空后由一个空格连接"], [("source/index.js", 172, 185)]),
            ("仅凭 Chalk 源码能确定用户当前远程终端的实际颜色显示效果吗？", ["不能，需要实际终端能力与环境；代码只提供检测和样式机制"], []),
            ("stringReplaceAll('x--x', 'x', '!') 返回什么？这里是删除原匹配后替换，还是保留原匹配再追加 postfix？无匹配时怎样返回？", ["返回'x!--x!'", "保留每个匹配并追加postfix，无匹配原样返回"], [("source/utilities.js", 1, 19)]),
            ("applyStyle 在颜色level<=0时，IS_EMPTY为false与true分别返回什么？颜色启用但styler为undefined时又怎样处理？", ["禁色时false返回原字符串，true返回空串", "颜色启用但无styler时返回原字符串"], [("source/index.js", 200, 211)]),
        ],
        "tj/commander.js": [
            ("Option('--no-cache') 的 negate 如何确定？构造时 mandatory 默认是什么？", ["long以--no-开头则negate=True；mandatory=False"], [("lib/option.js", 11, 36)]),
            ("Option.conflicts 连续传字符串和数组时会覆盖原冲突列表，还是追加？", ["concat追加，并返回this"], [("lib/option.js", 82, 85)]),
            ("Option.implies('log') 如何解释字符串参数？连续调用如何合并已有 implied？", ["字符串转{log:true}，Object.assign合并到已有对象"], [("lib/option.js", 100, 108)]),
            ("仅凭 Commander.js 源码能知道我公司某个进程实际使用的命令行参数吗？", ["不能，需要实际argv或启动配置"], []),
            ("new Argument('[files...]') 创建的 required、variadic 和 name() 分别是什么？请说明括号与省略号剥离顺序。", ["required=False，variadic=True，name为files", "先剥方括号再剥尾部三点"], [("lib/argument.js", 13, 40), ("lib/argument.js", 48, 50)]),
            ("Option.choices(values) 会保存调用者数组本身吗？后续传入非法值如何处理？variadic选项的合法值如何收集？", ["values.slice复制，非法值抛InvalidArgumentError", "variadic委派_collectValue：初次或previous为defaultValue时新建数组，否则push"], [("lib/option.js", 165, 172), ("lib/option.js", 181, 195)]),
        ],
    }


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def settings():
    return load_chat_settings(ROOT / "config/model-settings.json")


def implementation():
    return {"rag": rag_version(), "runner": hashlib.sha256(Path(__file__).read_bytes().replace(b"\r\n", b"\n")).hexdigest(),
            "baseline": hashlib.sha256((ROOT / "src/repomentor/interview_baselines.py").read_bytes().replace(b"\r\n", b"\n")).hexdigest()}


def reader_for(task):
    rd = SnapshotStore(data_root() / "corpus").open(task["snapshot_id"])
    if rd.snapshot.commit_sha != task["commit_sha"]:
        raise ValueError("snapshot changed")
    return rd


def prior_count():
    total = 0
    for relative in ("p63/dispatches.sqlite", "rag-interview/dispatches.sqlite"):
        with sqlite3.connect((runtime_dir() / relative).resolve().as_uri() + "?mode=ro", uri=True) as db:
            total += db.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0]
    return total


def reserved_count():
    path = PRIVATE / "dispatches.sqlite"
    if not path.exists():
        return 0
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return db.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0]


def clone_check(repositories):
    # Only fingerprint source, including sealed test source; never access its tasks or answers.
    old = load(ROOT / "experiments/p0/corpus.json")["repositories"]
    old += load(ROOT / "experiments/p6-d-external/admission.json")["repositories"]
    new_names = {r["repository"].casefold() for r in repositories}
    if new_names & {r["repository"].casefold() for r in old}:
        raise ValueError("repository split overlap")
    docs, inverted, matches, counts = [], defaultdict(list), [], Counter()
    for repo in [*repositories, *old]:
        new = repo["repository"].casefold() in new_names
        rd = reader_for(repo)
        for path in rd.paths:
            if not is_source(path) or re.search(r"(^|/)(vendor|fixtures|node_modules|dist|build)/", path):
                continue
            text, truncated = rd.read(path, 500_000)
            if truncated:
                raise ValueError("clone scan source exceeds reader bound")
            tokens = re.findall(r"[A-Za-z_][\w]*|\d+|[^\w\s]", text)
            if len(tokens) < 80:
                continue
            counts["new" if new else "existing"] += 1
            shingles = {hashlib.blake2b(" ".join(tokens[i:i + 20]).encode(), digest_size=8).digest()
                        for i in range(len(tokens) - 19)}
            if new:
                docid = len(docs)
                docs.append((repo["repository"], path, len(shingles)))
                for h in shingles:
                    inverted[h].append(docid)
            else:
                overlap = Counter(i for h in shingles for i in inverted.get(h, ()))
                for i, n in overlap.items():
                    if n / min(len(shingles), docs[i][2]) >= .8:
                        matches.append({"new": docs[i][:2], "existing": [repo["repository"], path]})
    return {"method": "20-token shingles; >=80 tokens; containment>=0.8; new vs existing repositories",
            "scanned_files": dict(counts), "matches": matches,
            "policy": "Exclude matched new-side files from the index view and references; preserve original snapshots.",
            "limitations": "No semantic clone or pretraining-contamination guarantee."}


def prepare():
    if (OUT / "dataset.json").exists():
        raise ValueError("dataset already frozen; use verify or a new identity")
    model = settings()
    if (model.context_window, model.max_tokens) != (16384, 2048) or prior_count() != 571:
        raise ValueError("unexpected model or historical budget")
    repos = load(PRIVATE / "admission.json")
    dedup = clone_check(repos)
    tasks, references, inputs, retrieval = [], {}, {}, []
    for repo in repos:
        rd = reader_for(repo)
        repo["excluded_paths"] = sorted({m["new"][1] for m in dedup["matches"] if m["new"][0] == repo["repository"]})
        if repo["excluded_paths"]:
            manifest = [m for m in rd.snapshot.manifest if m.path not in repo["excluded_paths"]]
            rd = SimpleNamespace(paths=[p for p in rd.paths if p not in repo["excluded_paths"]], read=rd.read,
                                 snapshot=rd.snapshot.model_copy(update={"manifest": manifest}))
        index = build_index(rd, storage=PRIVATE, chunker="syntax", retriever="bm25", device="cpu")
        short = repo["repository"].split("/")[-1]
        for n, (question, points, refs) in enumerate(specs()[repo["repository"]], 1):
            tid = f"{short}-{n:02}"
            task = {"task_id": tid, **{k: repo[k] for k in ("repository", "snapshot_id", "commit_sha")},
                    "split": "confirmation" if n >= 5 else "retrieval-development", "input": {"question": question}}
            tasks.append(task)
            sources = []
            for path, a, b in refs:
                if path in repo["excluded_paths"]:
                    raise ValueError("reference uses excluded clone source")
                text, truncated = rd.read(path, 500_000)
                if truncated or b > len(text.splitlines()):
                    raise ValueError("invalid reference range")
                sources.append(source_span(path, text.splitlines(), a, b))
            references[tid] = {"required_points": points, "sources": sources, "answerable": bool(refs)}
            for condition in CONDITIONS:
                selection = select_sources(rd, index, question, condition)
                prepared = prepare_input(model, question, selection, condition)
                if not prepared["feasible"]:
                    raise ValueError("bounded RAG input is infeasible")
                inputs[tid + "--" + condition] = {"context": prepared["context"], "messages": prepared["input_messages"],
                    "budget": input_budget(model, prepared["input_messages"])}
                retrieval.append({"task_id": tid, "condition": condition, "split": task["split"],
                                  "reference_coverage": coverage(sources, prepared["context"]) if sources else None})
    old = load(ROOT / "experiments/rag-interview/confirmation/cases/interview-confirm-02--code_rag.json")
    old_task = old["task"]
    rd = reader_for(old_task)
    index = build_index(rd, storage=PRIVATE, chunker="syntax", retriever="bm25", device="cpu")
    scoped = prepare_input(model, old_task["input"]["question"], select_sources(rd, index, old_task["input"]["question"], "code_rag"), "code_rag")
    concise = messages_for(old_task["input"]["question"], [], scoped["context"],
                           instruction=SYSTEM + "仅输出最终结论与必要依据，不输出自问自答或反复推演；最多三个要点。")
    diagnostics = [
        {"case_id": "diagnostic/output4096", "settings": model.model_copy(update={"max_tokens": 4096, "timeout_seconds": 300}).public(),
         "context": old["result"]["context"], "messages": old["result"]["input_messages"], "change": "output allowance only; exact original messages"},
        {"case_id": "diagnostic/scoped2048", "settings": model.public(), "context": scoped["context"],
         "messages": scoped["input_messages"], "change": "negated entry selection fix; standard prompt and output"},
        {"case_id": "diagnostic/scoped-concise2048", "settings": model.public(), "context": scoped["context"],
         "messages": concise, "change": "scope fix plus concise instruction; output unchanged"},
    ]
    protocol = {"version": "rag-expansion-v1", "date": "2026-10-20", "settings": model.public(),
        "implementation": implementation(), "max_new_requests": CAP, "prior_reserved": 571, "combined_limit": 600,
        "plan": "3 precommitted development diagnostics; 12 confirmation questions x 2 fixed single-call conditions; no retries",
        "confirmation_policy": "Two per new repository, frozen before any generation; source-selected by implementer, not an independent benchmark",
        "training_eligible": False, "human_approved": False, "default_prompt_changed": False}
    dataset = {"protocol": protocol, "repositories": repos, "clone_check": dedup, "tasks": tasks,
               "evaluation_only": references, "prepared_inputs": inputs, "retrieval": retrieval,
               "diagnostic_task": old_task, "diagnostics": diagnostics}
    write_json(OUT / "dataset.json", dataset)
    print(json.dumps({"repositories": len(repos), "new_tasks": len(tasks), "confirmation_tasks": 12, "max_requests": CAP}), flush=True)


def results():
    path = OUT / "results.jsonl"
    return [json.loads(s) for s in path.read_text(encoding="utf-8").splitlines() if s.strip()] if path.exists() else []


def plan(data):
    rows = [{**row, "task": data["diagnostic_task"], "condition": row["case_id"]} for row in data["diagnostics"]]
    tasks = [t for t in data["tasks"] if t["split"] == "confirmation"]
    random.Random(20261020).shuffle(tasks)
    for i, task in enumerate(tasks):
        conditions = list(CONDITIONS)
        random.Random(42 + i).shuffle(conditions)
        for c in conditions:
            cid = task["task_id"] + "--" + c
            rows.append({"case_id": cid, "task": task, "condition": c, "settings": data["protocol"]["settings"],
                         **data["prepared_inputs"][cid]})
    return rows


def check():
    data = load(OUT / "dataset.json")
    if data["protocol"]["implementation"] != implementation() or data["protocol"]["settings"] != settings().public():
        raise ValueError("frozen implementation or configuration changed")
    return data


async def run():
    data = check()
    ledger = RequestLedger(PRIVATE / "dispatches.sqlite", limit=CAP, experiment=fingerprint(data), context_windows=(16384,))
    done = {r["case_id"]: r for r in results()}
    if len(done) != len(results()):
        raise ValueError("duplicate results")
    metadata = await ChatCompletionsRuntime(settings()).verify_backend()
    for row in plan(data):
        cid = row["case_id"]
        if cid in done:
            continue
        if any(r["case"] == cid for r in ledger.rows()):
            raise ValueError("ambiguous dispatch reserved; no automatic retry")
        model = ChatSettings.model_validate(row["settings"])
        def before(body, budget):
            if prior_count() + len(ledger.rows()) >= 600:
                raise ValueError("combined cap reached")
            ledger.reserve(model, cid, body, budget)
        runtime = ChatCompletionsRuntime(model, before_dispatch=before)
        started = time.perf_counter()
        comp = await runtime.complete(row["messages"], response_schema=ANSWER_SCHEMA)
        answer = json.loads(comp.content) if comp.status == "success" else None
        record = {"case_id": cid, "task_id": row["task"]["task_id"], "condition": row["condition"],
                  "input_sha256": fingerprint(row["messages"]), "settings": model.public(), "backend": metadata,
                  "completion": comp.model_dump(), "answer": answer,
                  "citation_ranges_valid": citation_validity(answer, row["context"]),
                  "elapsed_seconds": time.perf_counter() - started, "training_eligible": False}
        with (OUT / "results.jsonl").open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        print(json.dumps({"case": cid, "status": comp.status, "output_tokens": comp.usage["completion_tokens"],
                          "combined_reserved": prior_count() + len(ledger.rows())}), flush=True)


def historical_tiers():
    old = ROOT / "experiments/rag-interview"
    mapping = load(old / "review/unblinding.json")["mapping"]
    groups = defaultdict(Counter)
    for cid, judgment in load(old / "judgments.json").items():
        case = load(ROOT / mapping[cid]["source"])
        phase = Path(mapping[cid]["source"]).parts[-3]
        g = groups[phase + "/" + case["condition"]]
        g["tasks"] += 1
        g.update({k: int(v) for k, v in quality_tiers(judgment, case["result"]["completion"]["status"]).items()})
    return {k: dict(v) for k, v in groups.items()}


def verify():
    data = check()
    planned = {r["case_id"]: r for r in plan(data)}
    records = results()
    if len({r["case_id"] for r in records}) != len(records):
        raise ValueError("duplicate result IDs")
    for row in records:
        source = planned[row["case_id"]]
        if row["input_sha256"] != fingerprint(source["messages"]):
            raise ValueError("input changed")
        comp = row["completion"]
        runtime = ChatCompletionsRuntime(ChatSettings.model_validate(source["settings"]))
        if comp["request_hash"] != fingerprint(runtime.request_body(source["messages"], response_schema=ANSWER_SCHEMA)):
            raise ValueError("request hash changed")
        verify_spans(reader_for(source["task"]), source["context"])
        if set(json.loads(source["messages"][-1]["content"])) != {"question", "context"}:
            raise ValueError("evaluation-only data in input")
    if prior_count() + reserved_count() > 600:
        raise ValueError("combined request budget exceeded")
    print(json.dumps({"verified_records": len(results()), "model_requests": 0}), flush=True)


def summary():
    data = check()
    prior = load(OUT / "summary.json") if (OUT / "summary.json").exists() else {}
    supplied = prior.get("reviews", {})
    planned = {r["case_id"]: r for r in plan(data)}
    reviews, groups = {}, defaultdict(Counter)
    for row in results():
        cid = row["case_id"]
        g = groups[row["condition"]]
        g["records"] += 1
        g[row["completion"]["status"]] += 1
        if cid in supplied:
            source = planned[cid]
            candidate = {"candidate_id": cid, "answer": row["answer"], "observed_context": source["context"]}
            value = {k: supplied[cid][k] for k in ("candidate_id", "correctness", "citation_support", "completeness", "abstention", "entry_scope", "consistency", "claims", "notes")}
            value["claims"] = [{k: c[k] for k in ("claim", "status", "rationale", "evidence")} for c in value["claims"]]
            reviews[cid] = validate_judgment(value, candidate, reader_for(source["task"]))
            reviews[cid]["result_sha256"] = fingerprint(row)
            g.update({k: int(v) for k, v in quality_tiers(reviews[cid], row["completion"]["status"]).items()})
    matrix = []
    for phase in ("development", "confirmation"):
        for cid, row in load(ROOT / "experiments/rag-interview" / phase / "inputs.json").items():
            if cid.endswith("--located_file") and not row["prepared"]["feasible"]:
                budget = input_budget(settings(), row["prepared"]["input_messages"])
                matrix.append({"case": cid, "path": row["selection"]["sources"][0]["path"], **budget,
                               "fits_32k_arithmetic_only": budget["total"] <= 32768})
    retrieval = {}
    for condition in CONDITIONS:
        rows = [r for r in data["retrieval"] if r["condition"] == condition and r["reference_coverage"] is not None]
        retrieval[condition] = {"answerable_tasks": len(rows), "complete_reference": sum(r["reference_coverage"] == 1 for r in rows)}
    new_count = reserved_count()
    write_json(OUT / "summary.json", {"reviews": reviews, "groups": {k: dict(v) for k, v in groups.items()},
        "historical_tiers_posthoc": historical_tiers(), "whole_file_budget_diagnostic": matrix,
        "retrieval_only_all_36": retrieval, "new_tasks": len(data["tasks"]), "new_repositories": len(data["repositories"]),
        "new_requests": new_count, "combined_requests": prior_count() + new_count, "paid_requests": 0,
        "checks": prior.get("checks", {}), "human_approved": False, "independent_review_completed": False,
        "training_eligible": False, "quality_policy": "core_supported tolerates omitted explanation, not false facts, unsupported citations, wrong entry or truncation; fully_resolved also requires adequate completeness"})
    print(json.dumps({"reviews": len(reviews), "groups": {k: dict(v) for k, v in groups.items()}}), flush=True)


def finalize():
    verify()
    summary()
    if len(results()) != CAP or len(load(OUT / "summary.json")["reviews"]) != CAP:
        raise ValueError("planned outputs and explicit reviews incomplete")
    hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir() if p.is_file() and p.name != "completed-files.json"}
    if (OUT / "completed-files.json").exists() and load(OUT / "completed-files.json")["sha256"] != hashes:
        raise ValueError("sealed output changed")
    write_json(OUT / "completed-files.json", {"sha256": hashes, "training_eligible": False})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run", "verify", "summary", "finalize"))
    action = parser.parse_args().action
    if action == "run":
        asyncio.run(run())
    elif action == "verify":
        import httpx
        import httpx2
        def forbidden(*args, **kwargs):
            raise AssertionError("offline verification attempted HTTP")
        with patch.object(httpx.Client, "send", forbidden), patch.object(httpx.AsyncClient, "send", forbidden), patch.object(httpx2.Client, "send", forbidden), patch.object(httpx2.AsyncClient, "send", forbidden):
            verify()
    else:
        {"prepare": prepare, "summary": summary, "finalize": finalize}[action]()


if __name__ == "__main__":
    main()
