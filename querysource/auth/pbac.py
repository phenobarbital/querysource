"""
querysource.auth.pbac — PBAC bootstrap for QuerySource.

Initialises navigator-auth's PBAC engine (PDP / PolicyEvaluator / Guardian)
and registers them on the aiohttp app.

IMPORTANT: This module must NOT be imported by the module loader at startup
when QS_PBAC_ENABLED=False. All navigator-auth imports happen lazily, inside
``setup_pbac()`` itself.

See FEAT-091 (pbac-support) spec §2 for the bootstrap design.
"""
from __future__ import annotations

import logging
from typing import Optional, TYPE_CHECKING

from aiohttp import web

from querysource.auth.credentials import CredentialResolver

if TYPE_CHECKING:
    from navigator_auth.abac.pdp import PDP
    from navigator_auth.abac.guardian import Guardian
    from navigator_auth.abac.policies.evaluator import PolicyEvaluator


def setup_pbac(
    app: web.Application,
    policy_dir: str = "policies",
    cache_ttl: int = 300,
) -> "tuple[Optional[PDP], Optional[PolicyEvaluator], Optional[Guardian]]":
    """Initialise navigator-auth PBAC and register it on the aiohttp app.

    Side effects (only when initialisation succeeds):
      ``app['security']``           = Guardian instance
      ``app['abac']``               = PDP instance
      ``app['policy_evaluator']``   = PolicyEvaluator instance
      ``app['credential_resolver']``= CredentialResolver instance

    Idempotent: if ``app['security']`` is already populated by a parent
    stack (e.g. navigator-api invoked ``PDP.setup(app)`` first), QuerySource
    reuses the existing instances instead of recreating them.

    NOTE: This function does NOT call ``pdp.setup(app)``.  That method
    installs navigator-auth's ABAC middleware and REST endpoints which the
    spec explicitly excludes from QuerySource — handlers enforce directly.

    Args:
        app: The aiohttp ``web.Application``.
        policy_dir: Path to directory containing ``*.yaml`` policy files.
        cache_ttl: Policy-decision cache TTL in seconds (default 300).

    Returns:
        ``(pdp, evaluator, guardian)`` — all non-None on success.
        Returns ``(None, None, None)`` on any error (logs but does not raise).
    """
    _log = logging.getLogger("querysource.auth.pbac")

    # ── Idempotency check ──────────────────────────────────────────────────
    existing_guardian = app.get("security")
    existing_evaluator = app.get("policy_evaluator")
    if existing_guardian is not None and existing_evaluator is not None:
        _log.info("PBAC: reusing pre-existing Guardian/PolicyEvaluator from app")
        # Still ensure credential_resolver is registered (QS-specific).
        if "credential_resolver" not in app:
            app["credential_resolver"] = CredentialResolver(logger=_log)
        return (app.get("abac"), existing_evaluator, existing_guardian)

    # ── Lazy navigator-auth imports ────────────────────────────────────────
    try:
        from navigator_auth.abac.pdp import PDP
        from navigator_auth.abac.guardian import Guardian
        from navigator_auth.abac.policies.evaluator import PolicyLoader
        from navigator_auth.abac.storages.yaml_storage import YAMLStorage
    except ImportError as exc:
        _log.error("PBAC bootstrap failed: navigator-auth not importable: %s", exc)
        return (None, None, None)

    # ── Load policies from YAML directory ─────────────────────────────────
    try:
        from pathlib import Path
        policy_path = Path(policy_dir)

        # Create YAMLStorage (PDP uses it for reload hot-path)
        storage = YAMLStorage(directory=str(policy_path))

        # Load policies synchronously using PolicyLoader
        policies = PolicyLoader.load_from_directory(policy_path)
    except Exception as exc:
        _log.error("PBAC bootstrap failed during policy load: %s", exc)
        return (None, None, None)

    # ── Build PDP + PolicyEvaluator ────────────────────────────────────────
    try:
        # PDP creates its own internal PolicyEvaluator; we set up externally too.
        pdp = PDP(storage=storage, policies=policies)

        # Use the PDP's public ``evaluator`` property (returns the same internal
        # PolicyEvaluator the PDP uses internally). Falls back to the private
        # ``_evaluator`` attribute for older navigator-auth versions where the
        # property did not yet exist.
        evaluator = getattr(pdp, "evaluator", None)
        if evaluator is None:
            evaluator = pdp._evaluator  # noqa: SLF001

        # Set TTL on the evaluator. The real attribute used by the cache lookup
        # is ``_cache_ttl`` (see PolicyEvaluator.__init__). We guard with
        # ``hasattr`` so a future rename in navigator-auth raises a loud warning
        # instead of silently no-op'ing the cache TTL setting.
        if hasattr(evaluator, "_cache_ttl"):
            evaluator._cache_ttl = cache_ttl  # noqa: SLF001
        else:
            _log.warning(
                "PBAC: PolicyEvaluator has no '_cache_ttl' attribute; "
                "cache TTL setting (%ds) will be ignored. Verify navigator-auth "
                "version compatibility.",
                cache_ttl,
            )
        # NOTE: do NOT set evaluator._default_effect to a PolicyEffect enum here.
        # The Rust-backed evaluator requires a string, not a Python enum; the
        # evaluator already defaults to "deny" via its own internal constant.
        # Setting it to PolicyEffect.DENY causes a PyO3 type error at evaluation
        # time: "argument 'default_effect': 'PolicyEffect' object cannot be
        # converted to 'PyString'".

        # Load the policies into the evaluator (PDP.__init__ may not do it if
        # policies were passed as a list — call explicitly to be safe).
        if policies:
            evaluator.load_policies(policies)

        guardian = Guardian(pdp=pdp)
    except Exception as exc:
        _log.error("PBAC bootstrap failed building PDP/Guardian: %s", exc)
        return (None, None, None)

    # ── Register on app ───────────────────────────────────────────────────
    app["security"] = guardian
    app["abac"] = pdp
    app["policy_evaluator"] = evaluator
    app["credential_resolver"] = CredentialResolver(logger=_log)

    _log.info(
        "PBAC enabled: %d policies loaded from %s, cache_ttl=%ds",
        len(policies) if policies else 0,
        policy_dir,
        cache_ttl,
    )
    return (pdp, evaluator, guardian)
