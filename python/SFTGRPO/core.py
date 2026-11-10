"""Shared data protocol; no model calls, training loop, or application configuration."""
import hashlib
import json
import re
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Optional preparation wheels stay inside this project, leaving the RAG environment intact.
if (ROOT / ".cache/site-packages").is_dir():
    sys.path.insert(0, str(ROOT / ".cache/site-packages"))


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def question_key(text: str) -> str:
    return digest(" ".join(text.casefold().split()))


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def data_path(name):
    return ROOT / load_json(ROOT / "run-config.json")["data"]["directory"] / name


def report_path(name):
    return ROOT / load_json(ROOT / "run-config.json")["data"]["reports_directory"] / name


def supervision_labels(labels, mode):
    """Change targets only; preserve the identical complete teacher input."""
    if mode not in {"all_assistant", "last_assistant"}:
        raise ValueError(f"Unknown supervision mode: {mode}")
    positions = [i for i, value in enumerate(labels) if value != -100]
    if not positions:
        raise ValueError("Missing assistant targets")
    if mode == "all_assistant":
        return list(labels)
    # The closed terminal assistant is the final contiguous generation span,
    # including its EOS; trailing template whitespace can remain unsupervised.
    start = positions[-1]
    while start and labels[start - 1] != -100:
        start -= 1
    return [-100] * start + list(labels[start:])


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_rows(path):
    with Path(path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def write_rows(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def normalize(row):
    """Restore recorded observations, pair calls FIFO, retain all teacher evidence."""
    messages, pending = [], deque()
    calls = restored = 0

    def observation(content):
        call_id, name = pending.popleft()
        messages.append({"role": "tool", "content": content, "tool_call_id": call_id, "name": name})

    for message in row["messages"]:
        role, content = message["role"], message.get("content", "")
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        if role == "tool_call":
            call = json.loads(content)
            arguments = call["arguments"]
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be an object")
            call_id = f"call_{calls}"
            calls += 1
            entry = {"id": call_id, "type": "function", "function": {"name": call["name"], "arguments": arguments}}
            if not messages or messages[-1]["role"] != "assistant":
                messages.append({"role": "assistant", "content": ""})
            messages[-1].setdefault("tool_calls", []).append(entry)
            pending.append((call_id, call["name"]))
        elif role == "tool_response":
            observation(content)
        elif role == "user" and (match := re.fullmatch(r"\s*<tool_response>\s*(.*?)\s*</tool_response>(.*)", content, re.S)):
            observation(match[1])
            restored += 1
            if match[2].strip():
                messages.append({"role": "user", "content": match[2]})
        elif role in {"system", "user", "assistant"}:
            messages.append({"role": role, "content": content})
        else:
            raise ValueError(f"Unexpected role {role}")
    if pending or not messages or messages[-1]["role"] != "assistant":
        raise ValueError("Unpaired call or missing final assistant")
    answers = re.findall(r"<finish>(.*?)</finish>", messages[-1]["content"], re.S)
    if not answers or not answers[-1].strip():
        raise ValueError("Missing final answer")
    return {"messages": messages, "tools": row["tools"]}, {"calls": calls, "restored": restored}


def trajectory_audit(messages):
    """Behavior flags are review cues, not automatic semantic rejection labels."""
    calls = [call for m in messages for call in m.get("tool_calls", [])]
    fingerprints = [json.dumps(call["function"], sort_keys=True, ensure_ascii=False) for call in calls]
    observations = [m.get("content", "") for m in messages if m["role"] == "tool"]
    characters = sum(len(m.get("content", "")) for m in messages)
    return {"tool_calls": len(calls),
            "tool_rounds": sum(bool(m.get("tool_calls")) for m in messages),
            "duplicate_requests": len(fingerprints) - len(set(fingerprints)),
            "external_finalization": any(m["role"] == "user" and "MAX TURNS REACHED" in m.get("content", "") for m in messages),
            "error_observations": sum(text.lstrip().startswith("ERROR:") for text in observations),
            "observation_character_share": sum(map(len, observations)) / max(1, characters)}


def identity(row):
    user = next(m["content"] for m in row["messages"] if m["role"] == "user")
    path = re.search(r"Repository Path:\s*(\S+)", user)[1]
    question = re.search(r"Question:\s*(.*?)(?:\n\s*\n|\Z)", user, re.S)[1].strip()
    return path.rstrip("/").split("/")[-1].casefold(), question
