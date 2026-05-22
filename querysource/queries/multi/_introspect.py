"""
SchemaIntrospectable — class-introspection mixin for MultiQuery components.

Provides three classmethods that derive JSON Schema and attribute lists from
class-level type annotations and ``kwargs.pop(...)`` / ``kwargs.get(...)``
patterns inside ``__init__``.

This mixin is inherited by both :class:`AbstractMulti` (operators, transforms,
sources) and :class:`AbstractDestination` so the documentation endpoint can
introspect every component uniformly.
"""
from __future__ import annotations

import inspect
import json
import re
import typing
from typing import Any, Union


def _type_to_json_schema(type_str: str) -> dict:
    """Convert a Python type string to a JSON Schema draft-2020-12 type dict."""
    mapping = {
        "str": {"type": "string"},
        "int": {"type": "integer"},
        "float": {"type": "number"},
        "bool": {"type": "boolean"},
        "list": {"type": "array"},
        "dict": {"type": "object"},
        "None": {"type": "null"},
        "Any": {},
    }
    # Unknown/complex types default to string representation in schema output
    return mapping.get(type_str, {"type": "string"})


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
        except Exception:
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
        for klass in cls.__mro__:
            if klass is SchemaIntrospectable or klass is object:
                break
            # Stop at ABC too (imported lazily to avoid circular deps)
            if klass.__name__ in ("ABC", "AbstractMulti", "AbstractDestination"):
                break
            try:
                source = inspect.getsource(klass.__init__)
            except (TypeError, OSError):
                continue
            # Match: self._attr = kwargs.pop('attr_name', default)
            #     or self._attr = kwargs.get('attr_name', default)
            pattern = re.compile(
                r"kwargs\.(?:pop|get)\s*\(\s*['\"](\w+)['\"]"
                r"(?:\s*,\s*([^)]+))?\s*\)"
            )
            for match in pattern.finditer(source):
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

        Returns:
            dict with keys: ``name``, ``description``, ``usage``, ``category``, ``example``.
        """
        doc = cls.__doc__ or ""
        name = cls.__name__
        category = getattr(cls, "_category", "Components")

        # Split into lines and extract description, usage, example
        lines = doc.strip().splitlines()
        description = lines[0].strip() if lines else ""
        usage = ""
        example: dict = {}

        # Walk through the docstring looking for Usage: and Example: sections
        i = 0
        while i < len(lines):
            stripped = lines[i].strip()
            if stripped.lower().startswith("usage:"):
                # Everything after "Usage:" on this line, or following lines until next section
                usage_parts = []
                after = stripped[len("usage:"):].strip()
                if after:
                    usage_parts.append(after)
                i += 1
                while i < len(lines):
                    next_line = lines[i].strip()
                    if next_line and next_line.endswith(":") and not next_line.startswith("{"):
                        break
                    if next_line:
                        usage_parts.append(next_line)
                    i += 1
                usage = " ".join(usage_parts)
                continue
            if stripped.lower().startswith("example:"):
                # Collect JSON block
                json_lines = []
                i += 1
                in_block = False
                while i < len(lines):
                    next_line = lines[i].rstrip()
                    stripped_next = next_line.strip()
                    if stripped_next.startswith("{"):
                        in_block = True
                    if in_block:
                        json_lines.append(next_line)
                        if stripped_next == "}":
                            break
                    elif stripped_next and stripped_next.endswith(":"):
                        break
                    i += 1
                if json_lines:
                    try:
                        example = json.loads("\n".join(json_lines))
                    except Exception:
                        pass
                i += 1
                continue
            i += 1

        return {
            "name": name,
            "description": description,
            "usage": usage,
            "category": category,
            "example": example,
        }
