from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    Decimal,
    InvalidOperation,
    localcontext,
)
from fractions import Fraction
from typing import Any, Literal, TypeGuard

from privacy_gateway.domain.plan_expression import MAX_EXPR_CHARS, parse_expression
from privacy_gateway.domain.redaction import PrivacyError

MAX_INPUTS = 8
MAX_OUTPUTS = 8
MAX_NAME_LENGTH = 64
MAX_INSTRUCTION_LENGTH = 4_000
MAX_AST_DEPTH = 32
MAX_AST_NODES = 128
MAX_VECTOR_LENGTH = 1_024
MAX_NUMBER_DIGITS = 50
MAX_ABS_ADJUSTED_EXPONENT = 100
MAX_DECIMAL_EXPONENT = 100
MAX_ROUND_DIGITS = 12
MAX_FRACTION_DIGITS = 256
MAX_FRACTION_BITS = 1_024
MAX_OUTPUT_BYTES = 8_192
MAX_TEMPLATE_BYTES = 8_192
MAX_TEMPLATE_SLOTS = 64
MAX_RENDER_BYTES = MAX_TEMPLATE_BYTES * 3
MAX_LOCAL_SCHEMA_FIELDS = 8
MAX_LOCAL_SCHEMA_REQUIRED = 8
MAX_LOCAL_SCHEMA_CHARS = 4_000
MAX_LOCAL_SCHEMA_ITEMS = 64
MAX_LOCAL_SCHEMA_HASH_BYTES = 16_384

type InputType = Literal["number", "numbers", "text"]
type OutputFormat = Literal["json", "text"]
type ExprType = Literal["number", "numbers", "text"]
type OutputValueType = Literal[
    "string", "number", "integer", "boolean", "string_list", "number_list"
]


@dataclass(frozen=True)
class PlanInput:
    name: str
    type: InputType
    instruction: str


@dataclass(frozen=True)
class Expr:
    op: Literal[
        "const",
        "ref",
        "var",
        "text_ref",
        "sum",
        "mean",
        "min",
        "max",
        "add",
        "sub",
        "mul",
        "div",
        "round",
        "dot",
    ]
    name: str | None = None
    value: Decimal | None = None
    arg: Expr | None = None
    left: Expr | None = None
    right: Expr | None = None
    digits: int | None = None


@dataclass(frozen=True)
class ComputePlan:
    version: Literal[1]
    kind: Literal["compute"]
    inputs: tuple[PlanInput, ...]
    outputs: dict[str, Expr]


@dataclass(frozen=True)
class TemplateRender:
    kind: Literal["template"]
    text: str


@dataclass(frozen=True)
class SignRender:
    kind: Literal["sign"]
    selector: str
    negative: str
    zero: str
    positive: str


type ComposeRender = TemplateRender | SignRender


@dataclass(frozen=True)
class ComposePlan:
    version: Literal[3]
    kind: Literal["compose"]
    compute: ComputePlan
    render: ComposeRender


@dataclass(frozen=True)
class LocalAnswerPlan:
    version: Literal[1, 3]
    kind: Literal["local_answer"]
    instruction: str
    output_format: OutputFormat
    output_schema: dict[str, Any] | None = None


type TypedPlan = ComputePlan | ComposePlan | LocalAnswerPlan

_SCALAR_OPS = {"add", "sub", "mul", "div"}
_AGGREGATE_OPS = {"sum", "mean", "min", "max"}
_ALL_OPS = {
    "const",
    "ref",
    "var",
    "text_ref",
    *_AGGREGATE_OPS,
    *_SCALAR_OPS,
    "round",
    "dot",
}
_NAME_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
_SLOT_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*\Z")


def planner_output_schema() -> dict[str, Any]:
    input_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": MAX_NAME_LENGTH},
            "type": {"type": "string", "enum": ["number", "numbers", "text"]},
            "instruction": {"type": "string", "minLength": 1, "maxLength": MAX_INSTRUCTION_LENGTH},
        },
        "required": ["name", "type", "instruction"],
    }
    output_item = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "key": {"type": "string", "minLength": 1, "maxLength": MAX_NAME_LENGTH},
            "expression": {"type": "string", "minLength": 1, "maxLength": MAX_EXPR_CHARS},
        },
        "required": ["key", "expression"],
    }
    string_field = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": MAX_NAME_LENGTH},
            "type": {"type": "string", "const": "string"},
            "max_length": {"type": "integer", "minimum": 1, "maximum": MAX_LOCAL_SCHEMA_CHARS},
        },
        "required": ["name", "type", "max_length"],
    }
    simple_field = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": MAX_NAME_LENGTH},
            "type": {"type": "string", "enum": ["number", "integer", "boolean"]},
        },
        "required": ["name", "type"],
    }
    string_list_field = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": MAX_NAME_LENGTH},
            "type": {"type": "string", "const": "string_list"},
            "max_items": {"type": "integer", "minimum": 1, "maximum": MAX_LOCAL_SCHEMA_ITEMS},
            "item_max_length": {"type": "integer", "minimum": 1, "maximum": MAX_LOCAL_SCHEMA_CHARS},
        },
        "required": ["name", "type", "max_items", "item_max_length"],
    }
    number_list_field = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "minLength": 1, "maxLength": MAX_NAME_LENGTH},
            "type": {"type": "string", "const": "number_list"},
            "max_items": {"type": "integer", "minimum": 1, "maximum": MAX_LOCAL_SCHEMA_ITEMS},
        },
        "required": ["name", "type", "max_items"],
    }
    text_field = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "name": {"type": "string", "const": "answer"},
            "type": {"type": "string", "const": "text"},
            "max_length": {"type": "integer", "minimum": 1, "maximum": MAX_LOCAL_SCHEMA_CHARS},
        },
        "required": ["name", "type", "max_length"],
    }
    json_field = {"anyOf": [string_field, simple_field, string_list_field, number_list_field]}
    local_text_plan = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "version": {"type": "integer", "const": 3},
            "kind": {"type": "string", "const": "local_answer"},
            "instruction": {"type": "string", "minLength": 1, "maxLength": MAX_INSTRUCTION_LENGTH},
            "output_format": {"type": "string", "const": "text"},
            "answer_fields": {"type": "array", "minItems": 1, "maxItems": 1, "items": text_field},
        },
        "required": ["version", "kind", "instruction", "output_format", "answer_fields"],
    }
    local_json_plan = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "version": {"type": "integer", "const": 3},
            "kind": {"type": "string", "const": "local_answer"},
            "instruction": {"type": "string", "minLength": 1, "maxLength": MAX_INSTRUCTION_LENGTH},
            "output_format": {"type": "string", "const": "json"},
            "answer_fields": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_LOCAL_SCHEMA_FIELDS,
                "items": json_field,
            },
        },
        "required": ["version", "kind", "instruction", "output_format", "answer_fields"],
    }
    local_legacy_plan = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "version": {"type": "integer", "const": 3},
            "kind": {"type": "string", "const": "local_answer"},
            "instruction": {"type": "string", "minLength": 1, "maxLength": MAX_INSTRUCTION_LENGTH},
            "output_format": {"type": "string", "enum": ["json", "text"]},
            "answer_contract": {"type": "string", "const": "unconstrained"},
        },
        "required": ["version", "kind", "instruction", "output_format", "answer_contract"],
    }
    compute_plan = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "version": {"type": "integer", "const": 3},
            "kind": {"type": "string", "const": "compute"},
            "inputs": {"type": "array", "maxItems": MAX_INPUTS, "items": input_item},
            "outputs": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_OUTPUTS,
                "items": output_item,
            },
        },
        "required": ["version", "kind", "inputs", "outputs"],
    }
    template_render = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string", "const": "template"},
            "text": {"type": "string", "minLength": 1, "maxLength": MAX_TEMPLATE_BYTES},
        },
        "required": ["kind", "text"],
    }
    sign_render = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "kind": {"type": "string", "const": "sign"},
            "selector": {"type": "string", "minLength": 1, "maxLength": MAX_NAME_LENGTH},
            "negative": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_TEMPLATE_BYTES,
            },
            "zero": {"type": "string", "minLength": 1, "maxLength": MAX_TEMPLATE_BYTES},
            "positive": {
                "type": "string",
                "minLength": 1,
                "maxLength": MAX_TEMPLATE_BYTES,
            },
        },
        "required": ["kind", "selector", "negative", "zero", "positive"],
    }
    compose_plan = {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "version": {"type": "integer", "const": 3},
            "kind": {"type": "string", "const": "compose"},
            "inputs": {"type": "array", "maxItems": MAX_INPUTS, "items": input_item},
            "outputs": {
                "type": "array",
                "minItems": 1,
                "maxItems": MAX_OUTPUTS,
                "items": output_item,
            },
            "render": {"anyOf": [template_render, sign_render]},
        },
        "required": ["version", "kind", "inputs", "outputs", "render"],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "plan": {
                "anyOf": [
                    compute_plan,
                    local_text_plan,
                    local_json_plan,
                    local_legacy_plan,
                    compose_plan,
                ]
            }
        },
        "required": ["plan"],
    }


