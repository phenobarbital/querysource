"""
SchemaIntrospectable — class-introspection mixin for MultiQuery components.

Provides three classmethods that derive JSON Schema and attribute lists from
class-level type annotations and ``kwargs.pop(...)`` / ``kwargs.get(...)``
patterns inside ``__init__``.

This mixin is inherited by both :class:`AbstractMulti` (operators, transforms,
sources) and :class:`AbstractDestination` so the documentation endpoint can
introspect every component uniformly.

Also exposes a module-level :func:`describe_class` helper used by
:mod:`querysource.queries.multi.registry` to parse docstrings of components
that are not SchemaIntrospectable subclasses (legacy ``ThreadSource``-based
sources).
"""
from __future__ import annotations

import inspect
import json
import re
import textwrap
import typing
from typing import Any, Union


# Default icon name per component category. Frontend can map these to its own
# icon set (Lucide/Heroicons/etc.). Individual classes override by setting an
# ``_icon`` class attribute.
DEFAULT_CATEGORY_ICONS: dict[str, str] = {
    "Sources": "database",
    "Operators": "git-merge",
    "Transformations": "shuffle",
    "Destinations": "upload-cloud",
    "Components": "box",
}

# Compiled at module level to avoid re-compiling inside the MRO loop.
# NOTE: The default-value capture group [^)]+ stops at the first ')'.
# This truncates nested calls like fn(a, b) or tuple literals (x, y).
# The default field is informational-only; treat complex defaults as approximate.
_KWARGS_PATTERN = re.compile(
    r"kwargs\.(?:pop|get)\s*\(\s*['\"](\w+)['\"]"
    r"(?:\s*,\s*([^)]+))?\s*\)"
)

# Typed assignment pattern: captures the type annotation on assignments like
#     driver: str = kwargs.get("driver", "pg") or "pg"
#     self._pk: List[str] = kwargs.get("pk", []) or []
#     method: str = (kwargs.get("method", "append") or "append").lower()
# Groups: (type_str, kwarg_name, default_str).
# Anchored to start-of-line (re.MULTILINE) so we don't fold the function
# signature's trailing ``-> None:`` into the next line's assignment. The type
# group forbids ``\n`` and ``=`` so it cannot span lines or capture the
# assignment operator. The optional ``(?:[ \t]*\(\s*)?`` swallows a leading
# ``(`` that wraps the kwargs.get expression (e.g. ``(kwargs.get(...) or x).lower()``).
_KWARGS_TYPED_PATTERN = re.compile(
    r"^[ \t]*(?:self\.)?_?\w+[ \t]*:[ \t]*([^\n=]+?)[ \t]*=[ \t]*"
    r"(?:\([ \t]*)?"
    r"kwargs\.(?:pop|get)\s*\(\s*['\"](\w+)['\"]"
    r"(?:\s*,\s*([^)]+))?\s*\)",
    re.MULTILINE,
)


def _split_top_level(s: str, sep: str = ",") -> list[str]:
    """Split *s* on *sep* but skip over content inside []/{}/() brackets."""
    parts: list[str] = []
    depth = 0
    current: list[str] = []
    for ch in s:
        if ch in "([{":
            depth += 1
            current.append(ch)
        elif ch in ")]}":
            depth -= 1
            current.append(ch)
        elif ch == sep and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        parts.append("".join(current))
    return parts


