"""
ComponentRegistry — discovers and catalogs all MultiQuery components.

Provides :meth:`ComponentRegistry.discover_all`, :meth:`ComponentRegistry.get_catalog`,
and :meth:`ComponentRegistry.validate_pipeline` for the documentation and validation
HTTP endpoints.
"""
from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("QS.ComponentRegistry")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class AttributeInfo:
    """Single attribute definition."""
    name: str
    type: str
    default: Any = None
    required: bool = False
    description: str = ""


@dataclass
class ComponentInfo:
    """Documentation for a single MultiQuery component.

    Attributes:
        example: Pre-formatted JSON snippet (string) showing how to write
            this component in a MultiQuery pipeline. Rendered by the UI.
        icon: Icon name for the frontend. Defaults to a per-category icon
            and can be overridden by setting ``_icon`` on the class.
    """
    name: str
    category: str  # "Operators" | "Transformations" | "Sources" | "Destinations" | "Components"
    description: str
    usage: str
    attributes: list[AttributeInfo] = field(default_factory=list)
    json_schema: dict | None = field(default_factory=dict)  # None means schema unknown
    example: str = ""
    icon: str = ""


@dataclass
class ValidationError:
    """Single pipeline validation error."""
    step: str
    field: str
    message: str


@dataclass
class ValidationResult:
    """Pipeline validation result."""
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)


# ---------------------------------------------------------------------------
# ComponentRegistry
# ---------------------------------------------------------------------------

