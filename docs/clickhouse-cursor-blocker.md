<!-- SPDX-License-Identifier: Apache-2.0 -->

# Resolved regression: ClickHouse FixedString cursor boundary

Observability 1.0.3 requires test adapter `meridian-storage-clickhouse>=1.1.1,<2`.
The owning adapter fixed this regression in its public 1.1.1 release
([PR #14](https://github.com/zephytiju/MeridianClickHouseAdapter/pull/14)).
The original 1.1.0 failure is documented below; the deterministic regression
remains a required gate for every selected Python/server combination.

## Reproduction

Install the hash lock and candidate normally as in the README, start its
ClickHouse 25.8 digest-pinned fixture, then run:

```console
CLICKHOUSE_SELECTED_RELEASE=25.8 .venv/bin/pytest tests/test_real_telemetry.py
```

Each test appends three normalized records, retries that append, then requests
two cursor pages of size two through the plugin's ordinary log/span/metric
helper and the installed public adapter. The second page must contain only the
third record. With adapter 1.1.0, actual behavior repeats the second record and returns two rows.
The regression chooses a legal payload whose canonical row fingerprint begins
with `f`, making the original intermittently observed failure deterministic.
No production implementation or dependency bytes are modified by this fixture.

## Cause and ownership

The ClickHouse table stores `_meridian_row_fingerprint` as `FixedString(64)`.
The released driver returns its hidden sort alias as bytes. In ClickHouse
adapter 1.1.0 `query/compiler.py`, `normalize_result()` calls `_json_value()` on
that hidden sort value; bytes become Base64. The next `compile_simple_query()`
passes that Base64 string directly to the keyset comparison of the raw hash.
A raw value beginning with `f` compares greater than the encoded boundary,
causing the previous row to reappear. Query signatures and plan validation do
not catch a semantically corrupted sort value.

The correction belongs to **MeridianClickHouseAdapter**, not Observability.
Keep generic logical bytes serialization separate from the physical cursor
sort representation; retain signatures, scope/schema/plan/page-size checks,
real-engine pagination regressions and all existing adapter acceptance gates.
Adapter 1.1.1 preserves the signed format and handles both historical physical
cursor encodings. The refreshed validation lock consumes that public release.
The complete required regression matrix still gates merge and publication;
untested combinations remain unverified.
