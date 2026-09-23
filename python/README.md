# RepoMentor Python

RepoMentor 的独立 Python CLI / 库，也是后续开发的主版本。当前实现 P0–P2：不可变仓库快照、受控仓库工具与 Harness、Claude Agent SDK 三阶段报告。原 TS Web 版在仓库根目录保留。对话、RAG 和 MiniMind/Qwen 实验尚未实现，见[路线图](notes/roadmap.md)。

## 新机器安装

需要 Git 和 Python 3.12 或 3.13；不需要 Node、npm 或 TS 服务。在自己的项目环境中进入 `python/` 后执行：

```sh
python -s -m pip install -e '.[dev]'
python -s -m repomentor.cli doctor
python -s -m pytest -q
```

运行 SDK 报告前配置 Python 自己的 `config/model-settings.json`，或设置专属的 `REPOMENTOR_PY_*` 环境变量，见下文。Python 不读取 TS 配置或共享 `ANTHROPIC_*` 模型变量。只验证核心可用 `--runtime stub`，无需模型或凭据。

## 使用指定环境

本机解释器固定为 `D:\anaconda\envs\repomentor\python.exe`。`run.ps1` 指定此路径，并关闭用户级 site-packages，避免混入其他 Python 环境的包。

```powershell
cd 'D:\Edge Download\Coding\python'
.\run.ps1 -m repomentor.cli doctor
.\run.ps1 -m repomentor.cli --help
```

安装已在本机完成。需要在同一环境重现时：

```powershell
.\run.ps1 -m pip install -r requirements-win-py312.lock
.\run.ps1 -m pip install --no-deps -e .
.\run.ps1 -m pip check
```

锁文件针对 Windows x64 / Python 3.12；Linux 训练机应重新解析平台依赖并记录新锁文件。P0 的 Torch、CUDA 和检索检查仅检查可用安装包元数据，没有安装训练依赖或验证 CUDA 算子。

## Python 独立配置与数据

首次配置，在 `python/` 下复制模板，填写 `apiKey`、`baseUrl`、`model`。本地配置被 Git 忽略；模板不含密钥。不要覆盖已有配置。

```powershell
Copy-Item config\model-settings.example.json config\model-settings.json
```

也可只用 Python 专属环境变量（三项一起设置）：

```powershell
$env:REPOMENTOR_PY_API_KEY = '<your-api-key>'
$env:REPOMENTOR_PY_BASE_URL = 'https://your-provider.example/anthropic'
$env:REPOMENTOR_PY_MODEL = '<your-model>'
```

配置来源按整体优先级选择：`--settings` 文件 → `REPOMENTOR_PY_SETTINGS` 文件 → 上述环境变量组 → 默认配置文件。不同来源不合并字段；选中的配置缺失或无效会报错，不回退到 TS 或其他来源。`.env` 文件不会自动加载。

| 用途 | 源码版默认位置 | 可选覆盖 |
| --- | --- | --- |
| Python 主目录 | `python/`，与启动位置无关 | `REPOMENTOR_PY_HOME` |
| 模型配置 | 主目录下 `config/model-settings.json` | `--settings` / `REPOMENTOR_PY_SETTINGS` |
| 私有数据根目录 | 主目录下 `artifacts/` | `REPOMENTOR_PY_DATA_DIR` |
| CLI 快照、数据库与报告 | 数据根目录下 `runtime/` | `--data-dir` 仅覆盖这次 CLI 存储位置 |
| 语料缓存 / 实验轨迹 / SDK 临时目录 | 数据根目录下 `corpus/`、`live/`、`sdk-tmp/` | 随数据根目录移动 |

路径类环境变量必须是绝对路径。独立 wheel 安装的默认主目录为 `~/.repomentor-python/`。已有 Python 数据仍在原位置，无需搬迁；模型配置需独立填写，不自动读取或复制旧 TS 配置。`doctor` 可查看解析后的路径，不读取或显示密钥。

## 独立生成报告

```powershell
.\run.ps1 -m repomentor.cli report --repository https://github.com/developit/mitt
```

首次使用 `HEAD` 解析当前提交；严肃实验使用冻结清单中的完整 SHA：

```powershell
.\run.ps1 -m repomentor.cli report --repository https://github.com/developit/mitt --revision 6b41670516ed8e8b738612f60491995470aa63b3 --output artifacts\mitt-report.json
```

无模型调用的核心验证：

```powershell
.\run.ps1 -m repomentor.cli report --repository .. --runtime stub
```

