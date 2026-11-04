"""Codex reviews of the retained raw report stages, not a judge-model call."""

from p63_dataset import OUT
from record_p63_reviews import assessment as claim
from record_p63_reviews import ref, verdict
from record_p63_v2_reviews import missing
from run_rag_quality import frozen


def main():
    rows = {
        "developit--mitt/explorer": verdict("correct", "supported", "adequate",
            "Explorer 核心任务的用途、技术栈、入口与类型职责正确，13 文件与快照一致；明确未读取实现后半段/测试。不能把它当完整三阶段报告。",
            claim("轻量无依赖事件库，TypeScript 与 microbundle 构建", "supported", "README 自述和 package 声明支持，未声称已执行构建。", ref("README.md", 9, 19), ref("package.json", 9, 27)),
            claim("源码入口 src/index.ts，定义 Emitter 与导出 mitt", "supported", "包 source 字段、接口与默认函数声明支持。", ref("package.json", 9, 10), ref("src/index.ts", 23, 48))),
        "developit--mitt/mentor": missing("Mentor 输出截断；Contributor 未执行，整份报告失败，未重跑。"),
        "ai--nanoid/explorer": verdict("partially_correct", "partial", "partial",
            "用途/随机来源基本正确，但将 demo 的 Vite 当库构建、推断 vite build，并把浏览器、非安全与 CLI 入口全部填为 index.js。动态路径 schema 只验证路径合法，无法防止角色错误。",
            claim("库用途是安全 URL 友好 ID，使用 crypto", "supported", "包描述与 fillRandom 支持其设计用途，不等于实际线上无碰撞保证。", ref("package.json", 1, 4), ref("index.js", 5, 25)),
            claim("Vite 为库构建框架，存在 vite build", "contradicted", "start 仅针对 test/demo；脚本无 vite build。", ref("package.json", 43, 52)),
            claim("浏览器/非安全/CLI 入口都为 index.js", "contradicted", "条件导出分别指向 index.browser.js、non-secure/index.js，bin 为 bin/nanoid.js。", ref("package.json", 20, 40)), entry="mixed"),
        "ai--nanoid/mentor": missing("Mentor 输出截断；Contributor 未执行，整份报告失败，原始阶段输出保留。"),
    }
    frozen(OUT / "runs/reports-v1/review-decisions.json", rows)


if __name__ == "__main__":
    main()