class ComponentRegistry:
    """Discovers and catalogs all registered MultiQuery components.

    Scans the filesystem for operator and transform modules, reads the
    SOURCE_REGISTRY and DESTINATION_REGISTRY dicts, and exposes the full
    catalog via :meth:`get_catalog`. Pipeline validation is available via
    :meth:`validate_pipeline`.
    """

    # Known operator step names for validation
    _KNOWN_PIPELINE_KEYS = {
        "queries", "files", "sources",    # data input sections
        "Output", "Transform",            # output/transform sections
        "Info", "Join", "Concat", "Melt", "Merge",  # top-level operators
        "Filter", "GroupBy",              # top-level operators (static)
        "Processors",                     # ignored/passthrough
    }

    @classmethod
    @functools.lru_cache(maxsize=1)
    def discover_all(cls) -> dict[str, type]:
        """Discover all component classes by scanning filesystem and registries.

        The result is cached after the first call (lru_cache maxsize=1) so
        repeated calls within the same process do not re-scan the filesystem.

        Returns:
            Dict mapping component name to class object.
        """
        from querysource.queries.multi import get_operator_module, get_transform_module

        components: dict[str, type] = {}

        # 1. Scan operators directory (dynamic .py files)
        operators_dir = Path(__file__).parent / "operators"
        for py_file in sorted(operators_dir.glob("*.py")):
            if py_file.name.startswith("_") or py_file.name == "abstract.py":
                continue
            clsname = py_file.stem
            try:
                comp_cls = get_operator_module(clsname)
                components[clsname] = comp_cls
            except Exception as exc:
                logger.warning("Could not import operator %s: %s", clsname, exc)

        # Add statically-imported operators: Filter and GroupBy
        try:
            from querysource.queries.multi.operators.filter.flt import Filter
            components["Filter"] = Filter
        except (ImportError, AttributeError) as exc:
            logger.warning("Could not import Filter: %s", exc)

        # Note: GroupBy is already picked up by the glob("*.py") scan above;
        # this fallback is intentionally removed to avoid double-registration.

        # 2. Scan transformations directory (dynamic .py files)
        transforms_dir = Path(__file__).parent / "transformations"
        for py_file in sorted(transforms_dir.glob("*.py")):
            if py_file.name.startswith("_") or py_file.name == "abstract.py":
                continue
            clsname = py_file.stem
            try:
                comp_cls = get_transform_module(clsname)
                components[clsname] = comp_cls
            except Exception as exc:
                # Broad catch: some modules may have optional deps that fail
                # with ImportError or ValueError (e.g. native ABI mismatches).
                logger.warning("Could not import transform %s: %s", clsname, exc)

        # Add GoogleMaps (in subdirectory)
        try:
            from querysource.queries.multi.transformations.google.maps import GoogleMaps
            components["GoogleMaps"] = GoogleMaps
        except (ImportError, AttributeError) as exc:
            logger.warning("Could not import GoogleMaps: %s", exc)

        # 3. Sources (from SOURCE_REGISTRY)
        try:
            from querysource.queries.multi.sources import SOURCE_REGISTRY
            components.update(SOURCE_REGISTRY)
        except (ImportError, AttributeError) as exc:
            logger.warning("Could not import SOURCE_REGISTRY: %s", exc)

        # SOURCE_REGISTRY only holds entries dispatched dynamically from the
        # YAML `sources:` block. ThreadQuery (`queries:` block) and FileSource
        # (`files:` block) are dispatched specially by MultiQS, but they are
        # still valid catalog entries the components API must expose.
        try:
            from querysource.queries.multi.sources import FileSource, ThreadQuery
            components.setdefault("ThreadQuery", ThreadQuery)
            components.setdefault("FileSource", FileSource)
        except (ImportError, AttributeError) as exc:
            logger.warning("Could not import ThreadQuery/FileSource: %s", exc)

        # 4. Destinations — new canonical folder wins on key collision, legacy fills gaps
        try:
            from querysource.queries.multi.destinations import (
                DESTINATION_REGISTRY as _local_destinations,
            )
            # Filesystem-discovered MultiQS-local destinations win on key collision
            components.update(_local_destinations)
        except (ImportError, AttributeError) as exc:
            logger.warning("Could not import queries.multi.destinations registry: %s", exc)

        try:
            from querysource.outputs.destinations import DESTINATION_REGISTRY as _legacy_destinations
            # Merge: only add entries not already provided by the new folder
            for step_name, dest_cls in _legacy_destinations.items():
                components.setdefault(step_name, dest_cls)
        except (ImportError, AttributeError) as exc:
            logger.warning("Could not import legacy DESTINATION_REGISTRY: %s", exc)

        return components

    @classmethod
    def get_catalog(cls) -> list[ComponentInfo]:
        """Return a list of ComponentInfo for all discovered components.

        Docstring parsing (description, usage, example, icon) is delegated to
        :func:`querysource.queries.multi._introspect.describe_class` so the
        same logic applies to both SchemaIntrospectable subclasses and legacy
        (e.g. ``ThreadSource``) sources.

        For SchemaIntrospectable subclasses we additionally populate
        ``attributes`` and ``json_schema`` from the introspection helpers.

        Returns:
            List of ComponentInfo dataclass instances.
        """
        from querysource.queries.multi._introspect import (
            SchemaIntrospectable,
            build_companion_catalog,
            describe_class,
            extract_source_schema,
        )
        try:
            from querysource.queries.multi.sources.base import ThreadSource
        except (ImportError, AttributeError):
            ThreadSource = None  # type: ignore[assignment]

        components = cls.discover_all()
        catalog: list[ComponentInfo] = []
        # Track seen class objects to avoid duplicate catalog entries when the
        # same class is registered under multiple step-name keys (e.g. "DWH"
        # and "DWHDestination" both resolve to the same DWHDestination class).
        _seen_classes: set[int] = set()

        for name, comp_cls in components.items():
            # Deduplicate: if this exact class object was already catalogued,
            # skip the current (YAML step-name) alias entry.
            # id() is safe here: all registered classes are module-level and
            # never garbage-collected during a process lifetime, so id() is stable.
            class_id = id(comp_cls)
            if isinstance(comp_cls, type) and class_id in _seen_classes:
                continue
            if isinstance(comp_cls, type):
                _seen_classes.add(class_id)
            try:
                is_introspectable = isinstance(comp_cls, type) and issubclass(
                    comp_cls, SchemaIntrospectable
                )

                # For non-introspectable classes (legacy ThreadSource-based
                # sources) we still need a sensible category — fall back to
                # heuristic classification.
                category_override = (
                    None if is_introspectable else cls._classify(name, comp_cls)
                )
                desc = describe_class(comp_cls, category=category_override)

                is_source = (
                    ThreadSource is not None
                    and isinstance(comp_cls, type)
                    and issubclass(comp_cls, ThreadSource)
                )
                if is_introspectable:
                    schema = comp_cls.get_schema()
                    introspected_attr_dicts = schema.get("attributes", [])
                    json_schema = schema.get("json_schema", {})
                elif is_source:
                    # ThreadSource subclasses: parse __init__ for nested
                    # options.get/<alias>.get patterns.
                    schema = extract_source_schema(comp_cls)
                    introspected_attr_dicts = schema.get("attributes", [])
                    json_schema = schema.get("json_schema")
                else:
                    # Truly opaque legacy components: schema unknown.
                    introspected_attr_dicts = []
                    json_schema = None

                attrs = [
                    AttributeInfo(
                        name=a["name"],
                        type=a.get("type", "Any"),
                        default=a.get("default"),
                        required=a.get("required", False),
                        description=a.get("description", ""),
                    )
                    for a in introspected_attr_dicts
                ]

                # Documentation override: a sibling ``<module>.catalog.yaml``
                # companion (preferred, hybrid-merged onto the introspected
                # attributes) or a class-level ``_catalog`` dict (legacy) for
                # components whose introspected schema doesn't reflect their
                # user-facing config shape (e.g. ``ThreadQuery``).
                overrides = build_companion_catalog(
                    comp_cls, introspected_attr_dicts
                ) or getattr(comp_cls, "_catalog", None)
                if isinstance(overrides, dict):
                    if isinstance(overrides.get("attributes"), list):
                        attrs = [
                            AttributeInfo(
                                name=a["name"],
                                type=a.get("type", "Any"),
                                default=a.get("default"),
                                required=a.get("required", False),
                                description=a.get("description", ""),
                            )
                            for a in overrides["attributes"]
                        ]
                    if "json_schema" in overrides:
                        json_schema = overrides["json_schema"]

                catalog.append(ComponentInfo(
                    name=desc.get("name", name),
                    category=desc.get("category", "Components"),
                    description=desc.get("description", ""),
                    usage=desc.get("usage", ""),
                    attributes=attrs,
                    json_schema=json_schema,
                    example=desc.get("example", ""),
                    icon=desc.get("icon", ""),
                ))
            except Exception as exc:
                logger.warning("Could not build ComponentInfo for %s: %s", name, exc)

        return catalog

    @classmethod
    def _classify(cls, name: str, comp_cls: type) -> str:
        """Classify a component class into a category string."""
        try:
            from querysource.queries.multi.sources.base import ThreadSource
            if isinstance(comp_cls, type) and issubclass(comp_cls, ThreadSource):
                return "Sources"
        except (ImportError, AttributeError):
            pass
        try:
            from querysource.outputs.destinations.abstract import AbstractDestination
            if isinstance(comp_cls, type) and issubclass(comp_cls, AbstractDestination):
                return "Destinations"
        except (ImportError, AttributeError):
            pass
        # Heuristic from name (kept as final fallback only)
        if name.endswith("Source"):
            return "Sources"
        return "Components"

    @classmethod
    def validate_pipeline(cls, payload: dict) -> ValidationResult:
        """Validate a MultiQuery pipeline definition payload.

        Performs syntactic and structural checks:
        - At least one data source (``queries``, ``files``, or ``sources``) is defined.
        - All operator/transform step names are known.
        - Join and Merge require 2+ data inputs.

        Args:
            payload: Dict representing a MultiQuery pipeline definition.

        Returns:
            :class:`ValidationResult` with ``valid`` flag and list of errors.
        """
        errors: list[ValidationError] = []
        components = cls.discover_all()
        known_names = set(components.keys())

        # Rule 1: at least one source section must exist
        has_sources = (
            bool(payload.get("queries"))
            or bool(payload.get("files"))
            or bool(payload.get("sources"))
        )
        if not has_sources:
            errors.append(ValidationError(
                step="pipeline",
                field="queries/files/sources",
                message="Pipeline must define at least one data source (queries, files, or sources).",
            ))

        # Rule 2: check step names
        skip_keys = {"queries", "files", "sources", "Output", "Transform", "Processors"}
        for step_name, step_value in payload.items():
            if step_name in skip_keys:
                continue

            if step_name not in known_names:
                errors.append(ValidationError(
                    step=step_name,
                    field="",
                    message=f"Unknown operator/transform: '{step_name}'. "
                            f"Available: {sorted(known_names)}",
                ))
                continue

            # Rule 3: structural check — Join/Merge need 2+ inputs
            if step_name in ("Join", "Merge") and isinstance(step_value, dict):
                left = step_value.get("left") or step_value.get("using")
                right = step_value.get("right")
                # If both left and right are not specified, relies on data dict having 2+ entries
                # We can only warn if explicitly checking references
                n_sources = len(payload.get("queries", {})) + len(payload.get("files", {}))
                if n_sources < 2 and not (left and right):
                    errors.append(ValidationError(
                        step=step_name,
                        field="left/right",
                        message=f"'{step_name}' requires at least 2 data sources or explicit left/right keys.",
                    ))

        # Rule 4: check Transform steps
        transform_steps = payload.get("Transform", [])
        if isinstance(transform_steps, list):
            for transform_spec in transform_steps:
                if not isinstance(transform_spec, dict):
                    continue
                for t_name in transform_spec:
                    if t_name not in known_names:
                        errors.append(ValidationError(
                            step=f"Transform/{t_name}",
                            field="",
                            message=f"Unknown transform: '{t_name}'.",
                        ))

        return ValidationResult(
            valid=len(errors) == 0,
            errors=errors,
        )
