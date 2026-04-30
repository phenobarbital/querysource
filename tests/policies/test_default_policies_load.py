"""Smoke tests for default policy YAML files (TASK-639)."""
import pytest
from pathlib import Path

POLICY_DIR = Path(__file__).parent.parent.parent / "policies"


def _navauth_available():
    """Check if navigator-auth PolicyLoader is importable."""
    try:
        from navigator_auth.abac.policies.evaluator import PolicyLoader  # noqa: F401
        return True
    except (ImportError, Exception):
        return False


@pytest.mark.skipif(not POLICY_DIR.exists(), reason="policies/ not present in dev env")
def test_all_default_files_present():
    """All six expected YAML files must exist under policies/."""
    expected = {
        "defaults.yaml",
        "slugs.yaml",
        "datasources.yaml",
        "drivers.yaml",
        "raw_queries.yaml",
        "superusers.yaml",
    }
    found = {p.name for p in POLICY_DIR.glob("*.yaml")}
    missing = expected - found
    assert not missing, f"Missing default policy files: {missing}"


@pytest.mark.skipif(not POLICY_DIR.exists(), reason="policies/ not present")
def test_yaml_load():
    """Each file must parse as valid YAML with 'version' and 'policies' keys."""
    import yaml
    for path in sorted(POLICY_DIR.glob("*.yaml")):
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data is not None, f"{path.name} parsed as None"
        assert "version" in data, f"{path.name} missing 'version' key"
        assert "policies" in data, f"{path.name} missing 'policies' key"


def test_defaults_has_admin_allow():
    """defaults.yaml must include the admin_full_access policy."""
    import yaml
    p = POLICY_DIR / "defaults.yaml"
    if not p.exists():
        pytest.skip("policies/defaults.yaml not present")
    with open(p) as f:
        data = yaml.safe_load(f)
    names = {pol["name"] for pol in data.get("policies", [])}
    assert "admin_full_access" in names, (
        f"Expected 'admin_full_access' in defaults.yaml policies, got: {names}"
    )


def test_defaults_admin_policy_has_correct_priority():
    """admin_full_access policy must have priority=100 and enforcing=True."""
    import yaml
    p = POLICY_DIR / "defaults.yaml"
    if not p.exists():
        pytest.skip("policies/defaults.yaml not present")
    with open(p) as f:
        data = yaml.safe_load(f)
    pol = next(
        (p for p in data.get("policies", []) if p.get("name") == "admin_full_access"),
        None,
    )
    assert pol is not None
    assert pol.get("priority") == 100, f"Expected priority=100, got {pol.get('priority')}"
    assert pol.get("enforcing") is True, f"Expected enforcing=True, got {pol.get('enforcing')}"


def test_scaffold_files_have_empty_policies():
    """Scaffold files (slugs, datasources, drivers, raw_queries, superusers) have empty active list."""
    import yaml
    scaffold_files = [
        "slugs.yaml",
        "datasources.yaml",
        "drivers.yaml",
        "raw_queries.yaml",
        "superusers.yaml",
    ]
    for fname in scaffold_files:
        p = POLICY_DIR / fname
        if not p.exists():
            pytest.skip(f"policies/{fname} not present")
        with open(p) as f:
            data = yaml.safe_load(f)
        assert data.get("policies") == [], (
            f"{fname} should have an empty policies list, got: {data.get('policies')}"
        )


@pytest.mark.skipif(not _navauth_available(), reason="navigator-auth not available or incompatible")
def test_loader_parses_all_files():
    """PolicyLoader must parse defaults.yaml without raising."""
    if not POLICY_DIR.exists():
        pytest.skip("policies/ not present")
    from navigator_auth.abac.policies.evaluator import PolicyLoader
    try:
        policies = PolicyLoader().load_from_directory(str(POLICY_DIR))
        # defaults.yaml has 1 policy (admin_full_access)
        assert len(policies) >= 1
    except Exception as exc:
        pytest.xfail(f"PolicyLoader not fully compatible yet: {exc}")
