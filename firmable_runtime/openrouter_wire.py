"""Provider transport grammar; decoded responses still use the sole host schema.

Anthropic's strict grammar excludes recursive schemas and several validation
keywords. The bounded AST uses a postorder node table only on the wire. Known
value constraints remain local checks; unsupported schema constructs fail before
sending. No JSON text is hidden inside a string-valued response envelope.
"""

import copy

from jsonschema import Draft202012Validator

from .common import Failure, canonical
from .engine import AST_KEYS, MAX_AST_DEPTH, MAX_AST_NODES
from .schema import AST_RULES_SCHEMA, CONFIG_SCHEMA, check_response

WIRE_PROTOCOL = "openrouter-strict-flat-ast-5"
_LOCAL_CONSTRAINTS = {
    "minimum",
    "maximum",
    "minLength",
    "maxLength",
    "maxItems",
    "uniqueItems",
    "maxProperties",
    "pattern",
    "not",
}
_ANNOTATIONS = {"$schema", "$id", "$defs", "definitions", "title"}
_SUPPORTED = {
    "type",
    "properties",
    "required",
    "additionalProperties",
    "items",
    "enum",
    "const",
    "description",
    "anyOf",
    "oneOf",
    "minItems",
    "$ref",
}
_SCALAR = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "string_value", "number_value", "boolean_value"],
    "properties": {
        "kind": {"type": "string", "enum": ["string", "number", "boolean", "null"]},
        "string_value": {"type": "string"},
        "number_value": {"type": "number"},
        "boolean_value": {"type": "boolean"},
    },
}


def _fail(message, path=(), rule="wire_contract"):
    raise Failure("validation_error", message, path=list(path), rule=rule)


def _unsupported(message, path=()):
    raise Failure(
        "unsupported_schema", message, path=list(path), rule="provider_schema"
    )


