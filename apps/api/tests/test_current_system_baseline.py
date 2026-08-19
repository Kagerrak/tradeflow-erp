"""Validate the current-system baseline inventory and success-measure definitions."""

from pathlib import Path
from typing import Any

import pytest
import yaml

BASELINE_PATH = Path(__file__).parents[3] / "docs" / "delivery" / "current-system-baseline.yml"


@pytest.fixture(scope="module")
def baseline() -> dict[str, Any]:
    assert BASELINE_PATH.is_file(), f"Baseline file not found: {BASELINE_PATH}"
    with BASELINE_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@pytest.fixture(scope="module")
def inventory_items(baseline: dict[str, Any]) -> list[dict[str, Any]]:
    return baseline.get("inventory", [])


class TestBaselineStructure:
    def test_required_top_level_keys(self, baseline: dict[str, Any]) -> None:
        required = {
            "baseline_version",
            "issue",
            "branch",
            "base_commit",
            "classifications",
            "inventory",
            "high_frequency_workflows",
            "success_measures",
        }
        missing = required - baseline.keys()
        assert not missing, f"Missing top-level keys: {missing}"

    def test_classifications_defined(self, baseline: dict[str, Any]) -> None:
        classifications = baseline["classifications"]
        for key in ("implemented_and_verified", "retired_with_approval", "temporary_bridge"):
            assert key in classifications, f"Classification {key} missing"
            assert "required_fields" in classifications[key]


class TestInventory:
    def test_inventory_items_have_required_fields(
        self, inventory_items: list[dict[str, Any]]
    ) -> None:
        required = {"id", "category", "name", "classification"}
        for item in inventory_items:
            missing = required - item.keys()
            assert not missing, f"Item {item.get('id')} missing fields: {missing}"

    def test_inventory_ids_are_unique(self, inventory_items: list[dict[str, Any]]) -> None:
        ids = [item["id"] for item in inventory_items]
        assert len(ids) == len(set(ids)), f"Duplicate inventory IDs: {ids}"

    def test_classifications_are_valid(
        self,
        baseline: dict[str, Any],
        inventory_items: list[dict[str, Any]],
    ) -> None:
        allowed = set(baseline["classifications"].keys())
        for item in inventory_items:
            classification = item["classification"]
            assert classification in allowed, (
                f"Invalid classification {classification!r} for {item['id']}"
            )

    def test_classification_specific_fields_present(
        self,
        baseline: dict[str, Any],
        inventory_items: list[dict[str, Any]],
    ) -> None:
        for item in inventory_items:
            classification = item["classification"]
            required = set(baseline["classifications"][classification]["required_fields"])
            missing = required - item.keys()
            assert not missing, f"{item['id']} classification {classification} missing: {missing}"
            for field in required:
                value = item[field]
                assert value is not None and str(value).strip(), (
                    f"{item['id']} field {field} is empty"
                )

    def test_no_unclassified_items(self, inventory_items: list[dict[str, Any]]) -> None:
        for item in inventory_items:
            assert item["classification"] not in (None, ""), f"{item['id']} has no classification"

    def test_inventory_covers_required_categories(
        self, inventory_items: list[dict[str, Any]]
    ) -> None:
        required_categories = {
            "workflow",
            "screen",
            "report",
            "role",
            "integration",
            "export",
            "data_store",
        }
        found = {item["category"] for item in inventory_items}
        missing = required_categories - found
        assert not missing, f"Missing inventory categories: {missing}"


class TestSuccessMeasures:
    def test_success_measures_required_fields(self, baseline: dict[str, Any]) -> None:
        required = {
            "id",
            "name",
            "source",
            "formula",
            "window",
            "baseline",
            "target",
            "owner",
            "missing_data_behavior",
        }
        measures = baseline.get("success_measures", [])
        assert measures, "No success measures defined"
        ids = []
        for measure in measures:
            missing = required - measure.keys()
            assert not missing, f"Measure {measure.get('id')} missing fields: {missing}"
            ids.append(measure["id"])
        assert len(ids) == len(set(ids)), f"Duplicate measure IDs: {ids}"


class TestHighFrequencyWorkflows:
    def test_high_frequency_workflows(self, baseline: dict[str, Any]) -> None:
        workflows = baseline.get("high_frequency_workflows", [])
        assert workflows, "No high-frequency workflows recorded"
        ids = []
        for workflow in workflows:
            assert "id" in workflow
            assert "name" in workflow
            assert "owner" in workflow
            assert "sampling_window" in workflow
            assert "metrics" in workflow and workflow["metrics"]
            metric_names = {m["metric"] for m in workflow["metrics"]}
            assert any(
                name in metric_names
                for name in ("task_time_minutes", "task_time_hours", "cycle_time_hours")
            ), f"{workflow['id']} missing task-time metric"
            assert any(
                name in metric_names
                for name in (
                    "correction_rate_percent",
                    "exception_rate_percent",
                    "unmatched_rate_percent",
                )
            ), f"{workflow['id']} missing error/correction metric"
            ids.append(workflow["id"])
        assert len(ids) == len(set(ids)), f"Duplicate workflow IDs: {ids}"
