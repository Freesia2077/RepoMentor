"""Persistent sequential conversations; request IDs make completed turns replayable."""

import json
import re
from pathlib import Path
from uuid import uuid4

from .records import atomic_write
from .runtimes.chat import CompletionResult, fingerprint

FOLLOWUP = r"它|这个|该函数|上述|那么|对应|继续|然后|那它|\b(it|that|those)\b"


def followup_query(question, previous_question, current_symbol=None):
    # Deterministic, inspectable query resolution; no paid rewrite calls or gold history.
    # A connective such as “那么” does not make an explicitly named new call ambiguous.
    if re.search(r"\b[A-Za-z_][\w.]*(?:\s*\(|`)", question):
        return question
    if re.search(FOLLOWUP, question, re.I):
        context = current_symbol or previous_question
        return f"{question}\n追问对象：{context}" if context else question
    return question


class ConversationStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, session_id):
        if not re.fullmatch(r"[0-9a-f]{32}", session_id):
            raise ValueError("invalid session ID")
        return self.root / (session_id + ".json")

    def create(
        self,
        snapshot_id,
        goal="理解仓库",
        *,
        session_id=None,
        history_mode="recent",
        query_rewrite="rules",
        retrieval_config=None,
    ):
        if history_mode not in ("recent", "summary") or query_rewrite not in ("none", "rules"):
            raise ValueError("unknown conversation policy")
        session_id = session_id or uuid4().hex
        path = self.path(session_id)
        if path.exists():
            raise ValueError("session already exists")
        state = {
            "schema_version": "repomentor.conversation.v1",
            "session_id": session_id,
            "snapshot_id": snapshot_id,
            "goal": goal,
            "history_mode": history_mode,
            "query_rewrite": query_rewrite,
            "retrieval_config": retrieval_config,
            "current_symbol": None,
            "turns": [],
            "pending": None,
            "task_state": {"goal": goal, "entry": None, "current_symbol": None,
                           "unresolved_questions": [], "source_refs": [], "source_facts": False},
        }
        self.save(state)
        return state

    def load(self, session_id):
        return json.loads(self.path(session_id).read_text(encoding="utf-8"))

    def save(self, state):
        atomic_write(self.path(state["session_id"]), json.dumps(state, ensure_ascii=False, indent=2))

    async def turn(self, session_id, engine, question=None, *, request_id=None, token=None, resume=False):
        state = self.load(session_id)
        if state["snapshot_id"] != engine.reader.snapshot.snapshot_id:
            raise ValueError("session snapshot cannot change")
        if request_id:
            existing = next((row for row in state["turns"] if row["request_id"] == request_id), None)
            if existing:
                if question and existing["question"] != question:
                    raise ValueError("request ID reused with another question")
                return {**existing, "replayed": True, "new_model_requests": 0}
        if state["pending"]:
            if not resume:
                raise ValueError("unfinished turn exists; resume it explicitly")
            pending = state["pending"]
            if question and question != pending["question"]:
                raise ValueError("resume question differs")
            question, request_id = pending["question"], pending["request_id"]
        else:
            if resume or not question or not question.strip():
                raise ValueError("new turn requires a question; resume requires pending work")
            request_id = request_id or uuid4().hex
            pending = {"question": question, "request_id": request_id}
            state["pending"] = pending
            self.save(state)
        history = []
        for row in state["turns"]:
            history.extend(
                [
                    {"role": "user", "content": row["question"]},
                    {"role": "assistant", "content": row["result"]["answer"]["answer"]},
                ]
            )
        previous = state["turns"][-1]["question"] if state["turns"] else ""
        from .context_memory import navigation_summary, resolve_query

        memory = navigation_summary(history) if state.get("history_mode") == "summary" else None
        if memory and not memory["past_user_questions"]:
            memory = None
        query = resolve_query(
            question,
            previous,
            state["current_symbol"],
            memory,
            enabled=state.get("query_rewrite", "rules") == "rules",
        )
        identifiers = re.findall(r"[A-Za-z_][A-Za-z_0-9./]*", question)
        meaningful = [
            word
            for word in identifiers
            if word.lower() not in {"it", "that", "those", "how", "does", "work", "the", "test", "tests"}
        ]
        if (
            not history
            and re.search(FOLLOWUP, question, re.I)
            and not meaningful
            and not re.search(r"仓库|项目", question)
        ):
            if token:
                token.check()
            answer = {
                "answer": "请先说明要了解的函数、文件或模块；当前会话还没有可确定的追问对象。",
                "citations": [],
                "insufficient_evidence": True,
            }
            result = {
                "attempt_id": uuid4().hex,
                "answer": answer,
                "context": [],
                "input_messages": [],
                "completion": CompletionResult(
                    status="success",
                    content=json.dumps(answer, ensure_ascii=False),
                    finish_reason="stop",
                    request_hash=fingerprint(["clarification-v1", state["snapshot_id"], question]),
                ).model_dump(),
                "cache_hit": False,
                "new_model_requests": 0,
                "handling": "deterministic_clarification",
            }
        else:
            options = {}
            if getattr(engine, "supports_source_sharing", False):
                navigation = state.get("task_state") or {"goal": state["goal"], "entry": None}
                options["task_context"] = navigation
                if state["turns"] and re.search(FOLLOWUP + r"|切换|换到|switch", question, re.I):
                    previous_result = state["turns"][-1]["result"]
                    options["shared_context"] = {"snapshot_id": state["snapshot_id"],
                        "spans": previous_result.get("final_context", previous_result.get("context", [])),
                        "preferred_ranges": previous_result.get("answer", {}).get("citations", [])}
            result = await engine.answer(
                question,
                history=history,
                query=query,
                token=token,
                refresh=bool(resume and pending.get("last_attempt_id")),
                memory=memory,
                **options,
            )
        row = {
            "request_id": request_id,
            "question": question,
            "retrieval_query": query,
            "result": result,
            "replayed": False,
            "new_model_requests": result["new_model_requests"],
        }
        if result["completion"]["status"] == "success":
            state["turns"].append(row)
            if meaningful:
                state["current_symbol"] = meaningful[-1]
            if getattr(engine, "supports_source_sharing", False):
                resolved_symbol = result.get("symbol_navigation", {}).get("current_symbol")
                state["current_symbol"] = resolved_symbol
                state["task_state"] = {
                    "goal": state["goal"], "entry": result.get("entry_focus", {}).get("label"),
                    "current_symbol": state["current_symbol"],
                    "unresolved_questions": [question] if result["answer"]["insufficient_evidence"] else [],
                    "source_refs": [{k: s[k] for k in ("path", "start_line", "end_line", "content_sha256")}
                                    for s in result.get("final_context", [])[:8]],
                    "source_facts": False,
                }
            state["pending"] = None
        else:
            state["pending"]["last_attempt_id"] = result["attempt_id"]
        self.save(state)
        return row
