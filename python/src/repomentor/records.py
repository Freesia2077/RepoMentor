"""Atomic persistence shared by runtime and offline evaluation."""

# 统一写入运行记录；先写临时文件再原子替换，减少中断留下半份 JSON 的风险。

import json
from pathlib import Path
from uuid import uuid4


# 临时文件写完后原子替换目标，读者只会看到写入前或写入后的完整记录。
def atomic_write(path: Path, content: str):
    import time

    path.parent.mkdir(parents=True, exist_ok=True)
    # Keep temp names short enough for common Windows paths without long-path opt-in.
    temporary = path.with_name(".tmp-" + uuid4().hex)
    try:
        temporary.write_text(content, encoding="utf-8")
        # Windows readers/indexers can briefly hold the destination without delete sharing.
        # Retry only replacement of this completed temp file; never repeat model work.
        for attempt in range(7):
            try:
                temporary.replace(path)
                break
            except PermissionError:
                if attempt == 6:
                    raise
                time.sleep(0.05 * 2**attempt)
    finally:
        temporary.unlink(missing_ok=True)


def write(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2))