def _type_to_json_schema(type_str: str) -> dict:
    """Convert a Python type string to a JSON Schema draft-2020-12 type dict.

    Handles primitive types plus common generics: ``List[X]``, ``Dict[X, Y]``,
    ``Tuple[...]``, ``Set[X]``, ``Optional[X]``, ``Union[X, Y, ...]``, and the
    Python 3.10+ pipe-union syntax (``X | Y``). ``typing.`` prefixes are
    accepted. Optional/Union containing ``None`` becomes a nullable type.
    """
    type_str = (type_str or "").strip()
    if not type_str:
        return {}

    # Pipe-union syntax: X | Y | None
    if "|" in type_str and not type_str.startswith("Literal"):
        parts = [p.strip() for p in _split_top_level(type_str, "|")]
        non_none = [p for p in parts if p not in ("None", "type(None)")]
        nullable = len(parts) > len(non_none)
        if not non_none:
            return {"type": "null"}
        if len(non_none) == 1:
            inner = _type_to_json_schema(non_none[0])
            if nullable:
                _make_nullable(inner)
            return inner
        # Multiple non-None alternatives — collapse to a type-list when each
        # alternative resolves to a single primitive type.
        types: list[str] = []
        for p in non_none:
            sub = _type_to_json_schema(p)
            t = sub.get("type")
            if isinstance(t, str):
                types.append(t)
            else:
                # Mixed/complex alternatives — fall back to no type constraint.
                return {}
        if nullable:
            types.append("null")
        return {"type": types}

    # Optional[X] → unwrap, mark nullable
    m = re.match(r"^(?:typing\.)?Optional\[(.+)\]$", type_str)
    if m:
        inner = _type_to_json_schema(m.group(1))
        _make_nullable(inner)
        return inner

    # Union[X, Y, ...] → reuse pipe-union logic
    m = re.match(r"^(?:typing\.)?Union\[(.+)\]$", type_str)
    if m:
        return _type_to_json_schema(
            " | ".join(p.strip() for p in _split_top_level(m.group(1), ","))
        )

    # List[X] / list[X]
    m = re.match(r"^(?:typing\.)?[Ll]ist\[(.+)\]$", type_str)
    if m:
        items = _type_to_json_schema(m.group(1))
        return {"type": "array", "items": items} if items else {"type": "array"}

    # Tuple[...] / tuple[...]  (item types are heterogeneous — keep generic)
    if re.match(r"^(?:typing\.)?[Tt]uple\[", type_str):
        return {"type": "array"}

    # Dict[X, Y] / dict[X, Y]
    if re.match(r"^(?:typing\.)?[Dd]ict\[", type_str):
        return {"type": "object"}

    # Set[X] / set[X] / FrozenSet[X]
    if re.match(r"^(?:typing\.)?(?:Frozen)?[Ss]et\[", type_str):
        return {"type": "array"}

    mapping = {
        "str": {"type": "string"},
        "int": {"type": "integer"},
        "float": {"type": "number"},
        "bool": {"type": "boolean"},
        "list": {"type": "array"},
        "dict": {"type": "object"},
        "tuple": {"type": "array"},
        "set": {"type": "array"},
        "None": {"type": "null"},
        "Any": {},
    }
    return mapping.get(type_str, {"type": "string"})


def _make_nullable(schema: dict) -> None:
    """In-place: extend ``schema['type']`` so the value can also be null."""
    if "type" not in schema:
        return
    t = schema["type"]
    if isinstance(t, list):
        if "null" not in t:
            t.append("null")
    elif t != "null":
        schema["type"] = [t, "null"]


def _hint_to_str(hint) -> str:
    """Convert a type hint to a string representation."""
    if hint is Any:
        return "Any"
    if hasattr(hint, "__name__"):
        return hint.__name__
    # Handle generics like Optional[str], Union[str, None], etc.
    origin = getattr(hint, "__origin__", None)
    if origin is Union:
        args = getattr(hint, "__args__", ())
        non_none = [a for a in args if a is not type(None)]
        if non_none:
            return _hint_to_str(non_none[0])
    return str(hint)


def _parse_default(default_str: str | None) -> Any:
    """Parse a default value string into a Python value."""
    if default_str is None:
        return None
    s = default_str.strip()
    if s in ("None", ""):
        return None
    if s in ("True", "False"):
        return s == "True"
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    # Strip quotes for string literals
    if (s.startswith("'") and s.endswith("'")) or (s.startswith('"') and s.endswith('"')):
        return s[1:-1]
    # Handle tuple defaults like ('_x', '_y') — return as raw string
    if s.startswith("(") and s.endswith(")"):
        return s
    # Handle dict ({}) and list ([]) literals via JSON
    import json as _json
    try:
        return _json.loads(s)
    except (ValueError, _json.JSONDecodeError):
        pass
    return s


