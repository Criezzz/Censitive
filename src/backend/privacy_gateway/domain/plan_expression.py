from __future__ import annotations

import ast
import keyword
import re
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from typing import Any

from privacy_gateway.domain.redaction import PrivacyError

MAX_EXPR_CHARS = 1_000
MAX_AST_NODES = 128
MAX_AST_DEPTH = 32
MAX_ROUND_DIGITS = 12

_INPUT_TYPES = frozenset({"number", "numbers", "text"})
_CALLS = frozenset({"sum", "mean", "min", "max", "round", "dot"})
_DECIMAL_LEXEME = re.compile(r"(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?\Z")
_ROUND_LEXEME = re.compile(r"(?:0|[1-9]|1[0-2])\Z")

_ASCII_SOURCE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_+-*/()., \t\r\n\v\f"
)


def _fail(code: str) -> PrivacyError:
    return PrivacyError(code)


def _validate_input_types(input_types: dict[str, str]) -> dict[str, str]:
    if type(input_types) is not dict:
        raise _fail("PLAN_REFERENCE_INVALID")

    validated: dict[str, str] = {}
    for name, kind in input_types.items():
        if type(name) is not str or not _is_ascii_identifier(name):
            raise _fail("PLAN_REFERENCE_INVALID")
        if type(kind) is not str or kind not in _INPUT_TYPES:
            raise _fail("PLAN_REFERENCE_INVALID")
        if name in _CALLS:
            raise _fail("PLAN_REFERENCE_INVALID")
        validated[name] = kind
    return validated


def _is_ascii_identifier(value: str) -> bool:
    if not value or any(ord(char) >= 128 for char in value):
        return False
    if not value.isidentifier() or keyword.iskeyword(value):
        return False
    return True


def _check_source(text: str) -> None:
    if type(text) is not str:
        raise _fail("PLAN_EXPR_INVALID")
    if len(text) > MAX_EXPR_CHARS:
        raise _fail("PLAN_EXPR_LIMIT")
    if any(char not in _ASCII_SOURCE_CHARS for char in text):
        raise _fail("PLAN_EXPR_INVALID")


def _check_tree_limits(tree: ast.AST) -> int:
    count = 0
    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    while stack:
        node, depth = stack.pop()
        count += 1
        if count > MAX_AST_NODES or depth > MAX_AST_DEPTH:
            raise _fail("PLAN_EXPR_LIMIT")
        children = list(ast.iter_child_nodes(node))
        stack.extend((child, depth + 1) for child in children)
    return count


def _decimal_constant(text: str, node: ast.Constant) -> Decimal:
    if type(node.value) not in {int, float}:
        raise _fail("PLAN_EXPR_INVALID")
    source = ast.get_source_segment(text, node)
    if source is None or _DECIMAL_LEXEME.fullmatch(source) is None:
        raise _fail("PLAN_EXPR_INVALID")
    try:
        value = Decimal(source)
    except (InvalidOperation, ValueError, OverflowError):
        raise _fail("PLAN_EXPR_INVALID") from None
    if not value.is_finite():
        raise _fail("PLAN_EXPR_INVALID")
    return value


def _round_digits(text: str, node: ast.AST) -> int:
    if not isinstance(node, ast.Constant) or type(node.value) is not int:
        raise _fail("PLAN_EXPR_INVALID")
    source = ast.get_source_segment(text, node)
    if source is None or _ROUND_LEXEME.fullmatch(source) is None:
        raise _fail("PLAN_EXPR_INVALID")
    digits = node.value
    if digits < 0 or digits > MAX_ROUND_DIGITS:
        raise _fail("PLAN_EXPR_INVALID")
    return digits


