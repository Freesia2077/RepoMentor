"""Offline reproduction of the long-function excerpt/continuation regression."""

from types import SimpleNamespace

from p63_dataset import OUT

from repomentor.agent_tools import RepositoryEnvironment
from repomentor.contracts import ToolCall
from repomentor.records import write


def main():
    source = "def outer():\n" + "".join(f'    value_{i} = "padding_for_bounded_source_window"\n' for i in range(80))
    source += "    return should_bypass_proxies()\n"
    reader = SimpleNamespace(snapshot=SimpleNamespace(snapshot_id="synthetic-long-function"),
        paths=["src/main.py"], read=lambda path, maximum: (source, False))
    env = RepositoryEnvironment(reader, None)
    chunk = {"path": "src/main.py", "start_line": 1, "end_line": 82, "content": source, "chunk_id": "long"}
    hit = env._excerpts([{"chunk": chunk, "score": 1}], "should_bypass_proxies", 18000)
    first = env.step(ToolCall(call_id="first", tool="read_code", arguments={
        "path": "src/main.py", "start_line": 1, "end_line": 82, "expand": "symbol"}))
    cursor = first["source_spans"][0]["end_line"] + 1
    second = env.step(ToolCall(call_id="next", tool="read_code", arguments={
        "path": "src/main.py", "start_line": cursor, "end_line": 82, "expand": "symbol"}))
    assert "should_bypass_proxies" in hit["spans"][0]["content"]
    assert second["source_spans"][0]["start_line"] == cursor
    write(OUT / "tool-demo.json", {"fixture": source, "generation_requests": 0,
        "before": {"kind": "simulation_of_recorded_prefix_truncation_defect", "returned_lines": [1, 22],
                   "hit_line": 82, "hit_returned": False},
        "after": {"actual_search": hit, "first_read": first, "continuation": second}})
    lines = ["# 长函数工具修复演示", "", "合成的 82 行函数，查询命中最后一行；全程零模型调用。", "",
        "修复前缺陷：只取函数前缀 1–22 行，漏掉第 82 行；续读按符号扩展后再次从头开始。这里以该前缀行为模拟旧缺陷，并非重新运行已归档的旧引擎。", "",
        f"当前实际查询返回 {hit['spans'][0]['start_line']}–{hit['spans'][0]['end_line']} 行，包含第 82 行。",
        f"当前首次读取截至 {cursor-1} 行；显式续读从 {cursor} 行开始，没有重复前缀。", "",
        "```python", hit["spans"][0]["content"], "```", "",
        "运行 `scripts/p63_tool_demo.py` 可重现；完整 observation、未覆盖区间及命中元数据见 `experiments/p63/tool-demo.json`。"]
    (OUT / "tool-demo.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("tool demo: matched line retained; continuation advanced; model calls 0")


if __name__ == "__main__":
    main()