class SchemaIntrospectable:
    """Mixin providing JSON-Schema introspection from class annotations + __init__.

    Subclasses that mix this in (alongside ABC) gain three classmethods:
    :meth:`get_attributes`, :meth:`get_schema`, :meth:`get_description`.

    The default ``_category`` is ``"Components"``; override it in subclasses to
    classify them as ``"Operators"``, ``"Transformations"``, ``"Sources"``, or
    ``"Destinations"``.
    """

    _category: str = "Components"

    @classmethod
    def get_attributes(cls) -> list[dict]:
        """Return a list of attribute dicts for this component.

        Each dict has keys: ``name``, ``type``, ``default``, ``required``.

        The implementation:
        1. Checks ``typing.get_type_hints(cls)`` for class-level annotations.
        2. Inspects ``__init__`` source for ``kwargs.pop(...)``/``kwargs.get(...)``
           patterns with defaults.
        3. Merges both sources; types default to ``"Any"`` when unresolvable.
        """
        attrs: dict[str, dict] = {}

        # --- Pass 1: class-level type annotations ---
        try:
            hints = typing.get_type_hints(cls)
        except Exception as exc:
            import logging as _log
            _log.getLogger(__name__).warning(
                "get_type_hints(%s) failed, falling back to empty hints: %s",
                cls.__qualname__, exc,
            )
            hints = {}

        # Filter out private / dunder / inherited-from-SchemaIntrospectable annotations
        _skip = {"data"}
        for name, hint in hints.items():
            if name.startswith("__") or name in _skip:
                continue
            if name.startswith("_"):
                # Skip private/dunder attrs — they are implementation details,
                # not user-facing attributes that should appear in schema output.
                continue
            type_str = _hint_to_str(hint)
            attrs[name] = {
                "name": name,
                "type": type_str,
                "default": None,
                "required": True,
            }

        # --- Pass 2: inspect __init__ source for kwargs.pop / kwargs.get ---
        # Collect stop bases by identity (not name) to avoid false matches.
        # ABC is stdlib — no circular-import risk; identity check is more precise.
        from abc import ABC as _ABC
        _stop_bases: tuple[type, ...] = (_ABC, object)
        try:
            from querysource.queries.multi.abstract import AbstractMulti as _AM
            _stop_bases = _stop_bases + (_AM,)
        except ImportError:
            pass
        try:
            from querysource.outputs.destinations.abstract import AbstractDestination as _AD
            _stop_bases = _stop_bases + (_AD,)
        except ImportError:
            pass

        for klass in cls.__mro__:
            if klass is SchemaIntrospectable:
                break
            if klass in _stop_bases:
                break
            try:
                source = inspect.getsource(klass.__init__)
            except (TypeError, OSError):
                continue
            # Pass 2a: typed assignments — `var: TYPE = kwargs.get(...)`.
            # Captures the type annotation so attrs aren't all stuck at "Any".
            for match in _KWARGS_TYPED_PATTERN.finditer(source):
                type_str = match.group(1).strip()
                kwarg_name = match.group(2)
                default_str = match.group(3)
                if kwarg_name in ("backend",):
                    continue
                default = _parse_default(default_str)
                required = default_str is None
                if kwarg_name not in attrs:
                    attrs[kwarg_name] = {
                        "name": kwarg_name,
                        "type": type_str,
                        "default": default,
                        "required": required,
                    }
                else:
                    # Upgrade type when prior pass left it as "Any" or unset.
                    existing_type = attrs[kwarg_name].get("type")
                    if not existing_type or existing_type == "Any":
                        attrs[kwarg_name]["type"] = type_str
                    if attrs[kwarg_name].get("default") is None:
                        attrs[kwarg_name]["default"] = default
                    attrs[kwarg_name]["required"] = required
            # Pass 2b: untyped fallback — `self._attr = kwargs.pop('attr_name', default)`
            # or `kwargs.get('attr_name', default)` without a type annotation.
            for match in _KWARGS_PATTERN.finditer(source):
                kwarg_name = match.group(1)
                default_str = match.group(2)
                if kwarg_name in ("backend",):
                    continue  # skip infra kwargs
                default = _parse_default(default_str)
                required = default_str is None
                if kwarg_name not in attrs:
                    attrs[kwarg_name] = {
                        "name": kwarg_name,
                        "type": "Any",
                        "default": default,
                        "required": required,
                    }
                else:
                    # Merge: fill in default/required if not already set from hints
                    if attrs[kwarg_name]["default"] is None:
                        attrs[kwarg_name]["default"] = default
                    attrs[kwarg_name]["required"] = required

        return list(attrs.values())

    @classmethod
    def get_schema(cls) -> dict:
        """Return both a JSON Schema (draft-2020-12) and a simplified attribute list.

        Returns:
            dict with keys:
                ``json_schema`` — JSON Schema draft-2020-12 object.
                ``attributes``  — simplified ``[{name, type, default, required}]`` list.
        """
        attrs = cls.get_attributes()
        properties = {}
        for a in attrs:
            properties[a["name"]] = _type_to_json_schema(a["type"])
            if a.get("description"):
                properties[a["name"]]["description"] = a["description"]

        json_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "title": cls.__name__,
            "properties": properties,
            "required": [a["name"] for a in attrs if a.get("required")],
        }
        return {"json_schema": json_schema, "attributes": attrs}

    @classmethod
    def get_description(cls) -> dict:
        """Return documentation metadata extracted from the class docstring.

        Delegates to the module-level :func:`describe_class` helper so the
        same parsing logic is used by the catalog for both
        SchemaIntrospectable and legacy (e.g. ``ThreadSource``) components.

        Returns:
            dict with keys: ``name``, ``description``, ``usage``,
            ``category``, ``example`` (str), ``icon``.
        """
        return describe_class(cls)


