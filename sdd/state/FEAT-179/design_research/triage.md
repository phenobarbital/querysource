# FEAT-179 design-research triage (model gpt-5.6-luna, status completed)

| # | Suggestion (kind) | Disposition | Reason | Landed in |
|---|---|---|---|---|
| S1 | One compositional containment-term primitive in both builders (architecture) | CONFIRM | within-builder term reuse via jsonb_operand+pg_literal; @>| byte-identical; no cross-language abstraction possible | §7 Patterns |
| S2 | Replace first-key dispatch with explicit operator collection (architecture) | CONFIRM | exactly the planned multi-operator iteration | §2 Overview |
| S3 | Mixed comparison/JSONB dicts → explicit deterministic rejection (risk) | CONFIRM | real Cython/Rust divergence today; made an explicit dispatch rule + both-path test | §2 Overview, §4, §5 AC |
| S4 | Specify empty-list behavior separately for @! and @$ (api) | CONFIRM | contract pinned: empty/non-list drops the condition (repo convention), docstring + tests; @$ empty is a no-op, not FALSE | §2 Overview, §5 AC |
| S5 | Parenthesize each NOT term and the full OR group (api) | CONFIRM | already the confirmed rendering; rendered-forms table normative | §2 rendered forms |
| S6 | AST assertions for negation grouping and multi-operator composition (testing) | CONFIRM | test_build_query_negation_is_grouped asserts sqlglot nesting incl. @!+@$+scalar | §4 Integration Tests |
| S7 | Fail (not skip) rust-path tests when extension stale/absent in release validation (testing) | ESCALATE | permanent CI gate is a repo-wide decision for the user; feature AC already requires rust cases to execute | §8 Q1 |
| S8 | Golden escaping corpus through full build_query for negated operands (testing) | CONFIRM | quotes+braces+backslashes negated operands added to build_query escaping cases | §4 Integration Tests |

Path verification: all affected_paths (querysource/parsers/pgsql.pyx, rust/src/pgsql_parser.rs, tests/test_pgsql_jsonb_filters.py) are repository-contained and exist (test -e passed).
Summary: 7 confirmed · 0 rejected · 1 escalated.