def planner_schema_hash() -> str:
    encoded = json.dumps(
        planner_output_schema(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _schema_int(value: object, *, maximum: int) -> int:
    if type(value) is not int or not 1 <= value <= maximum:
        raise _fail("PLAN_RESPONSE_INVALID")
    return value


def _lower_answer_fields(fields: object, output_format: str) -> dict[str, Any]:
    if type(fields) is not list:
        raise _fail("PLAN_RESPONSE_INVALID")
    if output_format == "text":
        if len(fields) != 1 or not _is_wire_dict(fields[0]):
            raise _fail("PLAN_RESPONSE_INVALID")
        field = fields[0]
        _expect_keys(field, {"name", "type", "max_length"}, code="PLAN_RESPONSE_INVALID")
        if field["name"] != "answer" or field["type"] != "text":
            raise _fail("PLAN_RESPONSE_INVALID")
        maximum = _schema_int(field["max_length"], maximum=MAX_LOCAL_SCHEMA_CHARS)
        return {"type": "string", "maxLength": maximum}
    if output_format != "json" or not 1 <= len(fields) <= MAX_LOCAL_SCHEMA_FIELDS:
        raise _fail("PLAN_RESPONSE_INVALID")
    properties: dict[str, Any] = {}
    required: list[str] = []
    for field in fields:
        if not _is_wire_dict(field):
            raise _fail("PLAN_RESPONSE_INVALID")
        field_type = field.get("type")
        name = _validate_output_field_name(field.get("name"))
        if name in properties or type(field_type) is not str:
            raise _fail("PLAN_RESPONSE_INVALID")
        if field_type == "string":
            _expect_keys(field, {"name", "type", "max_length"}, code="PLAN_RESPONSE_INVALID")
            properties[name] = {
                "type": "string",
                "maxLength": _schema_int(field["max_length"], maximum=MAX_LOCAL_SCHEMA_CHARS),
            }
        elif field_type in {"number", "integer", "boolean"}:
            _expect_keys(field, {"name", "type"}, code="PLAN_RESPONSE_INVALID")
            properties[name] = {"type": field_type}
        elif field_type == "string_list":
            _expect_keys(
                field,
                {"name", "type", "max_items", "item_max_length"},
                code="PLAN_RESPONSE_INVALID",
            )
            properties[name] = {
                "type": "array",
                "items": {
                    "type": "string",
                    "maxLength": _schema_int(
                        field["item_max_length"], maximum=MAX_LOCAL_SCHEMA_CHARS
                    ),
                },
                "maxItems": _schema_int(field["max_items"], maximum=MAX_LOCAL_SCHEMA_ITEMS),
            }
        elif field_type == "number_list":
            _expect_keys(field, {"name", "type", "max_items"}, code="PLAN_RESPONSE_INVALID")
            properties[name] = {
                "type": "array",
                "items": {"type": "number"},
                "maxItems": _schema_int(field["max_items"], maximum=MAX_LOCAL_SCHEMA_ITEMS),
            }
        else:
            raise _fail("PLAN_RESPONSE_INVALID")
        required.append(name)
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _lower_output_descriptors(value: object) -> dict[str, str]:
    if type(value) is not list or not 1 <= len(value) <= MAX_OUTPUTS:
        raise _fail("PLAN_RESPONSE_INVALID")
    outputs: dict[str, str] = {}
    for item in value:
        if not _is_wire_dict(item):
            raise _fail("PLAN_RESPONSE_INVALID")
        _expect_keys(item, {"key", "expression"}, code="PLAN_RESPONSE_INVALID")
        key = _validate_name(item["key"], code="PLAN_OUTPUT_INVALID")
        if key in outputs:
            raise _fail("PLAN_OUTPUT_INVALID")
        expression = _validate_text(
            item["expression"], code="PLAN_EXPR_INVALID", maximum=MAX_EXPR_CHARS
        )
        outputs[key] = expression
    return outputs


def _validate_template_text(text: object, output_names: set[str]) -> str:
    if type(text) is not str:
        raise _fail("PLAN_TEMPLATE_INVALID")
    if not text:
        raise _fail("PLAN_TEMPLATE_INVALID")
    try:
        encoded_length = len(text.encode("utf-8"))
    except UnicodeError:
        raise _fail("PLAN_TEMPLATE_INVALID") from None
    if encoded_length > MAX_TEMPLATE_BYTES:
        raise _fail("PLAN_TEMPLATE_LIMIT")
    slots = 0
    position = 0
    while position < len(text):
        character = text[position]
        if character == "{":
            if not text.startswith("{{", position):
                raise _fail("PLAN_TEMPLATE_INVALID")
            end = text.find("}}", position + 2)
            if end < 0:
                raise _fail("PLAN_TEMPLATE_INVALID")
            name = text[position + 2 : end]
            if _SLOT_NAME.fullmatch(name) is None:
                raise _fail("PLAN_TEMPLATE_INVALID")
            if name not in output_names:
                raise _fail("PLAN_TEMPLATE_INVALID")
            slots += 1
            if slots > MAX_TEMPLATE_SLOTS:
                raise _fail("PLAN_TEMPLATE_LIMIT")
            position = end + 2
            continue
        if character == "}":
            raise _fail("PLAN_TEMPLATE_INVALID")
        position += 1
    return text


def _validate_render(
    raw: object, output_names: set[str], numeric_outputs: set[str]
) -> ComposeRender:
    if not _is_wire_dict(raw):
        raise _fail("PLAN_TEMPLATE_INVALID")
    kind = raw.get("kind")
    if kind == "template":
        _expect_keys(raw, {"kind", "text"}, code="PLAN_TEMPLATE_INVALID")
        return TemplateRender("template", _validate_template_text(raw["text"], output_names))
    if kind == "sign":
        _expect_keys(
            raw,
            {"kind", "selector", "negative", "zero", "positive"},
            code="PLAN_TEMPLATE_INVALID",
        )
        selector = _validate_name(raw["selector"], code="PLAN_TEMPLATE_INVALID")
        if selector not in numeric_outputs:
            raise _fail("PLAN_TEMPLATE_INVALID")
        negative = _validate_template_text(raw["negative"], output_names)
        zero = _validate_template_text(raw["zero"], output_names)
        positive = _validate_template_text(raw["positive"], output_names)
        total = sum(len(item.encode("utf-8")) for item in (negative, zero, positive))
        if total > MAX_RENDER_BYTES:
            raise _fail("PLAN_TEMPLATE_LIMIT")
        return SignRender("sign", selector, negative, zero, positive)
    raise _fail("PLAN_TEMPLATE_INVALID")


def lower_planner_plan(raw: object) -> dict[str, Any]:
    if not _is_wire_dict(raw):
        raise _fail("PLAN_RESPONSE_INVALID")
    _expect_keys(raw, {"plan"}, code="PLAN_RESPONSE_INVALID")
    plan = raw["plan"]
    if not _is_wire_dict(plan):
        raise _fail("PLAN_RESPONSE_INVALID")
    version = plan.get("version")
    kind = plan.get("kind")
    if type(version) is not int or version != 3 or type(kind) is not str:
        raise _fail("PLAN_RESPONSE_INVALID")
    if kind == "compute":
        _expect_keys(plan, {"version", "kind", "inputs", "outputs"}, code="PLAN_RESPONSE_INVALID")
        outputs = _lower_output_descriptors(plan["outputs"])
        return {"version": 2, "kind": "compute", "inputs": plan["inputs"], "outputs": outputs}
    if kind == "compose":
        _expect_keys(
            plan,
            {"version", "kind", "inputs", "outputs", "render"},
            code="PLAN_RESPONSE_INVALID",
        )
        outputs = _lower_output_descriptors(plan["outputs"])
        names = set(outputs)
        render = plan["render"]
        if not _is_wire_dict(render):
            raise _fail("PLAN_TEMPLATE_INVALID")
        if render.get("kind") == "sign":
            selector = _validate_name(render.get("selector"), code="PLAN_TEMPLATE_INVALID")
            if selector not in names:
                raise _fail("PLAN_TEMPLATE_INVALID")
        normalized_render: dict[str, Any]
        if render.get("kind") == "template":
            _expect_keys(render, {"kind", "text"}, code="PLAN_TEMPLATE_INVALID")
            normalized_render = {
                "kind": "template",
                "text": _validate_template_text(render["text"], names),
            }
        elif render.get("kind") == "sign":
            _expect_keys(
                render,
                {"kind", "selector", "negative", "zero", "positive"},
                code="PLAN_TEMPLATE_INVALID",
            )
            normalized_render = {
                "kind": "sign",
                "selector": _validate_name(render["selector"], code="PLAN_TEMPLATE_INVALID"),
                "negative": _validate_template_text(render["negative"], names),
                "zero": _validate_template_text(render["zero"], names),
                "positive": _validate_template_text(render["positive"], names),
            }
            total = sum(
                len(normalized_render[key].encode("utf-8"))
                for key in ("negative", "zero", "positive")
            )
            if total > MAX_RENDER_BYTES:
                raise _fail("PLAN_TEMPLATE_LIMIT")
        else:
            raise _fail("PLAN_TEMPLATE_INVALID")
        return {
            "version": 3,
            "kind": "compose",
            "inputs": plan["inputs"],
            "outputs": outputs,
            "render": normalized_render,
        }
    if kind == "local_answer":
        if plan.get("answer_contract") == "unconstrained":
            _expect_keys(
                plan,
                {"version", "kind", "instruction", "output_format", "answer_contract"},
                code="PLAN_RESPONSE_INVALID",
            )
            instruction = _validate_text(
                plan["instruction"], code="LOCAL_PLAN_INVALID", maximum=MAX_INSTRUCTION_LENGTH
            )
            output_format = plan["output_format"]
            if output_format != "json":
                raise _fail("LOCAL_PLAN_INVALID")
            return {
                "version": 2,
                "kind": "local_answer",
                "instruction": instruction,
                "output_format": output_format,
            }
        _expect_keys(
            plan,
            {"version", "kind", "instruction", "output_format", "answer_fields"},
            code="PLAN_RESPONSE_INVALID",
        )
        instruction = _validate_text(
            plan["instruction"], code="LOCAL_PLAN_INVALID", maximum=MAX_INSTRUCTION_LENGTH
        )
        output_format = plan["output_format"]
        if type(output_format) is not str or output_format not in {"json", "text"}:
            raise _fail("LOCAL_PLAN_INVALID")
        output_schema = _lower_answer_fields(plan["answer_fields"], output_format)
        return {
            "version": 3,
            "kind": "local_answer",
            "instruction": instruction,
            "output_format": output_format,
            "output_schema": output_schema,
        }
    raise _fail("PLAN_KIND_INVALID")


def _fail(code: str) -> PrivacyError:
    return PrivacyError(code)


def _is_wire_dict(value: object) -> TypeGuard[dict[str, Any]]:
    return type(value) is dict


def _validate_text(value: object, *, code: str, maximum: int, allow_blank: bool = False) -> str:
    if type(value) is not str:
        raise _fail(code)
    if len(value) > maximum or (not allow_blank and not value.strip()):
        raise _fail(code)
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise _fail(code) from None
    return value


def _validate_name(value: object, *, code: str) -> str:
    name = _validate_text(value, code=code, maximum=MAX_NAME_LENGTH)
    if name[0] not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_":
        raise _fail(code)
    if any(char not in _NAME_CHARS for char in name):
        raise _fail(code)
    return name


def _decimal_from_value(value: object, *, code: str = "PLAN_NUMBER_INVALID") -> Decimal:
    if type(value) is Decimal:
        result = value
    elif type(value) is int:
        try:
            result = Decimal(value)
        except (InvalidOperation, ValueError, OverflowError):
            raise _fail(code) from None
    elif type(value) is float:
        if not math.isfinite(value):
            raise _fail(code)
        try:
            result = Decimal(repr(value))
        except (InvalidOperation, ValueError):
            raise _fail(code) from None
    else:
        raise _fail(code)

    if not result.is_finite():
        raise _fail(code)
    digits = len(result.as_tuple().digits)
    if digits > MAX_NUMBER_DIGITS:
        raise _fail(code)
    exponent = result.as_tuple().exponent
    if type(exponent) is not int or abs(exponent) > MAX_DECIMAL_EXPONENT:
        raise _fail(code)
    if result != 0 and abs(result.adjusted()) > MAX_ABS_ADJUSTED_EXPONENT:
        raise _fail(code)
    return result


def _expect_keys(value: dict[str, Any], expected: set[str], *, code: str) -> None:
    if any(type(key) is not str for key in value) or set(value) != expected:
        raise _fail(code)


def _parse_inputs(raw: object) -> tuple[PlanInput, ...]:
    if type(raw) is not list or len(raw) > MAX_INPUTS:
        raise _fail("PLAN_INPUT_INVALID")
    parsed: list[PlanInput] = []
    names: set[str] = set()
    for item in raw:
        if not _is_wire_dict(item):
            raise _fail("PLAN_INPUT_INVALID")
        _expect_keys(item, {"name", "type", "instruction"}, code="PLAN_INPUT_INVALID")
        name = _validate_name(item["name"], code="PLAN_INPUT_INVALID")
        if name in names:
            raise _fail("PLAN_INPUT_INVALID")
        kind = item["type"]
        if type(kind) is not str or kind not in {"number", "numbers", "text"}:
            raise _fail("PLAN_INPUT_INVALID")
        instruction = _validate_text(
            item["instruction"], code="PLAN_INPUT_INVALID", maximum=MAX_INSTRUCTION_LENGTH
        )
        names.add(name)
        parsed.append(PlanInput(name, kind, instruction))  # type: ignore[arg-type]
    return tuple(parsed)


def _parse_expr(
    raw: object,
    input_types: Mapping[str, InputType],
    *,
    depth: int,
    state: dict[str, Any],
    active: set[int],
) -> tuple[Expr, ExprType]:
    if depth > MAX_AST_DEPTH:
        raise _fail("PLAN_EXPR_LIMIT")
    state["nodes"] += 1
    if state["nodes"] > MAX_AST_NODES:
        raise _fail("PLAN_EXPR_LIMIT")

    if type(raw) in {int, float, Decimal}:
        return Expr("const", value=_decimal_from_value(raw)), "number"
    if not _is_wire_dict(raw):
        raise _fail("PLAN_EXPR_INVALID")
    identity = id(raw)
    if identity in active:
        raise _fail("PLAN_EXPR_LIMIT")
    active.add(identity)
    try:
        op = raw.get("op")
        if type(op) is not str or op not in _ALL_OPS:
            raise _fail("PLAN_UNKNOWN_OP")
        if op == "const":
            _expect_keys(raw, {"op", "value"}, code="PLAN_EXPR_INVALID")
            return Expr("const", value=_decimal_from_value(raw["value"])), "number"
        if op in {"ref", "var", "text_ref"}:
            _expect_keys(raw, {"op", "name"}, code="PLAN_EXPR_INVALID")
            name = _validate_name(raw["name"], code="PLAN_EXPR_INVALID")
            expected = {"ref": "number", "var": "numbers", "text_ref": "text"}[op]
            if input_types.get(name) != expected:
                raise _fail("PLAN_REFERENCE_INVALID")
            result_type: ExprType = {
                "ref": "number",
                "var": "numbers",
                "text_ref": "text",
            }[op]  # type: ignore[assignment]
            return Expr(op, name=name), result_type  # type: ignore[arg-type]
        if op in _AGGREGATE_OPS:
            _expect_keys(raw, {"op", "arg"}, code="PLAN_EXPR_INVALID")
            argument, argument_type = _parse_expr(
                raw["arg"], input_types, depth=depth + 1, state=state, active=active
            )
            if argument_type != "numbers":
                raise _fail("PLAN_TYPE_INVALID")
            return Expr(op, arg=argument), "number"  # type: ignore[arg-type]
        if op == "round":
            _expect_keys(raw, {"op", "arg", "digits"}, code="PLAN_EXPR_INVALID")
            digits = raw["digits"]
            if type(digits) is not int or not 0 <= digits <= MAX_ROUND_DIGITS:
                raise _fail("PLAN_EXPR_INVALID")
            argument, argument_type = _parse_expr(
                raw["arg"], input_types, depth=depth + 1, state=state, active=active
            )
            if argument_type != "number":
                raise _fail("PLAN_TYPE_INVALID")
            return Expr("round", arg=argument, digits=digits), "number"
        if op == "dot":
            _expect_keys(raw, {"op", "left", "right"}, code="PLAN_EXPR_INVALID")
            left, left_type = _parse_expr(
                raw["left"], input_types, depth=depth + 1, state=state, active=active
            )
            right, right_type = _parse_expr(
                raw["right"], input_types, depth=depth + 1, state=state, active=active
            )
            if left_type != "numbers" or right_type != "numbers":
                raise _fail("PLAN_TYPE_INVALID")
            return Expr("dot", left=left, right=right), "number"
        _expect_keys(raw, {"op", "left", "right"}, code="PLAN_EXPR_INVALID")
        left, left_type = _parse_expr(
            raw["left"], input_types, depth=depth + 1, state=state, active=active
        )
        right, right_type = _parse_expr(
            raw["right"], input_types, depth=depth + 1, state=state, active=active
        )
        if left_type != "number" or right_type != "number":
            raise _fail("PLAN_TYPE_INVALID")
        return Expr(op, left=left, right=right), "number"  # type: ignore[arg-type]
    finally:
        active.remove(identity)


def _validate_output_field_name(value: object) -> str:
    name = _validate_text(value, code="LOCAL_SCHEMA_INVALID", maximum=MAX_NAME_LENGTH)
    if any(ord(char) < 0x20 for char in name):
        raise _fail("LOCAL_SCHEMA_INVALID")
    return name


def _parse_output_field_schema(raw: object) -> dict[str, Any]:
    if not _is_wire_dict(raw):
        raise _fail("LOCAL_SCHEMA_INVALID")
    field_type = raw.get("type")
    if type(field_type) is not str:
        raise _fail("LOCAL_SCHEMA_INVALID")
    scalar_types = {"string", "number", "integer", "boolean"}
    if field_type in scalar_types:
        if field_type == "string":
            _expect_keys(raw, {"type", "maxLength"}, code="LOCAL_SCHEMA_INVALID")
            maximum = raw["maxLength"]
            if type(maximum) is not int or not 1 <= maximum <= MAX_LOCAL_SCHEMA_CHARS:
                raise _fail("LOCAL_SCHEMA_INVALID")
            return {"type": "string", "maxLength": maximum}
        _expect_keys(raw, {"type"}, code="LOCAL_SCHEMA_INVALID")
        return {"type": field_type}
    if field_type != "array":
        raise _fail("LOCAL_SCHEMA_INVALID")
    _expect_keys(raw, {"type", "items", "maxItems"}, code="LOCAL_SCHEMA_INVALID")
    maximum_items = raw["maxItems"]
    if type(maximum_items) is not int or not 1 <= maximum_items <= MAX_LOCAL_SCHEMA_ITEMS:
        raise _fail("LOCAL_SCHEMA_INVALID")
    items = raw["items"]
    if not _is_wire_dict(items):
        raise _fail("LOCAL_SCHEMA_INVALID")
    item_type = items.get("type")
    if type(item_type) is not str or item_type not in scalar_types:
        raise _fail("LOCAL_SCHEMA_INVALID")
    if item_type == "string":
        _expect_keys(items, {"type", "maxLength"}, code="LOCAL_SCHEMA_INVALID")
        item_maximum = items["maxLength"]
        if type(item_maximum) is not int or not 1 <= item_maximum <= MAX_LOCAL_SCHEMA_CHARS:
            raise _fail("LOCAL_SCHEMA_INVALID")
        clean_items = {"type": "string", "maxLength": item_maximum}
    else:
        _expect_keys(items, {"type"}, code="LOCAL_SCHEMA_INVALID")
        clean_items = {"type": item_type}
    return {"type": "array", "items": clean_items, "maxItems": maximum_items}


def _parse_output_schema(raw: object, output_format: str) -> dict[str, Any]:
    if not _is_wire_dict(raw):
        raise _fail("LOCAL_SCHEMA_INVALID")
    if output_format == "text":
        _expect_keys(raw, {"type", "maxLength"}, code="LOCAL_SCHEMA_INVALID")
        maximum = raw["maxLength"]
        if raw["type"] != "string" or type(maximum) is not int:
            raise _fail("LOCAL_SCHEMA_INVALID")
        if not 1 <= maximum <= MAX_LOCAL_SCHEMA_CHARS:
            raise _fail("LOCAL_SCHEMA_INVALID")
        return {"type": "string", "maxLength": maximum}

    _expect_keys(
        raw,
        {"type", "properties", "required", "additionalProperties"},
        code="LOCAL_SCHEMA_INVALID",
    )
    if raw["type"] != "object" or raw["additionalProperties"] is not False:
        raise _fail("LOCAL_SCHEMA_INVALID")
    properties = raw["properties"]
    if not _is_wire_dict(properties) or not 1 <= len(properties) <= MAX_LOCAL_SCHEMA_FIELDS:
        raise _fail("LOCAL_SCHEMA_INVALID")
    clean_properties: dict[str, Any] = {}
    for key, value in properties.items():
        clean_properties[_validate_output_field_name(key)] = _parse_output_field_schema(value)
    required = raw["required"]
    if type(required) is not list or not 1 <= len(required) <= MAX_LOCAL_SCHEMA_REQUIRED:
        raise _fail("LOCAL_SCHEMA_INVALID")
    if any(type(key) is not str for key in required) or len(set(required)) != len(required):
        raise _fail("LOCAL_SCHEMA_INVALID")
    if set(required) != set(clean_properties):
        raise _fail("LOCAL_SCHEMA_INVALID")
    return {
        "type": "object",
        "properties": clean_properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _validate_local_output_value(value: object, schema: dict[str, Any]) -> None:
    field_type = schema.get("type")
    if field_type == "string":
        if type(value) is not str or len(value) > schema.get("maxLength", 0):
            raise _fail("PRIVATE_OUTPUT_INVALID")
        return
    if field_type == "boolean":
        if type(value) is not bool:
            raise _fail("PRIVATE_OUTPUT_INVALID")
        return
    if field_type == "integer":
        if type(value) is not int:
            raise _fail("PRIVATE_OUTPUT_INVALID")
        _decimal_from_value(value, code="PRIVATE_OUTPUT_INVALID")
        return
    if field_type == "number":
        if type(value) not in {int, Decimal}:
            raise _fail("PRIVATE_OUTPUT_INVALID")
        _decimal_from_value(value, code="PRIVATE_OUTPUT_INVALID")
        return
    if field_type == "array":
        maximum = schema.get("maxItems")
        if type(value) is not list or type(maximum) is not int or len(value) > maximum:
            raise _fail("PRIVATE_OUTPUT_INVALID")
        item_schema = schema.get("items")
        if type(item_schema) is not dict:
            raise _fail("PRIVATE_OUTPUT_INVALID")
        for item in value:
            _validate_local_output_value(item, item_schema)
        return
    raise _fail("PRIVATE_OUTPUT_INVALID")


def _strict_local_json(raw: str) -> object:
    def unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result

    def constant(_: str) -> None:
        raise ValueError("non-finite")

    try:
        result = json.loads(
            raw,
            object_pairs_hook=unique,
            parse_int=int,
            parse_float=Decimal,
            parse_constant=constant,
        )
        pending = [result]
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                item.encode("utf-8")
            elif isinstance(item, dict):
                pending.extend(item.keys())
                pending.extend(item.values())
            elif isinstance(item, list):
                pending.extend(item)
            elif type(item) is Decimal and not item.is_finite():
                raise ValueError("non-finite")
        return result
    except (ValueError, TypeError, UnicodeError, RecursionError, ArithmeticError):
        raise _fail("PRIVATE_OUTPUT_INVALID") from None


def validate_local_answer_result(plan: LocalAnswerPlan, raw: object) -> str:
    if type(plan) is not LocalAnswerPlan or plan.version != 3:
        raise _fail("LOCAL_SCHEMA_REQUIRED")
    if type(raw) is not str:
        raise _fail("PRIVATE_OUTPUT_INVALID")
    try:
        if len(raw.encode("utf-8")) > MAX_OUTPUT_BYTES:
            raise _fail("PRIVATE_OUTPUT_INVALID")
    except UnicodeError:
        raise _fail("PRIVATE_OUTPUT_INVALID") from None
    schema = plan.output_schema
    if type(schema) is not dict:
        raise _fail("LOCAL_SCHEMA_REQUIRED")
    if plan.output_format == "text":
        if schema.get("type") != "string" or len(raw) > schema.get("maxLength", 0):
            raise _fail("PRIVATE_OUTPUT_INVALID")
        return raw
    if plan.output_format != "json":
        raise _fail("PRIVATE_OUTPUT_INVALID")
    parsed = _strict_local_json(raw)
    if type(parsed) is not dict or schema.get("type") != "object":
        raise _fail("PRIVATE_OUTPUT_INVALID")
    properties = schema.get("properties")
    required = schema.get("required")
    if type(properties) is not dict or type(required) is not list:
        raise _fail("PRIVATE_OUTPUT_INVALID")
    if set(parsed) != set(properties) or set(required) != set(properties):
        raise _fail("PRIVATE_OUTPUT_INVALID")
    for name, field_schema in properties.items():
        if type(name) is not str or type(field_schema) is not dict:
            raise _fail("PRIVATE_OUTPUT_INVALID")
        _validate_local_output_value(parsed[name], field_schema)
    return raw


def _parse_compute_plan_fields(
    raw: dict[str, Any], *, version: int
) -> tuple[ComputePlan, dict[str, ExprType]]:
    inputs = _parse_inputs(raw["inputs"])
    outputs_raw = raw["outputs"]
    if not _is_wire_dict(outputs_raw) or not outputs_raw or len(outputs_raw) > MAX_OUTPUTS:
        raise _fail("PLAN_OUTPUT_INVALID")
    input_types = {item.name: item.type for item in inputs}
    state = {"nodes": 0}
    outputs: dict[str, Expr] = {}
    output_types: dict[str, ExprType] = {}
    for key, raw_expr in outputs_raw.items():
        output_key = _validate_name(key, code="PLAN_OUTPUT_INVALID")
        if version == 2:
            if type(raw_expr) is not str:
                raise _fail("PLAN_EXPR_INVALID")
            raw_expr = parse_expression(raw_expr, dict(input_types))
        expression, expression_type = _parse_expr(
            raw_expr, input_types, depth=1, state=state, active=set()
        )
        if expression_type not in {"number", "text"}:
            raise _fail("PLAN_TYPE_INVALID")
        outputs[output_key] = expression
        output_types[output_key] = expression_type
    return ComputePlan(1, "compute", inputs, outputs), output_types


def validate_plan(raw: object) -> TypedPlan:
    if not _is_wire_dict(raw):
        raise _fail("PLAN_INVALID")
    version = raw.get("version")
    kind = raw.get("kind")
    if type(version) is not int or version not in (1, 2, 3) or type(kind) is not str:
        raise _fail("PLAN_INVALID")
    if kind == "compute":
        if version not in (1, 2):
            raise _fail("PLAN_INVALID")
        _expect_keys(raw, {"version", "kind", "inputs", "outputs"}, code="PLAN_INVALID")
        compute, _ = _parse_compute_plan_fields(raw, version=version)
        return compute
    if kind == "compose":
        if version != 3:
            raise _fail("PLAN_INVALID")
        _expect_keys(
            raw,
            {"version", "kind", "inputs", "outputs", "render"},
            code="PLAN_INVALID",
        )
        compute_candidate, output_types = _parse_compute_plan_fields(
            {"inputs": raw["inputs"], "outputs": raw["outputs"]}, version=2
        )
        numeric_outputs = {
            name for name, result_type in output_types.items() if result_type == "number"
        }
        render = _validate_render(raw["render"], set(compute_candidate.outputs), numeric_outputs)
        return ComposePlan(3, "compose", compute_candidate, render)
    if kind == "local_answer":
        if version in (1, 2):
            _expect_keys(
                raw,
                {"version", "kind", "instruction", "output_format"},
                code="LOCAL_PLAN_INVALID",
            )
        elif version == 3:
            _expect_keys(
                raw,
                {"version", "kind", "instruction", "output_format", "output_schema"},
                code="LOCAL_PLAN_INVALID",
            )
        else:  # pragma: no cover - the outer version check catches this
            raise _fail("PLAN_INVALID")
        instruction = _validate_text(
            raw["instruction"], code="LOCAL_PLAN_INVALID", maximum=MAX_INSTRUCTION_LENGTH
        )
        output_format = raw["output_format"]
        if output_format == "json":
            typed_format: OutputFormat = "json"
        elif output_format == "text":
            typed_format = "text"
        else:
            raise _fail("LOCAL_PLAN_INVALID")
        output_schema = (
            _parse_output_schema(raw["output_schema"], typed_format) if version == 3 else None
        )
        typed_version: Literal[1, 3] = 3 if version == 3 else 1
        return LocalAnswerPlan(
            typed_version,
            "local_answer",
            instruction,
            typed_format,
            output_schema,
        )
    raise _fail("PLAN_KIND_INVALID")


def _validate_local_inputs(
    plan: ComputePlan, values: object
) -> dict[str, Fraction | tuple[Fraction, ...] | str]:
    if type(values) is not dict:
        raise _fail("LOCAL_INPUT_INVALID")
    if any(type(key) is not str for key in values):
        raise _fail("LOCAL_INPUT_INVALID")
    expected = {item.name: item.type for item in plan.inputs}
    if set(values) != set(expected):
        raise _fail("LOCAL_INPUT_INVALID")
    converted: dict[str, Fraction | tuple[Fraction, ...] | str] = {}
    for name, expected_type in expected.items():
        value = values[name]
        if expected_type == "number":
            converted[name] = _fraction_from_decimal(
                _decimal_from_value(value, code="LOCAL_INPUT_INVALID")
            )
            continue
        if expected_type == "text":
            converted[name] = _validate_text(
                value, code="LOCAL_INPUT_INVALID", maximum=MAX_INSTRUCTION_LENGTH
            )
            continue
        if type(value) not in {list, tuple} or len(value) > MAX_VECTOR_LENGTH:
            raise _fail("LOCAL_INPUT_INVALID")
        numbers: list[Fraction] = []
        for item in value:
            numbers.append(
                _fraction_from_decimal(_decimal_from_value(item, code="LOCAL_INPUT_INVALID"))
            )
        converted[name] = tuple(numbers)
    return converted


def _bounded_fraction(value: Fraction) -> Fraction:
    if type(value) is not Fraction or value.denominator <= 0:
        raise _fail("PLAN_NUMERIC_INVALID")
    numerator = abs(value.numerator)
    denominator = value.denominator
    if numerator.bit_length() > MAX_FRACTION_BITS or denominator.bit_length() > MAX_FRACTION_BITS:
        raise _fail("PLAN_NUMERIC_INVALID")
    if len(str(numerator)) > MAX_FRACTION_DIGITS or len(str(denominator)) > MAX_FRACTION_DIGITS:
        raise _fail("PLAN_NUMERIC_INVALID")
    if numerator:
        if numerator >= denominator * (10 ** (MAX_ABS_ADJUSTED_EXPONENT + 1)):
            raise _fail("PLAN_NUMERIC_INVALID")
        if numerator * (10**MAX_ABS_ADJUSTED_EXPONENT) < denominator:
            raise _fail("PLAN_NUMERIC_INVALID")
    return value


def _fraction_from_decimal(value: Decimal) -> Fraction:
    try:
        return _bounded_fraction(Fraction(value))
    except (ArithmeticError, ValueError, OverflowError):
        raise _fail("PLAN_NUMERIC_INVALID") from None


def _binary(op: str, left: Fraction, right: Fraction) -> Fraction:
    try:
        if op == "add":
            result = left + right
        elif op == "sub":
            result = left - right
        elif op == "mul":
            result = left * right
        elif op == "div":
            if right == 0:
                raise _fail("PLAN_DIVISION_ZERO")
            result = left / right
        else:  # pragma: no cover - validation makes this unreachable
            raise _fail("PLAN_UNKNOWN_OP")
    except PrivacyError:
        raise
    except (ArithmeticError, ValueError, OverflowError):
        raise _fail("PLAN_NUMERIC_INVALID") from None
    return _bounded_fraction(result)


def _round_fraction(value: Fraction, digits: int) -> Fraction:
    scale = 10**digits
    numerator = abs(value.numerator) * scale
    quotient, remainder = divmod(numerator, value.denominator)
    if remainder * 2 > value.denominator or (
        remainder * 2 == value.denominator and quotient % 2 == 1
    ):
        quotient += 1
    if value.numerator < 0:
        quotient = -quotient
    return _bounded_fraction(Fraction(quotient, scale))


def _fraction_to_decimal(value: Fraction) -> Decimal:
    _bounded_fraction(value)
    denominator = value.denominator
    twos = fives = 0
    while denominator % 2 == 0:
        denominator //= 2
        twos += 1
    while denominator % 5 == 0:
        denominator //= 5
        fives += 1
    if denominator == 1:
        scale = max(twos, fives)
        coefficient = abs(value.numerator)
        coefficient *= 2 ** (scale - twos)
        coefficient *= 5 ** (scale - fives)
        exponent = -scale
        if coefficient == 0:
            return Decimal(0)
        while coefficient % 10 == 0:
            coefficient //= 10
            exponent += 1
        digits = str(coefficient)
        if len(digits) > MAX_NUMBER_DIGITS:
            raise _fail("PLAN_NUMERIC_INVALID")
        sign = 1 if value.numerator < 0 else 0
        return _bounded_result(Decimal((sign, tuple(int(digit) for digit in digits), exponent)))
    try:
        with localcontext() as context:
            context.prec = MAX_NUMBER_DIGITS
            context.rounding = ROUND_HALF_EVEN
            result = Decimal(value.numerator) / Decimal(value.denominator)
    except (InvalidOperation, ArithmeticError, ValueError, OverflowError):
        raise _fail("PLAN_NUMERIC_INVALID") from None
    return _bounded_result(result)


def _bounded_result(value: Decimal, *, max_digits: int = MAX_NUMBER_DIGITS) -> Decimal:
    if not value.is_finite():
        raise _fail("PLAN_NUMERIC_INVALID")
    if len(value.as_tuple().digits) > max_digits:
        raise _fail("PLAN_NUMERIC_INVALID")
    exponent = value.as_tuple().exponent
    if type(exponent) is not int or abs(exponent) > MAX_DECIMAL_EXPONENT:
        raise _fail("PLAN_NUMERIC_INVALID")
    if value != 0 and abs(value.adjusted()) > MAX_ABS_ADJUSTED_EXPONENT:
        raise _fail("PLAN_NUMERIC_INVALID")
    return value


def _evaluate(
    expression: Expr, values: Mapping[str, Fraction | tuple[Fraction, ...] | str]
) -> Fraction | tuple[Fraction, ...] | str:
    op = expression.op
    if op == "const":
        if type(expression.value) is not Decimal:
            raise _fail("PLAN_INVALID")
        return _fraction_from_decimal(expression.value)
    if op == "ref":
        assert expression.name is not None
        value = values[expression.name]
        assert type(value) is Fraction
        return value
    if op == "var":
        assert expression.name is not None
        value = values[expression.name]
        assert type(value) is tuple
        return value
    if op == "text_ref":
        assert expression.name is not None
        value = values[expression.name]
        assert type(value) is str
        return value
    if op in _AGGREGATE_OPS:
        assert expression.arg is not None
        raw = _evaluate(expression.arg, values)
        assert type(raw) is tuple
        if op == "sum":
            result = Fraction(0)
            for item in raw:
                result = _binary("add", result, item)
            return result
        if not raw:
            raise _fail("PLAN_EMPTY_AGGREGATE")
        if op == "mean":
            return _binary("div", _evaluate_sum(raw), Fraction(len(raw)))
        if op == "min":
            return min(raw)
        return max(raw)
    if op == "dot":
        assert expression.left is not None and expression.right is not None
        left = _evaluate(expression.left, values)
        right = _evaluate(expression.right, values)
        if type(left) is not tuple or type(right) is not tuple:
            raise _fail("PLAN_INVALID")
        if not left or not right:
            raise _fail("PLAN_VECTOR_EMPTY")
        if len(left) != len(right):
            raise _fail("PLAN_VECTOR_LENGTH_MISMATCH")
        result = Fraction(0)
        for left_item, right_item in zip(left, right, strict=True):
            result = _binary("add", result, _binary("mul", left_item, right_item))
        return result
    if op == "round":
        assert expression.arg is not None and expression.digits is not None
        value = _evaluate(expression.arg, values)
        assert type(value) is Fraction
        return _round_fraction(value, expression.digits)
    assert expression.left is not None and expression.right is not None
    left = _evaluate(expression.left, values)
    right = _evaluate(expression.right, values)
    assert type(left) is Fraction and type(right) is Fraction
    return _binary(op, left, right)


def _evaluate_sum(values: tuple[Fraction, ...]) -> Fraction:
    result = Fraction(0)
    for value in values:
        result = _binary("add", result, value)
    return result


def _check_typed_compute_boundary(plan: ComputePlan) -> None:
    if type(plan) is not ComputePlan:
        raise _fail("PLAN_INVALID")
    if type(plan.version) is not int or plan.version != 1 or plan.kind != "compute":
        raise _fail("PLAN_INVALID")
    if type(plan.inputs) is not tuple or len(plan.inputs) > MAX_INPUTS:
        raise _fail("PLAN_INVALID")
    if type(plan.outputs) is not dict or not plan.outputs or len(plan.outputs) > MAX_OUTPUTS:
        raise _fail("PLAN_INVALID")
    if any(type(key) is not str for key in plan.outputs):
        raise _fail("PLAN_INVALID")


def _check_typed_compose_boundary(plan: ComposePlan) -> None:
    if type(plan) is not ComposePlan:
        raise _fail("PLAN_INVALID")
    if type(plan.version) is not int or plan.version != 3 or plan.kind != "compose":
        raise _fail("PLAN_INVALID")
    compute = plan.compute
    _check_typed_compute_boundary(compute)
    input_names: set[str] = set()
    for item in compute.inputs:
        if type(item) is not PlanInput or type(item.name) is not str:
            raise _fail("PLAN_INVALID")
        if item.name in input_names or item.type not in {"number", "numbers", "text"}:
            raise _fail("PLAN_INVALID")
        input_names.add(item.name)
    numeric_outputs: set[str] = set()
    for name, expression in compute.outputs.items():
        if type(name) is not str or _validate_name(name, code="PLAN_INVALID") != name:
            raise _fail("PLAN_INVALID")
        if type(expression) is not Expr or expression.op not in _ALL_OPS:
            raise _fail("PLAN_INVALID")
        if expression.op == "text_ref":
            result_type = "text"
        elif expression.op == "const":
            if type(expression.value) is not Decimal:
                raise _fail("PLAN_INVALID")
            _decimal_from_value(expression.value, code="PLAN_INVALID")
            result_type = "number"
        elif expression.op in {
            "ref",
            "sum",
            "mean",
            "min",
            "max",
            "add",
            "sub",
            "mul",
            "div",
            "round",
            "dot",
        }:
            result_type = "number"
        else:
            raise _fail("PLAN_INVALID")
        if result_type == "number":
            numeric_outputs.add(name)
    render = plan.render
    if type(render) is TemplateRender and render.kind == "template":
        _validate_template_text(render.text, set(compute.outputs))
        return
    if type(render) is SignRender and render.kind == "sign":
        if render.selector not in numeric_outputs:
            raise _fail("PLAN_TEMPLATE_INVALID")
        for text in (render.negative, render.zero, render.positive):
            _validate_template_text(text, set(compute.outputs))
        total = sum(
            len(text.encode("utf-8")) for text in (render.negative, render.zero, render.positive)
        )
        if total > MAX_RENDER_BYTES:
            raise _fail("PLAN_TEMPLATE_LIMIT")
        return
    raise _fail("PLAN_TEMPLATE_INVALID")


def _decimal_result_text(value: Decimal) -> str:
    value = _bounded_result(value)
    if value == 0:
        return "0"
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _compose_scalar_text(value: Decimal | str) -> str:
    if type(value) is str:
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise _fail("PLAN_RESULT_INVALID") from None
        return value
    if type(value) is not Decimal:
        raise _fail("PLAN_RESULT_INVALID")
    return _decimal_result_text(value)


def _render_compose_template(text: str, values: Mapping[str, Decimal | str]) -> str:
    pieces: list[str] = []
    used_bytes = 0

    def append(piece: str) -> None:
        nonlocal used_bytes
        try:
            used_bytes += len(piece.encode("utf-8"))
        except UnicodeError:
            raise _fail("PLAN_RESULT_INVALID") from None
        if used_bytes > MAX_OUTPUT_BYTES:
            raise _fail("PLAN_RESULT_LIMIT")
        pieces.append(piece)

    position = 0
    while position < len(text):
        if text.startswith("{{", position):
            end = text.find("}}", position + 2)
            if end < 0:
                raise _fail("PLAN_TEMPLATE_INVALID")
            name = text[position + 2 : end]
            if name not in values:
                raise _fail("PLAN_TEMPLATE_INVALID")
            append(_compose_scalar_text(values[name]))
            position = end + 2
            continue
        next_slot = text.find("{{", position)
        if next_slot < 0:
            next_slot = len(text)
        append(text[position:next_slot])
        position = next_slot
    return "".join(pieces)


def execute_compute_plan(
    plan: ComputePlan | object, local_inputs: dict[str, object]
) -> dict[str, Decimal | str]:
    if isinstance(plan, LocalAnswerPlan):
        raise _fail("LOCAL_ADAPTER_REQUIRED")
    candidate = validate_plan(plan) if not isinstance(plan, ComputePlan) else plan
    if type(candidate) is not ComputePlan:
        raise _fail("LOCAL_ADAPTER_REQUIRED")
    _check_typed_compute_boundary(candidate)
    try:
        values = _validate_local_inputs(candidate, local_inputs)
        result = {
            key: _evaluate(expression, values) for key, expression in candidate.outputs.items()
        }
    except PrivacyError:
        raise
    except (AssertionError, KeyError, TypeError, AttributeError, IndexError):
        raise _fail("PLAN_INVALID") from None
    checked: dict[str, Decimal | str] = {}
    for key, value in result.items():
        if type(value) is Fraction:
            checked[key] = _fraction_to_decimal(value)
        elif type(value) is str:
            checked[key] = value
        else:
            raise _fail("PLAN_TYPE_INVALID")
    return checked


def execute_compose_plan(plan: ComposePlan | object, local_inputs: dict[str, object]) -> str:
    if isinstance(plan, (ComputePlan, LocalAnswerPlan)):
        raise _fail("LOCAL_ADAPTER_REQUIRED")
    candidate = validate_plan(plan) if not isinstance(plan, ComposePlan) else plan
    if type(candidate) is not ComposePlan:
        raise _fail("LOCAL_ADAPTER_REQUIRED")
    _check_typed_compose_boundary(candidate)
    result = execute_compute_plan(candidate.compute, local_inputs)
    render = candidate.render
    if type(render) is TemplateRender:
        return _render_compose_template(render.text, result)
    selector_value = result.get(render.selector) if type(render) is SignRender else None
    if type(render) is not SignRender or type(selector_value) is not Decimal:
        raise _fail("PLAN_TEMPLATE_INVALID")
    if selector_value < 0:
        selected = render.negative
    elif selector_value > 0:
        selected = render.positive
    else:
        selected = render.zero
    return _render_compose_template(selected, result)


def serialize_result(result: Mapping[str, Decimal | str]) -> str:
    if type(result) is not dict or not result:
        raise _fail("PLAN_RESULT_INVALID")
    pieces: list[str] = []
    for key, value in result.items():
        if type(key) is not str:
            raise _fail("PLAN_RESULT_INVALID")
        _validate_name(key, code="PLAN_RESULT_INVALID")
        if type(value) is str:
            pieces.append(
                f"{json.dumps(key, ensure_ascii=False, separators=(',', ':'))}:"
                f"{json.dumps(value, ensure_ascii=False, separators=(',', ':'))}"
            )
            continue
        if type(value) is not Decimal:
            raise _fail("PLAN_RESULT_INVALID")
        text = _decimal_result_text(value)
        pieces.append(f"{json.dumps(key, ensure_ascii=False, separators=(',', ':'))}:{text}")
    encoded = "{" + ",".join(pieces) + "}"
    if len(encoded.encode("utf-8")) > MAX_OUTPUT_BYTES:
        raise _fail("PLAN_RESULT_LIMIT")
    return encoded


__all__ = [
    "ComposePlan",
    "ComposeRender",
    "ComputePlan",
    "Expr",
    "LocalAnswerPlan",
    "PlanInput",
    "SignRender",
    "TemplateRender",
    "TypedPlan",
    "execute_compose_plan",
    "execute_compute_plan",
    "lower_planner_plan",
    "planner_output_schema",
    "planner_schema_hash",
    "serialize_result",
    "validate_local_answer_result",
    "validate_plan",
]