本地仓库读取已提交版本，忽略未提交/未跟踪内容；stub 输出明确标记为测试结果。运行时不执行目标仓库的安装、构建、测试、脚本或指令。

默认 CLI 产物写入 Python 主目录的 `artifacts/runtime/`，从仓库根目录启动也不会写入 TS 的目录：

- `snapshots/`：Git 对象缓存与固定 SHA 的文件 manifest，没有可变化的共享 checkout。
- `runs/<run_id>/events.jsonl`：不含源码正文和密钥的工具、权限、状态、用量轨迹。
- `runs/<run_id>/result.json`：三个阶段的结构化报告、错误分类、预算和版本信息。
- `repomentor-python.sqlite3`：Python 独立运行账本。

`Ctrl+C` 取消调用；默认单次模型调用 180 秒、整份报告 600 秒。失败也保留已完成阶段与工具轨迹。`export` 从账本导出既有结果，不重跑工具。

## 直接调用核心

```python
from repomentor.config import load_model_settings
from repomentor.paths import runtime_dir
from repomentor.repository import SnapshotStore
from repomentor.report import run_report
from repomentor.runtimes.claude import ClaudeSdkRuntime

reader = SnapshotStore(runtime_dir() / "snapshots").import_repository(
    "developit/mitt", "6b41670516ed8e8b738612f60491995470aa63b3"
)
runtime = ClaudeSdkRuntime(load_model_settings())
# Notebook 中使用 await；普通脚本中使用 asyncio.run(...)
result = await run_report(reader, runtime)
```

CLI、Notebook、未来 Web 层都调用同一业务函数，不依赖 FastAPI、Fastify、React 或 SSE。Qwen/MiniMind 的通用工具循环属于 P3/P6，不能通过修改 Claude SDK 的模型名代替实现。

## 验证与实验记录

```powershell
.\run.ps1 -m pytest -q -p no:cacheprovider
.\run.ps1 -m ruff check --no-cache src scripts tests
.\run.ps1 -m ruff format --check --no-cache src scripts tests
```

默认测试只运行 Python，不访问模型或下载仓库。仓库快照核验测试在没有本地 P0 缓存时跳过。历史跨语言对照单独标为 `ts_parity`，只有明确需要对照时才执行 `pytest -m ts_parity`（另需原仓库 Node/tsx）；日常开发不运行。

- [整体结构与 TS 对应关系](notes/architecture.md)
- [配置与数据隔离规则](notes/configuration.md)
- [后续路线图与两个项目的关联](notes/roadmap.md)
- [阶段验收与已知限制](notes/p0-p2-acceptance.md)
- [数据划分、标注与复核规范](notes/data-protocol.md)
- [SDK 迁移检查表与面试笔记](notes/sdk-migration.md)
- `experiments/p0/`：上游版本、20 仓库清单、去重审计、60 条验证种子、旧 8 仓库修正清单。
- `experiments/p1/`：发布前复查记录；机器安装日志、绝对路径审计及原始 pytest XML 留在本地。
- `experiments/p2/`：真实 SDK 调用、三阶段报告、取消测试和旧仓库回归结果。

真实运行使用用户原有的 `deepseek-v4-flash` Anthropic-compatible 配置；不能把这些记录写成 Qwen、MiniMind 或官方 Claude 的实验。SDK 报告的美元成本字段不等同于兼容服务的实际账单。

当前的引用校验保证“路径来自已读内容”，不保证语义必然成立。已记录真实回答错误供后续 P4–P6 评测与改进，不能用结构验证通过率代替答案正确率。

## API 成本与结果复用

后续开发优先运行 pytest、stub 和 `scripts/verify_recorded_results.py`，这些不会调用模型。`run_live_baseline.py` 默认复用已有相同 SHA 的运行记录，只有显式 `--refresh` 才替换；复用历史记录不等于重新验证改动后的模型行为。`run_legacy_suite.py` 默认不为缺失记录发起调用，必须显式 `--allow-new-api`。

CLI 的 `report --runtime claude` 是真实报告请求，会调用 API；查看旧报告请打开保存的 JSON 或用 `export`，不要重复执行 report。选定的公开仓库实验记录随源码保存在 `experiments/`；凭据、本地数据库、源码缓存和运行事件目录不提交。

查看示例：[mitt 三阶段报告](experiments/p2/developit--mitt-report.json)。在 JSON 中展开 `outputs.explorer`、`outputs.mentor`、`outputs.contributor`。Python 暂无图形报告页面。
