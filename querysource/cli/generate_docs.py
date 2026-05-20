"""
generate_docs — CLI command for generating per-component JSON documentation.

Discovers all MultiQuery components via :class:`ComponentRegistry`, extracts
their documentation using introspection classmethods, and writes one JSON file
per component to an output directory (default: ``generated/``).

Entry point: ``generate-multiquery-docs``
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from dataclasses import asdict
from pathlib import Path


def generate_docs(
    output_dir: str = "generated",
    category: str | None = None,
    fmt: str = "json",
) -> int:
    """Generate per-component documentation files.

    Args:
        output_dir: Directory to write output files (created if missing).
        category: If given, only write components in this category.
        fmt: Output format — ``'json'`` (default) or ``'summary'``.

    Returns:
        Number of documentation files written.
    """
    from querysource.queries.multi.registry import ComponentRegistry

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    catalog = ComponentRegistry.get_catalog()

    if category:
        catalog = [c for c in catalog if c.category == category]

    count = 0
    for info in catalog:
        if fmt == "summary":
            _write_summary(output_path, info)
        else:
            _write_json(output_path, info)
        count += 1

    print(
        f"Generated documentation for {count} component(s) "
        f"in '{output_path}'."
    )
    return count


def _write_json(output_path: Path, info) -> None:
    """Write a single ComponentInfo to a JSON file."""
    # Convert dataclass to plain dict (handles nested dataclasses)
    data = _dataclass_to_dict(info)
    file_path = output_path / f"{info.name}.json"
    file_path.write_bytes(
        json.dumps(data, indent=2, default=str).encode("utf-8")
    )


def _write_summary(output_path: Path, info) -> None:
    """Write a brief text summary file."""
    lines = [
        f"Component: {info.name}",
        f"Category: {info.category}",
        f"Description: {info.description}",
    ]
    if info.usage:
        lines.append(f"Usage: {info.usage}")
    if info.attributes:
        lines.append("Attributes:")
        for attr in info.attributes:
            req = " (required)" if attr.required else ""
            lines.append(f"  - {attr.name}: {attr.type} = {attr.default!r}{req}")
    file_path = output_path / f"{info.name}.txt"
    file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _dataclass_to_dict(obj) -> dict:
    """Recursively convert a dataclass to a plain dict, including nested dataclasses."""
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        result = {}
        for f in dataclasses.fields(obj):
            value = getattr(obj, f.name)
            result[f.name] = _dataclass_to_dict(value)
        return result
    if isinstance(obj, list):
        return [_dataclass_to_dict(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _dataclass_to_dict(v) for k, v in obj.items()}
    return obj


def main() -> None:
    """Argparse entry point for the ``generate-multiquery-docs`` command."""
    parser = argparse.ArgumentParser(
        prog="generate-multiquery-docs",
        description="Generate per-component JSON documentation from MultiQuery registry.",
    )
    parser.add_argument(
        "-o", "--output-dir",
        default="generated",
        help="Directory to write output files (default: generated/).",
    )
    parser.add_argument(
        "-c", "--category",
        default=None,
        choices=["Operators", "Transformations", "Sources", "Destinations", "Components"],
        help="Filter output by component category.",
    )
    parser.add_argument(
        "-f", "--format",
        default="json",
        choices=["json", "summary"],
        dest="fmt",
        help="Output format: 'json' (default) or 'summary' (brief text).",
    )

    args = parser.parse_args()
    count = generate_docs(
        output_dir=args.output_dir,
        category=args.category,
        fmt=args.fmt,
    )
    if count == 0:
        print("No components found.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
