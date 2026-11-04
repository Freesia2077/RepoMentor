# 长函数工具修复演示

合成的 82 行函数，查询命中最后一行；全程零模型调用。

修复前缺陷：只取函数前缀 1–22 行，漏掉第 82 行；续读按符号扩展后再次从头开始。这里以该前缀行为模拟旧缺陷，并非重新运行已归档的旧引擎。

当前实际查询返回 64–82 行，包含第 82 行。
当前首次读取截至 59 行；显式续读从 60 行开始，没有重复前缀。

```python
    value_62 = "padding_for_bounded_source_window"
    value_63 = "padding_for_bounded_source_window"
    value_64 = "padding_for_bounded_source_window"
    value_65 = "padding_for_bounded_source_window"
    value_66 = "padding_for_bounded_source_window"
    value_67 = "padding_for_bounded_source_window"
    value_68 = "padding_for_bounded_source_window"
    value_69 = "padding_for_bounded_source_window"
    value_70 = "padding_for_bounded_source_window"
    value_71 = "padding_for_bounded_source_window"
    value_72 = "padding_for_bounded_source_window"
    value_73 = "padding_for_bounded_source_window"
    value_74 = "padding_for_bounded_source_window"
    value_75 = "padding_for_bounded_source_window"
    value_76 = "padding_for_bounded_source_window"
    value_77 = "padding_for_bounded_source_window"
    value_78 = "padding_for_bounded_source_window"
    value_79 = "padding_for_bounded_source_window"
    return should_bypass_proxies()
```

运行 `scripts/p63_tool_demo.py` 可重现；完整 observation、未覆盖区间及命中元数据见 `experiments/p63/tool-demo.json`。
