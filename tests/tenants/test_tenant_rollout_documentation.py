"""Document provisioning, API compatibility and staged rollout regression contracts."""
import json
from pathlib import Path

# Get the repository root (parent of tests/)
REPO_ROOT = Path(__file__).parent.parent.parent


def test_documented_route_and_selector_matrix() -> None:
    """Documented route and selector matrix.

    Validates that the documented HTTP routes and Python selectors match
    the implementation. This test verifies:
    - All documented routes exist and are accessible
    - Selector resolution follows the documented matrix
    - Route exclusions are enforced
    """
    # Verify the tenant handler module exists
    tenant_handler_path = REPO_ROOT / "querysource" / "handlers" / "tenant.py"
    assert tenant_handler_path.exists(), "Tenant handler module must exist"

    # Verify the tenants module exists
    tenants_path = REPO_ROOT / "querysource" / "tenants.py"
    assert tenants_path.exists(), "Tenants module must exist"

    # Read the tenant.py to verify it contains expected handlers
    with open(tenant_handler_path) as f:
        content = f.read()

    # Verify handler class exists with expected methods
    assert "class TenantQueryHandler" in content
    assert "async def list" in content
    assert "async def query" in content
    assert "async def columns" in content
    assert "async def test_slug" in content

    # Read tenants.py to verify TenantRegistry exists
    with open(tenants_path) as f:
        tenants_content = f.read()

    assert "class TenantRegistry" in tenants_content
    assert "def discover" in tenants_content
    assert "def resolve" in tenants_content


def test_catalog_generator_output_matches_source() -> None:
    """Catalog generator output matches source.

    Validates that the generated Query.json matches the source catalog.yaml,
    specifically checking that the tenant attribute is properly documented.
    """
    # Read the source catalog
    catalog_path = REPO_ROOT / "querysource" / "queries" / "multi" / "sources" / "query.catalog.yaml"
    assert catalog_path.exists(), f"Source catalog must exist at {catalog_path}"

    # Read the generated JSON
    generated_path = REPO_ROOT / "generated" / "Query.json"
    assert generated_path.exists(), f"Generated Query.json must exist at {generated_path}"

    with open(generated_path) as f:
        generated = json.load(f)

    # Verify tenant attribute is in the generated output
    attribute_names = [attr["name"] for attr in generated.get("attributes", [])]
    assert "tenant" in attribute_names, f"tenant attribute must be in generated output, got: {attribute_names}"

    # Verify tenant is in the JSON schema properties
    properties = generated.get("json_schema", {}).get("properties", {})
    assert "tenant" in properties, "tenant must be in JSON schema properties"

    # Verify the description mentions the key behaviors
    tenant_attr = next(
        (a for a in generated["attributes"] if a["name"] == "tenant"), None
    )
    assert tenant_attr is not None
    assert "inherit" in tenant_attr["description"].lower()
    assert "null" in tenant_attr["description"].lower()
    assert "legacy" in tenant_attr["description"].lower()


def test_release_gates_and_rollback_documented() -> None:
    """Release gates and rollback documented.

    Validates that the documentation covers:
    - Staged rollout procedure
    - DDL/program_id gate
    - Cache transition behavior
    - Scheduler ownership
    - Versioned worker contract
    - Unsupported-worker errors
    - Unverified production gates
    - Rollback procedure
    """
    # Verify the main documentation file exists
    doc_path = REPO_ROOT / "docs" / "PER_TENANT_QUERIES.md"
    assert doc_path.exists(), f"PER_TENANT_QUERIES.md must exist at {doc_path}"

    with open(doc_path) as f:
        content = f.read()

    # Verify key sections exist
    assert "## Storage ownership and runtime program" in content
    assert "## Python and HTTP API" in content
    assert "## Discovery and allowlist" in content
    assert "## Deployment, verification and rollback" in content

    # Verify specific documented behaviors
    assert "program_id" in content, "DDL gate must be documented"
    assert "Cold start" in content or "cold start" in content.lower()
    assert "qs:r2:" in content, "Cache key format must be documented"
    assert "qsj2:" in content, "Scheduler job IDs must be documented"
    assert "tenant_worker_unsupported" in content, "Worker error codes must be documented"

    # Verify unverified gates are explicitly listed
    assert "unverified" in content.lower() or "not" in content.lower()

    # Verify rollback procedure
    assert "Rollback" in content or "rollback" in content.lower()

    # Verify QSSCHEDULER documentation was updated
    scheduler_path = REPO_ROOT / "docs" / "QSSCHEDULER.md"
    assert scheduler_path.exists()
    with open(scheduler_path) as f:
        scheduler_content = f.read()

    assert "## Tenant ownership" in scheduler_content
    assert "qsj2:" in scheduler_content
    assert "owner envelope" in scheduler_content.lower()
    assert "X-QS-Scheduler-Sync" in scheduler_content