# ---------------------------------------------------------------------------
# Companion-file documentation (hybrid model)
# ---------------------------------------------------------------------------
#
# A component may ship a sibling ``<module>.catalog.yaml`` file holding the
# documentation the code cannot express by itself: per-field prose,
# schema-level constraints (``oneOf``, ``additionalProperties``), the usage
# example, icon, and display name. The companion is MERGED onto whatever the
# introspection helpers derive from the class — code owns the structure
# (property names/types/required when introspectable), the companion owns the
# prose and constraints. This keeps docs editable without touching Python and
# avoids depending solely on docstrings/pydoc.


def _companion_paths(cls: type) -> list:
    """Return candidate companion-file paths for a class, most-specific first.

    Looks for two sibling files next to the class source: ``<ClassName>.catalog.yaml``
    (preferred — unambiguous when several components share a module, e.g.
    ``filter/flt.py`` → ``Filter.catalog.yaml``) and ``<module>.catalog.yaml``
    (e.g. ``sources/query.py`` → ``query.catalog.yaml``). Returns ``[]`` when the
    class source file cannot be located.
    """
    from pathlib import Path
    try:
        src = inspect.getfile(cls)
    except (TypeError, OSError):
        return []
    p = Path(src)
    return [
        p.with_name(f"{cls.__name__}.catalog.yaml"),
        p.with_name(f"{p.stem}.catalog.yaml"),
    ]


def load_companion_doc(cls: type) -> dict | None:
    """Load and parse a class's sibling ``.catalog.yaml`` companion doc.

    Tries ``<ClassName>.catalog.yaml`` then ``<module>.catalog.yaml`` (see
    :func:`_companion_paths`). Returns the parsed mapping, or ``None`` when no
    companion file exists, PyYAML is unavailable, or the file does not parse to
    a mapping.
    """
    path = next((p for p in _companion_paths(cls) if p.is_file()), None)
    if path is None:
        return None
    try:
        import yaml
    except ImportError:
        import logging as _log
        _log.getLogger(__name__).warning(
            "PyYAML not installed; ignoring companion doc %s", path
        )
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — surface parse errors as a warning
        import logging as _log
        _log.getLogger(__name__).warning(
            "Failed to parse companion doc %s: %s", path, exc
        )
        return None
    return data if isinstance(data, dict) else None


def _resolve_class_enum(cls: type, attr: str) -> list | None:
    """Resolve a class attribute/classmethod into a list of enum values.

    The named attribute may be a classmethod/callable (called with no args), a
    ``dict`` (its keys are used), or a list/tuple/set. Returns ``None`` — with a
    warning — when the attribute is missing or yields an unusable value, so a
    stale directive degrades to "no enum constraint" rather than crashing docs.
    """
    target = getattr(cls, attr, None)
    if target is None:
        import logging as _log
        _log.getLogger(__name__).warning(
            "enum_from_class: %s has no attribute %r", cls.__qualname__, attr
        )
        return None
    if callable(target):
        try:
            target = target()
        except Exception as exc:  # noqa: BLE001
            import logging as _log
            _log.getLogger(__name__).warning(
                "enum_from_class: %s.%s() raised %s", cls.__qualname__, attr, exc
            )
            return None
    if isinstance(target, dict):
        return list(target.keys())
    if isinstance(target, (list, tuple, set)):
        return list(target)
    import logging as _log
    _log.getLogger(__name__).warning(
        "enum_from_class: %s.%s is not a list/dict/callable", cls.__qualname__, attr
    )
    return None


def _resolve_class_directives(node, cls: type) -> None:
    """In-place: resolve ``enum_from_class`` directives anywhere in a schema.

    Walks nested dicts/lists; whenever a mapping carries ``enum_from_class``, the
    directive is replaced by a concrete ``enum`` list pulled from the class — so
    a documented enum stays bound to the code that defines the valid values.
    """
    if isinstance(node, dict):
        if "enum_from_class" in node:
            values = _resolve_class_enum(cls, node.pop("enum_from_class"))
            if values is not None:
                node["enum"] = values
        for value in node.values():
            _resolve_class_directives(value, cls)
    elif isinstance(node, list):
        for item in node:
            _resolve_class_directives(item, cls)


