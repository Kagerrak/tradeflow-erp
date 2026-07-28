from __future__ import annotations

import os
from uuid import uuid4

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TRADEFLOW_REAL_STACK") != "1",
    reason="Runs only against the migrated real-stack acceptance environment.",
)


def auth(token: str, **headers: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", **headers}


def test_external_catalog_and_inventory_contract() -> None:
    base_url = os.environ.get("TRADEFLOW_REAL_STACK_API_URL", "http://127.0.0.1:8000")
    inventory_token = os.environ["TRADEFLOW_REAL_STACK_SALES_TOKEN"]
    with httpx.Client(base_url=base_url, timeout=10) as client:
        scope = client.get("/v1/organization/scope", headers=auth(inventory_token))
        assert scope.status_code == 200
        warehouse_id = scope.json()["warehouses"][0]["warehouse_id"]

        configured = client.post(
            "/v1/catalog/skus",
            headers=auth(
                inventory_token,
                **{"Idempotency-Key": f"real-stack-sku-{uuid4()}"},
            ),
            json={
                "product_code": "REAL-BEV",
                "product_name": "Real Stack Beverages",
                "sku_code": "REAL-COLA-330",
                "sku_name": "Real Stack Cola 330 mL",
                "base_stocking_unit": "EA",
                "tracking_policy": "lot",
                "expiration_control": True,
                "conversions": [
                    {
                        "unit_code": "CASE",
                        "base_quantity": "12.000000",
                        "effective_from": "2026-01-01",
                        "effective_to": None,
                    }
                ],
                "barcodes": [{"barcode": f"REAL-{uuid4()}", "unit_code": "CASE"}],
            },
        )
        assert configured.status_code == 201, configured.text
        location = client.post(
            "/v1/inventory/locations",
            headers=auth(
                inventory_token,
                **{"Idempotency-Key": f"real-stack-location-{uuid4()}"},
            ),
            json={
                "warehouse_id": warehouse_id,
                "code": "REAL-AVAILABLE",
                "name": "Real Stack Available",
                "custody": "available",
            },
        )
        assert location.status_code == 201, location.text
        command = {
            "sku_id": configured.json()["sku_id"],
            "warehouse_id": warehouse_id,
            "location_id": location.json()["location_id"],
            "quantity": "2.500000",
            "unit_code": "CASE",
            "unit_cost": "10.000000",
            "source_reference": "REAL-OPENING-001",
            "lot_code": "REAL-LOT-A",
            "serial_numbers": [],
            "expiration_date": "2027-12-31",
        }
        key = f"real-opening-{uuid4()}"
        posted = client.post(
            "/v1/inventory/opening-stock",
            headers=auth(inventory_token, **{"Idempotency-Key": key}),
            json=command,
        )
        replay = client.post(
            "/v1/inventory/opening-stock",
            headers=auth(inventory_token, **{"Idempotency-Key": key}),
            json=command,
        )
        assert posted.status_code == 201, posted.text
        assert replay.status_code == 200
        assert replay.json() == posted.json()
        assert posted.json()["quantity_base"] == "30.000000"

        availability = client.get(
            "/v1/inventory/availability?query=REAL-COLA",
            headers=auth(inventory_token),
        )
        assert availability.status_code == 200
        item = availability.json()["items"][0]
        assert item["on_hand"] == "30.000000"
        assert item["available"] == "30.000000"
        assert item["reserved"] == "0.000000"
        assert item["lot_code"] == "REAL-LOT-A"
        assert item["expiration_date"] == "2027-12-31"

        rebuilt = client.post(
            "/v1/inventory/projections/rebuild",
            headers=auth(inventory_token),
        )
        assert rebuilt.status_code == 200
        rebuilt_availability = client.get(
            "/v1/inventory/availability?query=REAL-COLA",
            headers=auth(inventory_token),
        )
        assert rebuilt_availability.json() == availability.json()