class OpenRouterWireContract:
    def __init__(self, host_schema):
        try:
            Draft202012Validator.check_schema(host_schema)
        except Exception as exc:
            raise Failure("unsupported_schema", "Invalid host JSON Schema") from exc
        self.host_schema = copy.deepcopy(host_schema)
        self.ast_paths = set()
        self.schema = self._adapt(self.host_schema, self.host_schema, (), ())
        # Anthropic rejects $defs alongside a root anyOf. Keep definitions on
        # a closed object root and put only the structured union under result.
        self.wrapped_root = "anyOf" in self.schema
        if self.wrapped_root:
            self.schema = {
                "type": "object",
                "additionalProperties": False,
                "required": ["result"],
                "properties": {"result": self.schema},
            }
        if self.ast_paths:
            self.schema["$defs"] = {"flat_ast": self._flat_ast_schema()}
        self._check_limits()
        self.instructions = (
            "Return exactly one JSON object matching the strict transport schema. "
            "Do not wrap JSON in a string, Markdown or response_json. "
            "Constraints marked Host constraints are checked after decoding. "
            "Omit source_schema from output configs: it is host-owned metadata "
            "rebuilt from the selected native tables, not a model proposal."
        )
        if self.wrapped_root:
            self.instructions += (
                ' The transport root is {"result": <your structured response object>}. '
                "Put the chosen action object inside result; result is an object, "
                "never JSON text. The host removes this transport-only outer object."
            )
        if self.ast_paths:
            self.instructions += (
                " TRANSPORT OVERRIDE for AST output only: each expression root in "
                "expressions[].ast, execution.transforms or execution time-metadata ast slots is "
                '{"nodes":[...]} in postorder, with its root '
                "LAST. Each node has exactly op, arguments, inputs, enum_entries, "
                "in_values; all five are required. Use [] for unused containers. "
                'arguments is [{"key":"host attribute","value":tagged_scalar},...], '
                "without duplicate keys, and only attributes allowed by that op. "
                "Every tagged_scalar has kind (string/number/boolean/null), "
                "string_value, number_value, boolean_value. Only the slot selected "
                "by kind carries the value; all unused slots MUST be respectively "
                '"", 0, false. For kind=null all slots are unused. '
                "Unlike host nested objects, input/condition/then/else are argument "
                "values tagged as number holding zero-based integer references to "
                "EARLIER nodes (write 0, not 0.0). "
                "join_address uses inputs for its earlier-node reference list. "
                'enum_lookup uses enum_entries [{"key":"raw code","value":"text"}], '
                "with unique keys; in uses in_values for its tagged_scalar list. All other "
                "host attributes (including equals.value) go in arguments. Do not "
                "put op, inputs or values in arguments. All nodes must be reachable "
                "from the last node. Example host "
                '{"op":"trim","input":{"op":"field","name":"label"}} becomes '
                '{"nodes":[{"op":"field","arguments":[{"key":"name","value":'
                '{"kind":"string","string_value":"label","number_value":0,"boolean_value":false}}],'
                '"inputs":[],"enum_entries":[],"in_values":[]},{"op":"trim",'
                '"arguments":[{"key":"input","value":{"kind":"number","string_value":"",'
                '"number_value":0,"boolean_value":false}}],"inputs":[],"enum_entries":[],"in_values":[]}]}. '
                "A when node has condition, then, else argument references. Maximum "
                "128 nodes and expanded depth 16 per transform; enum/in lists have "
                "at most 100 values. Host operator types and requirements still apply. "
                "Mapping execution_pointer still points to the transform "
                "index, never a node. Candidate configs in the input use host form; "
                "For expressions responses, copy each requested slot exactly once "
                "and put that slot's expression in ast; do not infer its slot from array order. "
                "convert ASTs to this wire form in your response. The host reconstructs "
                "the original AST and applies all original schema/compiler guards."
            )

    def _adapt(self, schema, resource, path, refs):
        if not isinstance(schema, dict) or not schema:
            _unsupported("Unconstrained or boolean schemas are unsupported", path)
        if "$id" in schema:
            resource = schema
        unknown = set(schema) - _SUPPORTED - _LOCAL_CONSTRAINTS - _ANNOTATIONS
        if unknown:
            _unsupported(
                "Unsupported schema keywords: " + ", ".join(sorted(unknown)), path
            )
        if "$ref" in schema:
            if set(schema) - {"$ref", "description"}:
                _unsupported(
                    "Schema references with sibling constraints are unsupported", path
                )
            ref = schema["$ref"]
            if (
                resource.get("$id") in {CONFIG_SCHEMA["$id"], AST_RULES_SCHEMA["$id"]}
                and ref == "#/$defs/expression"
            ):
                self.ast_paths.add(path)
                return {"$ref": "#/$defs/flat_ast"}
            if not ref.startswith("#/"):
                _unsupported(
                    "Only local nonrecursive schema references are supported", path
                )
            identity = (id(resource), ref)
            if identity in refs:
                _unsupported(
                    "Recursive schema has no supported transport encoding", path
                )
            target = resource
            try:
                for part in ref[2:].split("/"):
                    target = target[part.replace("~1", "/").replace("~0", "~")]
            except (KeyError, TypeError) as exc:
                raise Failure(
                    "unsupported_schema", "Unresolved local schema reference"
                ) from exc
            return self._adapt(target, resource, path, (*refs, identity))
        result = {}
        local = {key: schema[key] for key in _LOCAL_CONSTRAINTS if key in schema}
        for key in ("type", "enum", "const", "description", "required"):
            if key in schema:
                result[key] = copy.deepcopy(schema[key])
        if any(
            isinstance(v, (dict, list)) for v in schema.get("enum", [])
        ) or isinstance(schema.get("const"), (dict, list)):
            _unsupported("Complex enum/const values are unsupported", path)
        if "properties" in schema or schema.get("type") == "object":
            if isinstance(schema.get("additionalProperties"), dict):
                _unsupported(
                    "Dynamic dictionaries need an explicit transport encoding", path
                )
            result["type"] = "object"
            result["additionalProperties"] = False
            result["properties"] = {
                key: self._adapt(value, resource, (*path, key), refs)
                for key, value in schema.get("properties", {}).items()
            }
        if "items" in schema:
            result["items"] = self._adapt(schema["items"], resource, (*path, "*"), refs)
        if "minItems" in schema:
            if schema["minItems"] in (0, 1):
                result["minItems"] = schema["minItems"]
            else:
                local["minItems"] = schema["minItems"]
        if "oneOf" in schema and "properties" in schema:
            # e.g. raw span quote versus offsets: preserve object shape in the
            # wire grammar and enforce its exclusive dependency rules locally.
            local["oneOf"] = schema["oneOf"]
        elif "oneOf" in schema:
            result["anyOf"] = [
                self._adapt(item, resource, path, refs) for item in schema["oneOf"]
            ]
        if "anyOf" in schema:
            if "anyOf" in result:
                _unsupported("Combined oneOf/anyOf schemas are unsupported", path)
            result["anyOf"] = [
                self._adapt(item, resource, path, refs) for item in schema["anyOf"]
            ]
        if local:
            result["description"] = (
                result.get("description", "")
                + " Host constraints: "
                + canonical(local).decode()
            )
        return result

    def _flat_ast_schema(self):
        # One fixed node shape avoids the grammar product of 18 op variants and
        # their optional attributes at each expression/metadata reference.
        def pair(value_schema, key_schema):
            return {
                "type": "object",
                "additionalProperties": False,
                "required": ["key", "value"],
                "properties": {"key": key_schema, "value": value_schema},
            }

        properties = {
            "op": {"type": "string", "enum": list(AST_KEYS)},
            "arguments": {
                "type": "array",
                "items": pair(
                    copy.deepcopy(_SCALAR),
                    {
                        "type": "string",
                        "enum": sorted(
                            set().union(*AST_KEYS.values()) - {"op", "inputs", "values"}
                        ),
                    },
                ),
            },
            "inputs": {"type": "array", "items": {"type": "integer"}},
            "enum_entries": {
                "type": "array",
                "items": pair({"type": "string"}, {"type": "string"}),
            },
            "in_values": {"type": "array", "items": copy.deepcopy(_SCALAR)},
        }
        return {
            "type": "object",
            "additionalProperties": False,
            "required": ["nodes"],
            "properties": {
                "nodes": {
                    "type": "array",
                    "minItems": 1,
                    "items": {
                        "type": "object",
                        "additionalProperties": False,
                        "required": list(properties),
                        "properties": properties,
                    },
                }
            },
        }

    def _check_limits(self):
        optional = unions = 0

        def walk(schema):
            nonlocal optional, unions
            if not isinstance(schema, dict):
                return
            optional += len(
                set(schema.get("properties", {})) - set(schema.get("required", []))
            )
            unions += int("anyOf" in schema or isinstance(schema.get("type"), list))
            for key in ("properties", "$defs"):
                for child in schema.get(key, {}).values():
                    walk(child)
            if "items" in schema:
                walk(schema["items"])
            for child in schema.get("anyOf", []):
                walk(child)

        walk(self.schema)
        if optional > 24 or unions > 16:
            _unsupported("Strict schema exceeds provider optional/union limits")
        self.complexity = {"optional_parameters": optional, "union_parameters": unions}
        Draft202012Validator.check_schema(self.schema)

    def _map_asts(self, value, convert):
        value = copy.deepcopy(value)

        def walk(item, path, target):
            if path == target:
                return convert(item)
            if len(path) >= len(target):
                return item
            key = target[len(path)]
            if key == "*" and isinstance(item, list):
                return [walk(child, (*path, "*"), target) for child in item]
            if isinstance(item, dict) and key in item:
                return {**item, key: walk(item[key], (*path, key), target)}
            return item

        for target in sorted(self.ast_paths):
            value = walk(value, (), target)
        return value

    def decode(self, value):
        check_response(value, self.schema)
        if self.wrapped_root:
            value = value["result"]
        decoded = self._map_asts(value, _decode_ast)
        return check_response(decoded, self.host_schema)

    def encode(self, value):
        """Lossless fixture/contract conversion, not a model-output repair path."""
        check_response(value, self.host_schema)
        encoded = self._map_asts(value, _encode_ast)
        if self.wrapped_root:
            encoded = {"result": encoded}
        check_response(encoded, self.schema)
        if self.decode(encoded) != value:
            _fail("AST transport conversion changed the host value")
        return encoded