def build_companion_catalog(
    cls: type,
    introspected_attrs: list[dict] | None = None,
) -> dict | None:
    """Build a ``_catalog``-shaped override dict from a class's companion doc.

    Performs the hybrid merge: ``introspected_attrs`` (from the code) form the
    structural baseline; the companion's ``attributes`` overlay prose (and any
    explicitly-declared ``type``/``default``/``required``) by ``name`` and append
    fields the code did not expose. A ``json_schema`` is then synthesized from
    the merged attributes and the companion's ``schema:`` block (``title``,
    ``description``, ``oneOf``/``anyOf``/``allOf``, ``required``,
    ``additionalProperties``, ``comment``).

    Args:
        cls: The component class.
        introspected_attrs: Attribute dicts already derived from introspection
            (``[{name, type, default, required}, ...]``). Defaults to empty.

    Returns:
        A dict with keys ``display_name``, ``description``, ``usage``,
        ``category``, ``icon``, ``example``, ``attributes``, ``json_schema`` —
        directly consumable by the registry/``describe_class`` override paths —
        or ``None`` when the class has no companion file.
    """
    doc = load_companion_doc(cls)
    if not doc:
        return None

    # Per-attribute keys carried into the merge. ``schema`` is an explicit
    # JSON-Schema fragment for complex fields (nested objects, arrays of
    # objects); ``enum`` is a shorthand for a constrained scalar.
    _ATTR_KEYS = ("type", "default", "required", "description", "schema", "enum")

    # --- merge attributes: code structure + companion prose -----------------
    merged: dict[str, dict] = {}
    for a in introspected_attrs or []:
        merged[a["name"]] = dict(a)
    for a in doc.get("attributes") or []:
        name = a.get("name")
        if not name:
            continue
        if name in merged:
            for key in _ATTR_KEYS:
                if key in a:
                    merged[name][key] = a[key]
        else:
            merged[name] = {
                "name": name,
                "type": a.get("type", "Any"),
                "default": a.get("default"),
                "required": a.get("required", False),
                "description": a.get("description", ""),
                **{k: a[k] for k in ("schema", "enum") if k in a},
            }
    attributes = list(merged.values())

    # --- synthesize property schemas ---------------------------------------
    # An explicit ``schema`` fragment wins; otherwise derive from the type
    # string and layer on an optional ``enum`` shorthand.
    properties: dict = {}
    for a in attributes:
        if isinstance(a.get("schema"), dict):
            prop = dict(a["schema"])
        else:
            prop = _type_to_json_schema(a.get("type", "Any"))
            if a.get("enum"):
                prop["enum"] = a["enum"]
        if a.get("description") and "description" not in prop:
            prop["description"] = a["description"]
        if a.get("default") is not None and "default" not in prop:
            prop["default"] = a["default"]
        properties[a["name"]] = prop

    schema_block = doc.get("schema") or {}

    # Object-level schema (the per-item shape): properties + structural
    # constraints. Reused as ``items`` when the component is a list of objects.
    object_schema: dict = {"type": "object", "properties": properties}
    if "required" in schema_block:
        object_schema["required"] = schema_block["required"]
    else:
        derived_required = [a["name"] for a in attributes if a.get("required")]
        if derived_required:
            object_schema["required"] = derived_required
    for key in ("oneOf", "anyOf", "allOf"):
        if key in schema_block:
            object_schema[key] = schema_block[key]
    if schema_block.get("comment"):
        object_schema["$comment"] = schema_block["comment"]
    object_schema["additionalProperties"] = schema_block.get(
        "additionalProperties", True
    )

    title = schema_block.get("title") or doc.get("display_name") or cls.__name__
    description = schema_block.get("description")

    if schema_block.get("type") == "array":
        # Component is written as a LIST of rule objects (e.g. Join). The
        # object schema becomes the array's ``items``.
        json_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "array",
            "title": title,
            "items": object_schema,
        }
        if description:
            json_schema["description"] = description
        for key in ("minItems", "maxItems"):
            if key in schema_block:
                json_schema[key] = schema_block[key]
    else:
        json_schema = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "title": title,
            **object_schema,
        }
        if description:
            json_schema["description"] = description

    # Reusable sub-schemas: a companion ``schema.defs`` mapping is emitted as
    # ``$defs`` at the schema root, so per-attribute ``schema`` fragments can
    # ``$ref: '#/$defs/<name>'`` instead of duplicating large enums.
    if isinstance(schema_block.get("defs"), dict):
        json_schema["$defs"] = schema_block["defs"]

    # Bind ``enum_from_class`` directives to live class values so documented
    # enums cannot drift from the code that defines the valid options.
    _resolve_class_directives(json_schema, cls)

    return {
        "display_name": doc.get("display_name"),
        "description": doc.get("description"),
        "usage": doc.get("usage"),
        "category": doc.get("category"),
        "icon": doc.get("icon"),
        "example": doc.get("example"),
        "attributes": attributes,
        "json_schema": json_schema,
    }


# ---------------------------------------------------------------------------
# Module-level docstring parser
# ---------------------------------------------------------------------------
#
# `describe_class` is a free function (not a classmethod) so the catalog can
# parse docstrings of any component class — including legacy ``ThreadSource``
# subclasses that do not inherit from SchemaIntrospectable.
#
# Returns a dict with: name, description, usage, category, example, icon.
# `example` is a pre-formatted JSON string ready for the UI; the raw text of
# the docstring `Example:` block is preferred (it preserves the author's
# formatting), falling back to ``json.dumps(parsed_dict, indent=2)``.


