"""Инструменты: time.now (время/дата) и math.calc (безопасный калькулятор)."""

from __future__ import annotations

import ast
import operator as op
from datetime import datetime
from typing import Any, Dict

from ...kernel.tools import ToolResult

_OPS = {
    ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv, ast.Mod: op.mod, ast.Pow: op.pow,
    ast.USub: op.neg, ast.UAdd: op.pos,
}
_FUNCS = {
    "sqrt": __import__("math").sqrt, "sin": __import__("math").sin,
    "cos": __import__("math").cos, "log": __import__("math").log,
    "log10": __import__("math").log10, "abs": abs, "round": round,
    "min": min, "max": max, "floor": __import__("math").floor,
    "ceil": __import__("math").ceil,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval_node(node.operand))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
            and node.func.id in _FUNCS and not node.keywords:
        args = [_eval_node(a) for a in node.args]
        return _FUNCS[node.func.id](*args)
    raise ValueError(f"недопустимое выражение: {ast.dump(node)}")


def safe_eval(expr: str) -> float:
    expr = expr.replace("×", "*").replace("÷", "/").replace(",", ".")
    expr = expr.replace("^", "**").strip()
    tree = ast.parse(expr, mode="eval")
    return _eval_node(tree)


def time_handler() -> Dict[str, Any]:
    now = datetime.now()
    wd = ["понедельник", "вторник", "среда", "четверг",
          "пятница", "суббота", "воскресение"][now.weekday()]
    text = (f"Сейчас {now:%H:%M:%S} — {now:%d %B %Y}, {wd}. "
            f"Часовой пояс системы: {now.astimezone().tzname()}.")
    return {"text": text, "iso": now.isoformat(),
            "weekday": wd, "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M:%S")}


def math_handler(expression: str) -> Dict[str, Any]:
    expr = (expression or "").strip()
    if not expr:
        return {"text": "Укажите выражение для вычисления.", "ok": False}
    try:
        value = safe_eval(expr)
    except Exception as e:  # noqa: BLE001
        return {"text": f"Не удалось вычислить «{expr}»: {e}", "ok": False,
                "error": str(e)}
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        value = int(value)
    return {"text": f"≈ {value}", "result": value, "expression": expr, "ok": True}


def time_spec():
    from ...kernel.tools import ToolSpec, ToolParam
    return ToolSpec(
        name="time.now", title="Время и дата",
        description="Текущее время, дата и день недели по часовой поясу системы.",
        handler=time_handler, category="info", timeout=3.0, icon="🕒",
    )


def math_spec():
    from ...kernel.tools import ToolSpec, ToolParam
    return ToolSpec(
        name="math.calc", title="Калькулятор",
        description="Безопасное вычисление арифметических выражений "
                    "(+ − × ÷ ^, скобки, sqrt/sin/cos/log…).",
        handler=math_handler, category="info", timeout=5.0, icon="🧮",
        params=[ToolParam("expression", "string", "Выражение, например: 2+2*3", True)],
    )