def _unpack_scalar(value, path):
    kind = value["kind"]
    for name, neutral in (("string", ""), ("number", 0), ("boolean", False)):
        if name != kind and value[name + "_value"] != neutral:
            _fail("Unused tagged-scalar slots must be neutral", path, "scalar_neutral")
    return None if kind == "null" else value[kind + "_value"]


def _pack_scalar(value):
    kinds = {
        str: "string",
        int: "number",
        float: "number",
        bool: "boolean",
        type(None): "null",
    }
    kind = kinds.get(type(value))
    if kind is None:
        _fail("AST literal must be a JSON scalar", rule="ast_scalar")
    return {
        "kind": kind,
        "string_value": value if kind == "string" else "",
        "number_value": value if kind == "number" else 0,
        "boolean_value": value if kind == "boolean" else False,
    }


def _unpack_node(wire, index):
    op = wire["op"]
    allowed = AST_KEYS[op] - {"op", "inputs", "values"}
    arguments = wire["arguments"]
    if len(arguments) > len(allowed):
        _fail(
            "AST has too many arguments for its op", ("nodes", index), "ast_arguments"
        )
    node = {"op": op}
    for item in arguments:
        key = item["key"]
        if key not in allowed or key in node:
            _fail(
                "AST argument is duplicated or unsupported for its op",
                ("nodes", index, "arguments"),
                "ast_arguments",
            )
        node[key] = _unpack_scalar(item["value"], ("nodes", index, "arguments", key))
    for slot, owner, target, bound in (
        ("inputs", "join_address", "inputs", 12),
        ("enum_entries", "enum_lookup", "values", 100),
        ("in_values", "in", "values", 100),
    ):
        values = wire[slot]
        if len(values) > bound or (op != owner and values):
            _fail(
                "AST container is unused by its op or exceeds its bound",
                ("nodes", index, slot),
                "ast_container",
            )
        if op == owner:
            node[target] = (
                [
                    _unpack_scalar(value, ("nodes", index, slot, offset))
                    for offset, value in enumerate(values)
                ]
                if slot == "in_values"
                else copy.deepcopy(values)
            )
    return node