def _is_section_header(stripped_line: str) -> bool:
    """True if a docstring line looks like a 'Section:' header.

    We treat a line as a section header when it is non-empty, ends in ':',
    and does not start with characters that appear in code-block bodies
    (``{``, ``"``, ``[``).
    """
    if not stripped_line or not stripped_line.endswith(":"):
        return False
    return not stripped_line.startswith(("{", '"', "[", "*"))


def _parse_usage_section(lines: list[str], start: int) -> tuple[str, int]:
    """Parse a ``Usage:`` block starting at line index ``start``.

    Returns:
        Tuple of (usage_text, index_of_next_line_to_process).
    """
    parts: list[str] = []
    after = lines[start].strip()[len("usage:"):].strip()
    if after:
        parts.append(after)
    i = start + 1
    while i < len(lines):
        stripped = lines[i].strip()
        if _is_section_header(stripped):
            break
        if stripped:
            parts.append(stripped)
        i += 1
    return " ".join(parts), i


def _parse_example_section(lines: list[str], start: int) -> tuple[str, dict, int]:
    """Parse an ``Example:`` block starting at line index ``start``.

    Captures every line of a brace-balanced JSON object so nested ``}`` does
    not prematurely close the block.

    Returns:
        Tuple of (example_raw_text, example_parsed_dict, index_of_next_line).
        ``example_raw_text`` preserves the author's original indentation
        (re-dedented). ``example_parsed_dict`` is ``{}`` when the block is
        not valid JSON.
    """
    raw_block: list[str] = []
    json_block: list[str] = []
    i = start + 1
    in_block = False
    depth = 0
    while i < len(lines):
        line = lines[i].rstrip()
        stripped = line.strip()
        if not in_block and stripped.startswith("{"):
            in_block = True
        if in_block:
            raw_block.append(line)
            json_block.append(line)
            # Count braces ignoring those inside string literals. The
            # docstring examples use plain JSON without escaped quotes, so a
            # simple character-by-character pass is sufficient.
            in_string = False
            prev_char = ""
            for ch in line:
                if ch == '"' and prev_char != "\\":
                    in_string = not in_string
                elif not in_string:
                    if ch == "{":
                        depth += 1
                    elif ch == "}":
                        depth -= 1
                prev_char = ch
            if depth <= 0 and stripped.endswith("}"):
                i += 1
                break
        elif _is_section_header(stripped):
            break
        i += 1

    example_dict: dict = {}
    if json_block:
        try:
            example_dict = json.loads("\n".join(json_block))
        except (ValueError, json.JSONDecodeError):
            pass

    example_text = textwrap.dedent("\n".join(raw_block)).strip()
    return example_text, example_dict, i


def _parse_literal_block(lines: list[str], start: int) -> tuple[str, int]:
    """Parse an indented rST literal block following a line ending with ``::``.

    rST literal blocks are introduced by a line ending in ``::`` and
    contain indented lines. The block ends at the first non-blank line
    whose indentation is less than or equal to the introducing line's.

    Args:
        lines: The full docstring lines list.
        start: Index of the line containing the ``::`` marker.

    Returns:
        Tuple of (literal_block_text, index_of_next_line_to_process).
    """
    intro = lines[start]
    intro_indent = len(intro) - len(intro.lstrip())
    block_lines: list[str] = []
    i = start + 1
    # Skip leading blank lines
    while i < len(lines) and not lines[i].strip():
        i += 1
    # Capture indented lines (and blanks between them)
    while i < len(lines):
        raw = lines[i]
        if not raw.strip():
            block_lines.append("")
            i += 1
            continue
        indent = len(raw) - len(raw.lstrip())
        if indent <= intro_indent:
            break
        block_lines.append(raw)
        i += 1
    # Drop trailing blanks
    while block_lines and not block_lines[-1].strip():
        block_lines.pop()
    text = textwrap.dedent("\n".join(block_lines)).strip()
    return text, i


