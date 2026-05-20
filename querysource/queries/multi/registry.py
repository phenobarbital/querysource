"""
ComponentRegistry — discovers and catalogs all MultiQuery components.

Provides :meth:`ComponentRegistry.discover_all`, :meth:`ComponentRegistry.get_catalog`,
and :meth:`ComponentRegistry.validate_pipeline` for the documentation and validation
HTTP endpoints.
"""
from __future__ import annotations

import functools
import logging
from dataclasses import dataclass, field, asdict
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
    """Documentation for a single MultiQuery component."""
    name: str
    category: str  # "Operators" | "Transformations" | "Sources" | "Destinations" | "Components"
    description: str
    usage: str
    attributes: list[AttributeInfo] = field(default_factory=list)
    json_schema: dict = field(default_factory=dict)
    example: dict = field(default_factory=dict)


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
                # Broad catch: some modules may have optional deps (e.g. pmdarima)
                # that fail with ValueError on numpy ABI mismatch or ImportError.
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

        # 4. Destinations (from DESTINATION_REGISTRY)
        try:
            from querysource.outputs.destinations import DESTINATION_REGISTRY
            components.update(DESTINATION_REGISTRY)
        except (ImportError, AttributeError) as exc:
            logger.warning("Could not import DESTINATION_REGISTRY: %s", exc)

        return components

    @classmethod
    def get_catalog(cls) -> list[ComponentInfo]:
        """Return a list of ComponentInfo for all discovered components.

        For components that inherit from AbstractMulti, uses the introspection
        classmethods. For sources and destinations, builds ComponentInfo from
        __doc__ and __init__ inspection.

        Returns:
            List of ComponentInfo dataclass instances.
        """
        from querysource.queries.multi.abstract import AbstractMulti

        components = cls.discover_all()
        catalog: list[ComponentInfo] = []

        for name, comp_cls in components.items():
            try:
                if isinstance(comp_cls, type) and issubclass(comp_cls, AbstractMulti):
                    # Use introspection classmethods
                    schema = comp_cls.get_schema()
                    desc = comp_cls.get_description()
                    attrs = [
                        AttributeInfo(
                            name=a["name"],
                            type=a.get("type", "Any"),
                            default=a.get("default"),
                            required=a.get("required", False),
                        )
                        for a in schema.get("attributes", [])
                    ]
                    catalog.append(ComponentInfo(
                        name=desc.get("name", name),
                        category=desc.get("category", "Components"),
                        description=desc.get("description", ""),
                        usage=desc.get("usage", ""),
                        attributes=attrs,
                        json_schema=schema.get("json_schema", {}),
                        example=desc.get("example", {}),
                    ))
                else:
                    # Sources / destinations or plain classes
                    category = cls._classify(name, comp_cls)
                    doc = getattr(comp_cls, "__doc__", "") or ""
                    first_line = doc.strip().splitlines()[0].strip() if doc.strip() else ""
                    catalog.append(ComponentInfo(
                        name=name,
                        category=category,
                        description=first_line,
                        usage="",
                        attributes=[],
                        json_schema={
                            "$schema": "https://json-schema.org/draft/2020-12/schema",
                            "type": "object",
                            "title": name,
                            "properties": {},
                            "required": [],
                        },
                        example={},
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
        # Heuristic from name
        if name.startswith("Source"):
            return "Sources"
        if name in ("tableOutput", "TableOutput", "ToSharepoint", "ToS3", "Table", "DWH"):
            return "Destinations"
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
                            f"Available: {sorted(known_names)[:10]}...",
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