def _lower(
    text: str,
    node: ast.AST,
    input_types: Mapping[str, str],
    *,
    depth: int,
    state: dict[str, int],
) -> tuple[dict[str, Any], str]:
    if depth > MAX_AST_DEPTH:
        raise _fail("PLAN_EXPR_LIMIT")

    if isinstance(node, ast.Constant):
        return {"op": "const", "value": _decimal_constant(text, node)}, "number"

    if isinstance(node, ast.Name):
        name = node.id
        kind = input_types.get(name)
        if kind is None:
            raise _fail("PLAN_REFERENCE_INVALID")
        reference_op = {"number": "ref", "numbers": "var", "text": "text_ref"}[kind]
        return {"op": reference_op, "name": name}, kind

    if isinstance(node, ast.BinOp):
        operation = {
            ast.Add: "add",
            ast.Sub: "sub",
            ast.Mult: "mul",
            ast.Div: "div",
        }.get(type(node.op))
        if operation is None:
            raise _fail("PLAN_EXPR_INVALID")
        left, left_type = _lower(text, node.left, input_types, depth=depth + 1, state=state)
        right, right_type = _lower(text, node.right, input_types, depth=depth + 1, state=state)
        if left_type != "number" or right_type != "number":
            raise _fail("PLAN_TYPE_INVALID")
        return {"op": operation, "left": left, "right": right}, "number"

    if isinstance(node, ast.UnaryOp):
        if type(node.op) is not ast.USub:
            raise _fail("PLAN_EXPR_INVALID")
        operand, operand_type = _lower(
            text, node.operand, input_types, depth=depth + 1, state=state
        )
        if operand_type != "number":
            raise _fail("PLAN_TYPE_INVALID")
        state["used"] += 1
        if state["used"] > MAX_AST_NODES:
            raise _fail("PLAN_EXPR_LIMIT")
        zero = {"op": "const", "value": Decimal(0)}
        return {"op": "sub", "left": zero, "right": operand}, "number"

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _CALLS:
            raise _fail("PLAN_EXPR_INVALID")
        if node.keywords:
            raise _fail("PLAN_EXPR_INVALID")
        function = node.func.id
        if function in {"sum", "mean", "min", "max"}:
            if len(node.args) != 1:
                raise _fail("PLAN_EXPR_INVALID")
            arg, arg_type = _lower(text, node.args[0], input_types, depth=depth + 1, state=state)
            if arg_type != "numbers":
                raise _fail("PLAN_TYPE_INVALID")
            return {"op": function, "arg": arg}, "number"
        if function == "round":
            if len(node.args) != 2:
                raise _fail("PLAN_EXPR_INVALID")
            arg, arg_type = _lower(text, node.args[0], input_types, depth=depth + 1, state=state)
            if arg_type != "number":
                raise _fail("PLAN_TYPE_INVALID")
            digits = _round_digits(text, node.args[1])
            return {"op": "round", "arg": arg, "digits": digits}, "number"
        if function == "dot":
            if len(node.args) != 2:
                raise _fail("PLAN_EXPR_INVALID")
            left, left_type = _lower(text, node.args[0], input_types, depth=depth + 1, state=state)
            right, right_type = _lower(
                text, node.args[1], input_types, depth=depth + 1, state=state
            )
            if left_type != "numbers" or right_type != "numbers":
                raise _fail("PLAN_TYPE_INVALID")
            return {"op": "dot", "left": left, "right": right}, "number"
        raise _fail("PLAN_EXPR_INVALID")

    raise _fail("PLAN_EXPR_INVALID")


def parse_expression(text: str, input_types: dict[str, str]) -> dict[str, Any]:
    _check_source(text)
    validated_inputs = _validate_input_types(input_types)
    try:
        tree = ast.parse(text, mode="eval")
    except (SyntaxError, ValueError, TypeError, RecursionError):
        raise _fail("PLAN_EXPR_INVALID") from None

    source_nodes = _check_tree_limits(tree)
    state = {"used": source_nodes}
    try:
        result, _result_type = _lower(text, tree.body, validated_inputs, depth=0, state=state)
    except RecursionError:
        raise _fail("PLAN_EXPR_LIMIT") from None
    return result


__all__ = [
    "MAX_AST_DEPTH",
    "MAX_AST_NODES",
    "MAX_EXPR_CHARS",
    "PrivacyError",
    "parse_expression",
]
