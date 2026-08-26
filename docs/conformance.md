<!-- SPDX-License-Identifier: Apache-2.0 -->

# Conformance

CI performs the following deterministic checks on Python 3.12, 3.13, and 3.14:

1. formatting, lint, strict type checking, and an import smoke test;
2. public plugin manifest, compatibility ledger, and JSON Schema validation;
3. unit tests for resource/context/redaction/cardinality/provider lifecycle;
4. released Evidence bridge and parameterized ClickHouse query compilation;
5. global-provider conflict behavior in an isolated interpreter;
6. at least 85% branch-aware source coverage;
7. dependency vulnerability audit, wheel/sdist construction, metadata checks,
   archive-content checks, and artifact hashes.

`scripts/verify_contracts.py` prints a canonical JSON result whose fingerprint
is suitable for the task evidence ledger. `scripts/verify_artifacts.py dist`
does the same for built distributions. Neither verifier reads sibling source.
