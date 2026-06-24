import json
import xml.etree.ElementTree as ET

import pytest
from app.api.schemas import ChartSpec
from app.pipeline.splunk_chart_export import build_exports


def _spec(chart_type: str, **kw) -> ChartSpec:
    base = dict(
        chart_type=chart_type,
        x_field="_time",
        y_fields=["count"],
        series_field=None,
        title="Test Chart",
        confidence=1.0,
    )
    base.update(kw)
    return ChartSpec(**base)


SPL = "search index=main | timechart count"


@pytest.mark.parametrize(
    "chart_type,expected_charting",
    [
        ("line", "line"),
        ("area", "area"),
        ("bar", "bar"),
        ("column", "column"),
        ("stacked_bar", "column"),
        ("pie", "pie"),
        ("scatter", "scatter"),
        ("bubble", "bubble"),
        ("gauge", "radialGauge"),
    ],
)
def test_simple_xml_chart_types(chart_type, expected_charting):
    export = build_exports(SPL, _spec(chart_type))
    root = ET.fromstring(export.simple_xml)
    assert root.tag == "dashboard"
    options = {
        o.get("name"): o.text
        for o in root.iter("option")
    }
    assert options.get("charting.chart") == expected_charting
    # SPL is embedded in the panel search
    assert SPL in export.simple_xml


def test_simple_xml_stacked_mode():
    export = build_exports(SPL, _spec("stacked_bar"))
    assert 'name="charting.chart.stackMode">stacked' in export.simple_xml


def test_simple_xml_single_value_uses_single_element():
    export = build_exports(SPL, _spec("single_value", x_field=None))
    root = ET.fromstring(export.simple_xml)
    assert root.find(".//single") is not None
    assert root.find(".//chart") is None


def test_simple_xml_heatmap_degrades_to_table_with_note():
    export = build_exports(SPL, _spec("heatmap"))
    root = ET.fromstring(export.simple_xml)
    assert root.find(".//table") is not None
    assert any("heatmap" in n.lower() for n in export.notes)


@pytest.mark.parametrize(
    "chart_type,viz_type",
    [
        ("line", "splunk.line"),
        ("area", "splunk.area"),
        ("bar", "splunk.bar"),
        ("column", "splunk.column"),
        ("stacked_bar", "splunk.column"),
        ("pie", "splunk.pie"),
        ("scatter", "splunk.scatter"),
        ("bubble", "splunk.bubble"),
        ("single_value", "splunk.singlevalue"),
        ("gauge", "splunk.fillergauge"),
        ("heatmap", "splunk.table"),
    ],
)
def test_studio_json_viz_types(chart_type, viz_type):
    export = build_exports(SPL, _spec(chart_type))
    doc = json.loads(export.studio_json)
    vizzes = doc["visualizations"]
    assert len(vizzes) == 1
    viz = next(iter(vizzes.values()))
    assert viz["type"] == viz_type
    # SPL embedded in the data source
    ds = next(iter(doc["dataSources"].values()))
    assert ds["options"]["query"] == SPL


def test_studio_json_stacked_option():
    export = build_exports(SPL, _spec("stacked_bar"))
    doc = json.loads(export.studio_json)
    viz = next(iter(doc["visualizations"].values()))
    assert viz["options"]["stackMode"] == "stacked"


def test_xml_escapes_special_chars_in_spl():
    spl = 'search index=main foo="a<b>&c" | stats count'
    export = build_exports(spl, _spec("bar"))
    # Must remain well-formed XML despite special chars
    ET.fromstring(export.simple_xml)
    # JSON round-trips the raw query verbatim
    doc = json.loads(export.studio_json)
    ds = next(iter(doc["dataSources"].values()))
    assert ds["options"]["query"] == spl
