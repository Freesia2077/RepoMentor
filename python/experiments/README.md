# 实验结果

当前结果见[SWE-QA 50题报告](sweqa-50-2026-11-03/README.md)。本目录保留最终评测、来源原件和现有运行/测试依赖：

| 路径 | 保留用途 |
| --- | --- |
| `sweqa-50-2026-11-03/` | 主结果：50题、五组250个答案、评分、成本及失败归因 |
| `sweqa-20-review-2026-11-03/`、`sweqa-20-postfix-2026-11-03/` | 合并结果复用的原始答案、评分、修复前后记录与方法源码；按原字节保留 |
| `public-sweqa-2026-11-03/` | 旧20题封存报告引用的首轮公开评测与选题来源 |
| `business-steps-submission/` | 面试文档引用的报告提交与预算问题案例 |
| `rag-unified/`、`rag-interview/`、`rag-expansion/` | 默认benchmark使用的48题及其问题/参考来源、原始尝试和评审 |
| `p0/` | 冻结仓库划分、许可证、源码证据及离线测试种子 |
| `p63/`、`qwen9b-reliability/` | 保留的旧问题、对话及评测参考，供兼容工具使用 |
| `p5/`、`qwen9b-diagnostic/` | 检索模型与9B模型准备脚本使用的来源、版本和哈希 |
| `question-bank.json` | 从保留来源重建的定位索引；统计按各原始评测批次解释 |

从Python目录运行以下离线命令：

```powershell
.\run.ps1 scripts/verify_archives.py
.\run.ps1 -m repomentor.cli questions verify
.\run.ps1 -m repomentor.cli inspect-run experiments/sweqa-50-2026-11-03/combined/results.jsonl --case-id sweqa-flask-042--hybrid_rerank
```

早期部署日志、重复回放和已退出维护的诊断批次已移出活动目录。历史原件封存于本地忽略路径`.runtime/archives/experiments-cleanup-2026-11-10/`，包含可校验的ZIP与逐文件SHA256清单；Git历史亦保留已提交版本。

最终50题的答案、评分和来源清单保持原样，模型失败与原始数据划分均保留。各批次独立解释，流程状态与答案质量分别报告；这些已曝光的评测记录不作为后训练数据。