def _extract_body_usage_and_literal(lines: list[str]) -> tuple[str, str]:
    """Extract a usage paragraph and the first literal block from the docstring body.

    Walks the docstring body (everything after the first summary line) to find:

    * ``usage`` — the first prose paragraph. If the body opens with a
      ``description::``-introduced literal block, the literal block is
      skipped and the paragraph that follows it is used instead.
    * ``literal`` — the dedented text of the first rST literal block
      encountered, used as an ``example`` fallback when no explicit
      ``Example:`` JSON block is present.

    Both values are returned independently; callers decide whether to use
    the literal block.
    """
    if len(lines) < 2:
        return "", ""

    usage_parts: list[str] = []
    literal_text = ""
    i = 1  # skip the summary line
    usage_done = False
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        if not stripped:
            # Blank line ends the current usage paragraph but keeps scanning
            # for an upcoming literal block (e.g. ``Configuration shape::``).
            if usage_parts:
                usage_done = True
            i += 1
            continue
        # Stop at non-`::` section headers (e.g. "Args:", "Attributes:").
        if _is_section_header(stripped) and not stripped.endswith("::"):
            break
        if stripped.endswith("::"):
            # rST literal-block introducer. Keep the first literal block we
            # encounter; drop later ones to avoid clobbering.
            if not literal_text:
                literal_text, i = _parse_literal_block(lines, i)
            else:
                _, i = _parse_literal_block(lines, i)
            continue
        # Regular prose: counts toward usage only until the first paragraph
        # break. Subsequent prose paragraphs (e.g. extra notes between the
        # summary and the literal block) are ignored for usage but don't
        # stop us from finding a later literal block.
        if not usage_done:
            usage_parts.append(stripped)
        i += 1
    return " ".join(usage_parts), literal_text


def describe_class(cls: type, category: str | None = None) -> dict:
    """Parse a class docstring into documentation metadata.

    Args:
        cls: The component class.
        category: Optional override for the component category. When ``None``,
            falls back to ``cls._category`` and then ``"Components"``.

    Returns:
        Dict with keys: ``name``, ``description``, ``usage``, ``category``,
        ``example`` (str), ``icon``.
    """
    doc = cls.__doc__ or ""
    name = cls.__name__
    if category is None:
        category = getattr(cls, "_category", "Components")

    lines = doc.strip().splitlines()
    description = lines[0].strip() if lines else ""
    usage = ""
    example_text = ""
    example_dict: dict = {}

    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not usage and stripped.lower().startswith("usage:"):
            usage, i = _parse_usage_section(lines, i)
            continue
        if not example_text and stripped.lower().startswith("example:"):
            example_text, example_dict, i = _parse_example_section(lines, i)
            continue
        i += 1

    body_usage, body_literal = _extract_body_usage_and_literal(lines)
    if not usage:
        usage = body_usage

    if not example_text and example_dict:
        example_text = json.dumps(example_dict, indent=2)
    if not example_text and body_literal:
        # rST literal block — typically a YAML config example for destinations.
        # Try to parse as YAML and re-serialize as JSON so the UI shows JSON.
        try:
            import yaml  # noqa: PLC0415
            parsed = yaml.safe_load(body_literal)
            if isinstance(parsed, dict):
                example_text = json.dumps(parsed, indent=2)
            else:
                example_text = body_literal
        except Exception:
            example_text = body_literal

    icon = getattr(cls, "_icon", None) or DEFAULT_CATEGORY_ICONS.get(
        category, DEFAULT_CATEGORY_ICONS["Components"]
    )

    result = {
        "name": name,
        "description": description,
        "usage": usage,
        "category": category,
        "example": example_text,
        "icon": icon,
    }

    # A sibling ``<module>.catalog.yaml`` companion file (preferred) or a
    # class-level ``_catalog`` dict (legacy) overrides any of the parsed fields.
    # Used by components whose docstring/__init__ don't accurately describe
    # their user-facing config shape (e.g. ``ThreadQuery``, which is dispatched
    # via the YAML ``queries:`` block, not as a named source).
    overrides = build_companion_catalog(cls) or getattr(cls, "_catalog", None)
    if isinstance(overrides, dict):
        if overrides.get("display_name"):
            result["name"] = overrides["display_name"]
        for key in ("description", "usage", "example", "icon", "category"):
            if overrides.get(key):
                result[key] = overrides[key]

    return result


# ---------------------------------------------------------------------------
# Source-class introspection (ThreadSource subclasses)
# ---------------------------------------------------------------------------
#
# Source classes (subclasses of ``ThreadSource``) don't follow the flat
# ``kwargs.pop(...)`` pattern used by operators/transforms. Instead they
# receive an ``options`` dict and pull nested sub-dicts:
#
#     creds = options.get('credentials', {})
#     self._client_id = creds.get('client_id', 'SHAREPOINT_APP_ID')
#     source = options.get('source', {})
#     self._filename = source.get('filename', '')
#
# ``extract_source_schema`` parses this pattern to produce both a flat
# attribute list (with dotted names like ``credentials.client_id``) and a
# nested JSON Schema mirroring the YAML config shape.

# `<localvar> = <opts>.get('<container>', {})` — captures the sub-dict alias.
_SOURCE_CONTAINER_PATTERN = re.compile(
    r"(\w+)\s*=\s*(\w+)\.get\s*\(\s*['\"](\w+)['\"]"
)