def _pack_node(node):
    return {
        "op": node["op"],
        "arguments": [
            {"key": key, "value": _pack_scalar(value)}
            for key, value in node.items()
            if key not in {"op", "inputs", "values"}
        ],
        "inputs": node.get("inputs", []),
        "enum_entries": node.get("values", []) if node["op"] == "enum_lookup" else [],
        "in_values": [_pack_scalar(value) for value in node["values"]]
        if node["op"] == "in"
        else [],
    }


def _decode_ast(graph):
    nodes = graph["nodes"]
    if not 1 <= len(nodes) <= MAX_AST_NODES:
        _fail("AST node table exceeds node bound")
    values, depths, sizes, dependencies = [], [], [], []
    for index, wire in enumerate(nodes):
        node = _unpack_node(wire, index)
        edges = []

        def child(ref):
            if type(ref) is not int or not 0 <= ref < index:
                _fail(
                    "AST child must reference an earlier node",
                    ("nodes", index),
                    "ast_reference",
                )
            edges.append(ref)
            return ref

        for key in ("input", "condition", "then", "else"):
            if key in node:
                child(node[key])
        for ref in node.get("inputs", []):
            child(ref)
        size = 1 + sum(sizes[ref] for ref in edges)
        depth = max((depths[ref] + 1 for ref in edges), default=0)
        if size > MAX_AST_NODES or depth > MAX_AST_DEPTH:
            _fail(
                "Expanded AST exceeds bounded depth/node limit",
                ("nodes", index),
                "ast_bound",
            )
        for key in ("input", "condition", "then", "else"):
            if key in node:
                node[key] = copy.deepcopy(values[node[key]])
        if "inputs" in node:
            node["inputs"] = [copy.deepcopy(values[ref]) for ref in node["inputs"]]
        if node["op"] == "enum_lookup":
            entries = node["values"]
            if len(entries) > 100 or len({item["key"] for item in entries}) != len(
                entries
            ):
                _fail(
                    "Enum entries exceed the bound or repeat a key",
                    ("nodes", index, "values"),
                    "enum_keys",
                )
            node["values"] = {item["key"]: item["value"] for item in entries}
        values.append(node)
        sizes.append(size)
        depths.append(depth)
        dependencies.append(edges)
    seen, pending = set(), [len(nodes) - 1]
    while pending:
        index = pending.pop()
        if index not in seen:
            seen.add(index)
            pending.extend(dependencies[index])
    if len(seen) != len(nodes):
        _fail("AST node table contains unreachable nodes", rule="ast_unreachable")
    return values[-1]


def _encode_ast(ast):
    nodes = []

    def walk(node, depth=0):
        if depth > MAX_AST_DEPTH or len(nodes) >= MAX_AST_NODES:
            _fail("AST exceeds transport bounds")
        item = copy.deepcopy(node)
        for key in ("input", "condition", "then", "else"):
            if key in item:
                item[key] = walk(item[key], depth + 1)
        if "inputs" in item:
            item["inputs"] = [walk(child, depth + 1) for child in item["inputs"]]
        if item["op"] == "enum_lookup":
            item["values"] = [
                {"key": key, "value": value} for key, value in item["values"].items()
            ]
        nodes.append(_pack_node(item))
        return len(nodes) - 1

    walk(ast)
    return {"nodes": nodes}
