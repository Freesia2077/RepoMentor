"""Render an offline delivery report from verified results and authored semantic reviews."""

from p63 import load
from p63_dataset import OUT
from verify_p63_delivery import verify


def main():
    audit = verify()
    confirmed = load(OUT / "runs/confirmation-v1/comparison.json")
    lines = ["# P6.3：检索与有界 SDK Agent 的收益核验", "",
        "业务是固定仓库快照上的学习问答、持续追问与阅读报告。仅修改 Python 主线；原 TS、历史结果与验证/训练边界保留。", "",
        "## 核心结果", "",
        "24 题同仓库新题确认中，固定 RAG 与 SDK 均解决 **8/24**。SDK 平均 **2.08** 次请求，固定 RAG 为 **1**。跨函数题新增解决 **3** 题、退步 **3** 题、净增 **0**；不能宣称满足净增两题目标，也不能宣称 Agent 整体优于固定 RAG。", "",
        "这是 Codex 逐条核对源码的非盲模型评审，不是官方榜单、陌生仓库泛化实验或人类签核。解决须结论正确、必要内容完整且引用支持；来源未找到后的拒答不算解决。", "",
        f"全部批次本地请求预留 **{audit['reserved_local_requests']}/600**，付费与 DeepSeek **0**；已保存唯一请求 **{audit['unique_recorded_attempt_requests']}**。预留与已记录差额 **{audit['reservation_minus_recorded_requests']}**（若有差额，保守保留，不退款或自动重试）。", "",
        "## 实现与验证", "",
        "- 搜索保留命中行、同一源码单元去重、续读返回未覆盖区间；[长函数零调用演示](tool-demo.md)。",
        "- 已索引 Python/JS/TS 的持久化符号与有限静态关系；统一符号导航工具，一跳最多六个目标，动态关系保持未解析。",
        "- 固定 RAG 与 SDK 共享初始检索；SDK Runner 管理循环，call_model_input_filter 在调用前整理完整消息对与可见源码，传输层检查实际 8K 预算。",
        "- 曾读来源与当前可见来源分开，压缩后可重新获取；运行记录含 hooks、请求账本、上下文取舍、停止原因及确定性质量诊断。",
        "- 问答/报告提示词分离；最终上限 1536，研究上限 1024；研究直接输出合法 JSON 时仍受研究额度约束。报告静态边与命令按已见声明约束，原始输出保留。",
        "- 完整 Python 离线回归 208 passed、1 deselected；344 份受保护历史文件及冻结题目哈希核验。", "",
        "## 四层评测", "",
        "### 检索（72 题，同一切块集合）", "",
        "| 条件 | MRR | 完整参考段 Recall@6 | 目标重合 Recall@6 | 参考行覆盖@6 |",
        "| --- | --- | --- | --- | --- |"]
    for mode, r in audit["retrieval"].items():
        lines.append(f"| {mode} | {r['mrr']:.4f} | {r['recall_at_6']:.4f} | {r['target_overlap_recall_at_6']:.4f} | {r['line_coverage_at_6']:.4f} |")
    lines += ["", "四项检索指标衡量定位，不是答案正确率。首个命中定义为与参考区间重合；完整覆盖另算。参考区间可能遗漏有效替代来源。", "",
        "### 工具、上下文、答案与效率", "",
        "[确认逐题报告](runs/confirmation-v1/comparison.md)列出候选、曾读、最终参考覆盖与请求；[完整汇总](runs/confirmation-v1/comparison.json)另外保存初始覆盖、工具错误、重复调用和压缩次数，每次请求 token 见原始 case JSON。格式、引用范围、正文完整性疑点与语义判断分开。", "",
        "| 条件 | 工作流成功 | 语义解决 | 请求 | 输入 token | 输出 token |",
        "| --- | --- | --- | --- | --- | --- |"]
    for mode, r in confirmed["modes"].items():
        lines.append(f"| {mode} | {r['statuses'].get('success', 0)}/24 | {r['solved']}/24 | {r['model_requests']} | {r['prompt_tokens']} | {r['completion_tokens']} |")
    lines += ["", "耗时口径：固定记录模型完成时间，SDK 记录初始检索后的运行时间；不是端到端墙钟对照。原始 token 来自服务用量，预算检查是发送前 tokenizer 估算加输出预留。", "",
        "### 空缓存与热查询", "",
        "72 题复用已有索引/权重，不能叫全冷启动。下表另用 mitt 的独立空索引/向量/查询缓存；仍复用已安装权重，不控制 OS 磁盘缓存，不代表六仓库平均。", "",
        "| 条件 | 建索引秒 | 首个查询秒 | 热查询秒 |", "| --- | --- | --- | --- |"]
    for path in sorted((OUT / "retrieval/cache-probe").glob("*.json")):
        r = load(path)
        lines.append(f"| {r['retriever']} | {r['index_build_seconds']:.3f} | {r['cold_first_query_seconds']:.3f} | {r['warm_query_seconds']:.5f} |")
    lines += ["", "## 消融与失败归因", "",
        "开发 12 场景包括一个关联追问，不当作 12 个独立样本。BM25、混合、混合重排的 SDK 解决分别为 0/12、0/12、2/12；固定 RAG 各为 1/12。确认前据此选择混合重排、关闭专家，[选择记录](selection.json)保留评审哈希。确认结果不再用于改提示词或配置。", "",
        "专家开放消融 4 题共 8 次请求，实际委派 0 次。它检验工具可用条件的行为，不能证明专家协作效果。正确上下文 6 题解决 3/6，仍有继承关系虚构与要点遗漏，说明剩余问题并非全由检索造成。", "",
        "| 具体表现 | 归因与边界 |", "| --- | --- |",
        "| SDK 补查 Scheduler.cancel_job 与 Flask _prepare_response_obj | 关键分支补入上下文后解决；跨函数工具有局部价值 |",
        "| mitt 重复注册与 schedule 切片赋值仍答错 | 已见实现后误读，属于模型理解问题 |",
        "| Node nanoid 引用了压缩浏览器构建或混合导入测试 | 文件级入口筛选仍可能保留共享导入测试；不宣称达到符号级测试归属证明 |",
        "| merge_setting 答案正确但引用缺实际 del 行 | 引用范围合法不代表支持充分，分开计分 |",
        "| JSON 合法但正文在短语中结束 | 格式成功不代表解释完整；原始未完成正文保留 |", "",
        "## 连续对话、报告与恢复", "",
        "| 批次 | 计划文件 | 实际生成记录 | 已评审 | 工作流状态 | 零调用回放记录 |",
        "| --- | --- | --- | --- | --- | --- |"]
    for name in ("confirmation-v1", "dialogues-v1", "reports-v1"):
        r = audit["batches"].get(name, {})
        lines.append(f"| {name} | {r.get('case_files', 0)} | {r.get('records', 0)} | {r.get('reviewed', 0)} | {r.get('statuses', {})} | {r.get('replay_cases', 0)} |")
    lines += ["", "四组连续对话有三组完成四轮；nanoid 首轮截断，后三轮阻塞。实际生成 13 条，12 条工作流成功，按全部计划轮次计语义解决 7/16。两份报告都在 Mentor 阶段截断，Contributor 未执行；实际尝试 4/6 阶段，仅 mitt Explorer 语义通过，完整三阶段报告为 0/2。", "",
        "对话逐轮使用实际前文；失败后被阻塞的轮次没有伪造回答，也没有用固定首轮替代。报告失败保留已完成阶段，不反复生成直到成功。模型边界恢复和缓存回放已验证；网络发送中途强杀不保证远端恰好执行一次。", "",
        "实验后离线检查修复了报告发布约束的 gaps 字段结构和容量上限，原始内容保存在调整记录中；没有新增生成，也没有改写实验结果。此修复改变 SDK 实现身份，已有零调用回放收据对应修复前版本；当前版本拒绝跨版本复用，仍可通过 verify 离线核验历史结果。详见[修复身份记录](post-experiment-fix.json)。", "",
        "## 验收与后续", "",
        f"结果收集与评审待完成项：{audit['pending'] or '无'}。预算与源码校验：通过。**全部验收目标通过：{audit['all_acceptance_targets_met']}**。实现与实验收尾不代表质量验收通过：跨函数净增目标及连续对话、完整报告目标仍未满足。详见[验收 JSON](acceptance.json)。", "",
        "[终端与离线演示指南](../../notes/p63-demo.md)包含导入、问答、追问、跨函数补查和引用查看。确认集的题类并不包括独立测试验证类；测试验证在开发集与连续对话中覆盖。四种检索候选共享源码块，不把旧行块实验直接并入新口径。", "",
        "本轮验证集和外部开发集全部禁止进入 SFT/DPO。P7 尚未启动；优先据正确上下文失败研究否定/条件理解、入口识别、完整作答与引用对齐。MiniMind 与 Qwen 后续共用本项目的任务、工具动作、观察与结果协议，不能把模型规模差异归因为后训练收益。", ""]
    (OUT / "report.md").write_text("\n".join(lines), encoding="utf-8")
    print(str(OUT / "report.md"))


if __name__ == "__main__":
    main()
