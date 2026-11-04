"""Bounded navigation memory: user intentions only, never a substitute for retrieved source."""

import re


def navigation_summary(history, *, maximum=8, max_chars=1200):
    old = history[:-8]
    questions = list(dict.fromkeys(row["content"] for row in old if row["role"] == "user"))
    selected, used = [], 0
    # Keep both the earliest goal and recent omitted goals, with explicit omission counts.
    indices = (
        list(dict.fromkeys([0, *range(max(1, len(questions) - maximum + 1), len(questions))]))
        if questions
        else []
    )
    for i in indices:
        value = questions[i]
        if used + len(value) <= max_chars:
            selected.append(value)
            used += len(value)
    return {
        "kind": "extractive_user_navigation_v1",
        "past_user_questions": selected,
        "omitted_questions": len(questions) - len(selected),
        "source_facts": False,
    }


def first_symbol(questions):
    ignored = {"node", "node.js", "readme", "map", "javascript", "typescript", "python"}
    for question in questions:
        symbols = [s for s in re.findall(r"[A-Za-z_][A-Za-z_0-9.]*", question) if s.lower() not in ignored]
        if symbols:
            return symbols[-1]
    return None


def resolve_query(question, previous, current_symbol, memory=None, *, enabled=True):
    from .conversation import followup_query

    if not enabled:
        return question
    if memory and re.search(r"最开始|最早|第一个|起初", question):
        symbol = first_symbol(memory["past_user_questions"])
        if symbol:
            return f"{question}\n追问对象：{symbol}"
    return followup_query(question, previous, current_symbol)