# `<varname>.get|pop('<field>', <default>)` — same default-capture limitation
# as _KWARGS_PATTERN (stops at first ')'; nested defaults are approximate).
_SOURCE_FIELD_PATTERN = re.compile(
    r"(\w+)\.(?:get|pop)\s*\(\s*['\"](\w+)['\"]"
    r"(?:\s*,\s*([^)]+))?\s*\)"
)


def _source_options_param(cls) -> str | None:
    """Return the name of the source's options-dict parameter (3rd positional).

    Most sources use ``options``; ``FileSource`` uses ``file_options``;
    ``ThreadQuery`` uses ``query``. The 3rd positional parameter after
    (self, name, ...) is the convention enforced by ``ThreadSource.__init__``.
    """
    try:
        sig = inspect.signature(cls.__init__)
    except (TypeError, ValueError):
        return None
    params = [p for p in sig.parameters.values() if p.name != "self"]
    if len(params) < 2:
        return None
    return params[1].name  # skip 'name', return the options-dict param


def extract_source_schema(cls) -> dict:
    """Build ``{json_schema, attributes}`` for a ThreadSource subclass.

    Parses the ``__init__`` of every class in the MRO up to (but not
    including) ``ThreadSource`` for ``<container>.get|pop(...)`` calls and
    groups them into a nested JSON Schema. Top-level fields pulled directly
    from the options dict become root properties; fields pulled from
    aliased sub-dicts (e.g. ``creds = options.get('credentials', {})``)
    become nested ``properties`` under the container name.

    Returns:
        Dict with keys ``json_schema`` (nested object schema) and
        ``attributes`` (flat list with dotted names — empty when the source
        has no introspectable fields, e.g. ``ThreadQuery``).
    """
    from .sources.base import ThreadSource

    opts_param = _source_options_param(cls)
    if not opts_param:
        return {"json_schema": None, "attributes": []}

    # 1. Collect __init__ source from this class up the MRO until ThreadSource.
    bodies: list[str] = []
    for klass in cls.__mro__:
        if klass is ThreadSource or klass is object:
            break
        try:
            bodies.append(inspect.getsource(klass.__init__))
        except (TypeError, OSError):
            continue
    code = "\n".join(bodies)
    if not code:
        return {"json_schema": None, "attributes": []}

    # 2. Map sub-dict aliases: var_name -> container_name
    container_aliases: dict[str, str] = {}
    for m in _SOURCE_CONTAINER_PATTERN.finditer(code):
        local_var, parent, container = m.group(1), m.group(2), m.group(3)
        if parent == opts_param and local_var not in (opts_param, "self"):
            container_aliases[local_var] = container
    container_names = set(container_aliases.values())

    # 3. Extract fields. Keep insertion order so the rendered schema reflects
    #    the source-code order developers wrote.
    top_level: dict[str, dict] = {}
    containers: dict[str, dict[str, dict]] = {}
    for m in _SOURCE_FIELD_PATTERN.finditer(code):
        var, field, default_str = m.group(1), m.group(2), m.group(3)
        default = _parse_default(default_str)
        required = default_str is None
        if var == opts_param:
            # Direct pull from options dict — top-level field, unless the
            # field itself is a container we already aliased.
            if field in container_names:
                continue
            top_level.setdefault(field, {"default": default, "required": required})
        elif var in container_aliases:
            container = container_aliases[var]
            # Skip the alias-assignment line itself (creds = options.get('credentials', {}))
            if field == container:
                continue
            containers.setdefault(container, {})
            containers[container].setdefault(
                field, {"default": default, "required": required}
            )

    # 4. Build flat attributes list + nested json_schema.
    attributes: list[dict] = []
    properties: dict = {}
    root_required: list[str] = []

    for field, meta in top_level.items():
        attributes.append({
            "name": field,
            "type": "Any",
            "default": meta["default"],
            "required": meta["required"],
        })
        properties[field] = {}
        if meta["required"]:
            root_required.append(field)

    for container, fields in containers.items():
        sub_props: dict = {}
        sub_required: list[str] = []
        for field, meta in fields.items():
            sub_props[field] = {}
            if meta["required"]:
                sub_required.append(field)
            attributes.append({
                "name": f"{container}.{field}",
                "type": "Any",
                "default": meta["default"],
                "required": meta["required"],
            })
        prop_schema: dict = {"type": "object", "properties": sub_props}
        if sub_required:
            prop_schema["required"] = sub_required
        properties[container] = prop_schema

    json_schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "title": cls.__name__,
        "properties": properties,
        "required": root_required,
    }
    return {"json_schema": json_schema, "attributes": attributes}
