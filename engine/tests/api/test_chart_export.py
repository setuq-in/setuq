import json
import os
import xml.etree.ElementTree as ET

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

os.environ.pop("API_KEY", None)


def _payload(chart_type="line"):
    return {
        "spl": "search index=main | timechart count",
        "chart_spec": {
            "chart_type": chart_type,
            "x_field": "_time",
            "y_fields": ["count"],
            "series_field": None,
            "title": "Events over time",
            "confidence": 1.0,
        },
    }


@pytest.mark.asyncio
async def test_chart_export_returns_both_formats():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/chart/export", json=_payload("line"))
    assert resp.status_code == 200
    body = resp.json()
    # Simple XML is well-formed and carries the chart option
    root = ET.fromstring(body["simple_xml"])
    assert root.tag == "dashboard"
    # Studio JSON parses and has one line visualization
    doc = json.loads(body["studio_json"])
    viz = next(iter(doc["visualizations"].values()))
    assert viz["type"] == "splunk.line"


@pytest.mark.asyncio
async def test_chart_export_heatmap_includes_note():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/chart/export", json=_payload("heatmap"))
    assert resp.status_code == 200
    assert any("heatmap" in n.lower() for n in resp.json()["notes"])
