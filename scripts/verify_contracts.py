# SPDX-License-Identifier: Apache-2.0
"""Verify the released public contracts without reading sibling source trees."""

from __future__ import annotations

import ast
import hashlib
import json
from importlib import metadata, resources
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

from meridian_storage import __version__ as core_version
from meridian_storage.evidence import __version__ as evidence_version
from meridian_storage.plugins.observability import ObservabilityPluginFactory, __version__
from meridian_storage.query import QUERY_FORMAT_VERSION
from meridian_storage.query import __version__ as query_version
from meridian_storage.semantics import __version__ as semantics_version

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REQUIREMENTS = {
    "meridian-storage-core": "<2,>=1.1.0",
    "meridian-storage-evidence": "<2,>=1.0.2",
    "meridian-storage-query": "<2,>=1.0.3",
    "meridian-storage-semantics": "<3,>=2.0.1",
}
FORBIDDEN_IMPORTS = (
    "clickhouse_connect",
    "meridian_storage.adapters",
    "meridian_storage.spi.adapters",
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AssertionError(f"{path} must contain a JSON object")
    return value


def _distribution_pins() -> dict[str, str]:
    distribution = metadata.distribution("meridian-plugin-observability")
    result: dict[str, str] = {}
    for raw in distribution.requires or ():
        requirement = Requirement(raw)
        if requirement.name in EXPECTED_REQUIREMENTS and requirement.marker is None:
            result[requirement.name] = str(requirement.specifier)
    if result != EXPECTED_REQUIREMENTS:
        raise AssertionError(f"released Meridian requirements differ: {result!r}")
    return result


def _verify_import_boundary() -> int:
    checked = 0
    for path in sorted((ROOT / "src").rglob("*.py")):
        checked += 1
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: tuple[str, ...]
            if isinstance(node, ast.Import):
                names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                names = (node.module,)
            else:
                continue
            if any(name.startswith(FORBIDDEN_IMPORTS) for name in names):
                raise AssertionError(f"consumer source imports an Adapter/Engine module: {path}")
    return checked


def main() -> None:
    compatibility_text = (
        resources.files("meridian_storage.plugins.observability")
        .joinpath("compatibility.json")
        .read_text(encoding="utf-8")
    )
    compatibility = json.loads(compatibility_text)
    schema = _load_json(ROOT / "contracts" / "observability-plugin.v1.json")
    golden_path = ROOT / "contracts" / "conformance" / "golden" / "plugin-manifest.json"
    golden = _load_json(golden_path)
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(golden)

    factory = ObservabilityPluginFactory()
    manifest = factory.manifest()
    _require(manifest.plugin_id == golden["plugin"]["id"], "plugin id differs")
    _require(
        manifest.plugin_version == golden["version"] == __version__,
        "plugin version differs",
    )
    _require(
        manifest.plugin_contract_version == golden["plugin"]["contract"],
        "plugin contract differs",
    )
    _require(manifest.core_contract == golden["plugin"]["core"], "Core range differs")
    _require(compatibility["contracts"]["catalogsOwned"] == [], "plugin must own no Catalog")
    _require(
        compatibility["contracts"]["catalogsUsed"] == ["evidence"],
        "plugin must use only the Evidence Catalog",
    )
    _require(
        compatibility["contracts"]["queryOperation"] == QUERY_FORMAT_VERSION,
        "Query contract differs",
    )
    versions = {
        "meridian-storage-core": core_version,
        "meridian-storage-evidence": evidence_version,
        "meridian-storage-query": query_version,
        "meridian-storage-semantics": semantics_version,
    }
    _require(
        all(versions[name] in SpecifierSet(spec) for name, spec in EXPECTED_REQUIREMENTS.items()),
        "installed Meridian versions violate public API requirements",
    )
    _require(compatibility["version"] == __version__, "compatibility ledger version differs")
    ledger_requirements = {
        entry["package"]: str(SpecifierSet(entry["constraint"]))
        for entry in compatibility["dependencies"]
        if entry["role"] == "runtime"
    }
    _require(ledger_requirements == EXPECTED_REQUIREMENTS, "ledger API requirements differ")
    pins = _distribution_pins()
    checked_source_files = _verify_import_boundary()
    _require(len(tuple(ROOT.glob("pyproject.toml"))) == 1, "repository must have one project")
    _require(
        not (ROOT / "src" / "meridian_storage" / "__init__.py").exists(),
        "distribution must not own the root PEP 420 namespace",
    )
    _require(
        not (ROOT / "src" / "meridian_storage" / "plugins" / "__init__.py").exists(),
        "distribution must not own the plugins PEP 420 namespace",
    )

    evidence = {
        "formatVersion": "meridian.observability.conformance.v1",
        "package": "meridian-plugin-observability",
        "version": __version__,
        "contracts": {
            "goldenSha256": hashlib.sha256(golden_path.read_bytes()).hexdigest(),
            "queryOperation": QUERY_FORMAT_VERSION,
        },
        "installedVersions": versions,
        "pins": pins,
        "sourceFilesChecked": checked_source_files,
        "status": "passed",
    }
    encoded = json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    output = {**evidence, "fingerprint": "sha256:" + hashlib.sha256(encoded).hexdigest()}
    print(json.dumps(output, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
