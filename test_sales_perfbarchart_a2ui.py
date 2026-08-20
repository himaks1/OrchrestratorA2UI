import unittest
from google.genai.types import Part
from subagent_sales_perfbarchart import a2ui_converter, my_catalog

class TestSalesPerfBarChartA2UI(unittest.TestCase):

    def test_bargraph_output_conversion(self):
        """Test parsing and conversion of Basic Catalog A2UI surface payload."""
        text = """
<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "subagent_sales_perfbarchart/sales_bargraph_26891",
    "catalogId": "https://a2ui.org/specification/v0_9/catalogs/charts/chart_catalog.json"
  }
}
</a2ui-json>
<a2ui-json>
{
  "version": "v0.9",
  "updateComponents": {
    "surfaceId": "subagent_sales_perfbarchart/sales_bargraph_26891",
    "components": [
      {
        "id": "root",
        "component": "Card",
        "child": "content_col"
      },
      {
        "id": "content_col",
        "component": "Column",
        "children": [
          "title",
          "sales_bar_chart"
        ]
      },
      {
        "id": "title",
        "component": "Text",
        "text": "### Revenue by Division",
        "variant": "h3"
      },
      {
        "id": "sales_bar_chart",
        "component": "VegaChart",
        "props": {
          "spec": {
            "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
            "description": "Revenue Performance by Division",
            "data": {
              "values": [
                {"division": "Enterprise", "revenue": 1200000},
                {"division": "Commercial", "revenue": 750000}
              ]
            },
            "mark": "bar",
            "encoding": {
              "x": {"field": "division", "type": "nominal"},
              "y": {"field": "revenue", "type": "quantitative"}
            }
          }
        }
      }
    ]
  }
}
</a2ui-json>
"""
        part = Part(text=text)
        result = a2ui_converter.convert(part)
        
        self.assertEqual(len(result), 2)
        
        # Verify createSurface
        create_surface_part = result[0]
        data = create_surface_part.root.data
        self.assertIn("createSurface", data)
        self.assertEqual(data["createSurface"]["catalogId"], "https://a2ui.org/specification/v0_9/catalogs/charts/chart_catalog.json")
        
        # Verify updateComponents contains VegaChart component
        update_components_part = result[1]
        data2 = update_components_part.root.data
        self.assertIn("updateComponents", data2)
        
        components = data2["updateComponents"]["components"]
        chart_component = next((c for c in components if c.get("id") == "sales_bar_chart"), None)
        self.assertIsNotNone(chart_component)
        self.assertEqual(chart_component["component"], "VegaChart")
        self.assertEqual(chart_component["props"]["spec"]["mark"], "bar")

    def test_bargraph_interactive_button_action(self):
        """Test drill-down button with event context payload."""
        text = """
<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "subagent_sales_perfbarchart/sales_bargraph_999",
    "catalogId": "https://a2ui.org/specification/v0_9/catalogs/charts/chart_catalog.json"
  }
}
</a2ui-json>
<a2ui-json>
{
  "version": "v0.9",
  "updateComponents": {
    "surfaceId": "subagent_sales_perfbarchart/sales_bargraph_999",
    "components": [
      {
        "id": "root",
        "component": "Card",
        "child": "content_col"
      },
      {
        "id": "content_col",
        "component": "Column",
        "children": ["title", "drill_down_btn"]
      },
      {
        "id": "title",
        "component": "Text",
        "text": "Segment Performance"
      },
      {
        "id": "drill_down_btn",
        "component": "Button",
        "child": "btn_txt",
        "action": {
          "event": {
            "name": "analyze_sales_performance",
            "context": {
              "query": "Show Enterprise segment breakdown"
            }
          }
        }
      },
      {
        "id": "btn_txt",
        "component": "Text",
        "text": "Analyze Details"
      }
    ]
  }
}
</a2ui-json>
"""
        part = Part(text=text)
        result = a2ui_converter.convert(part)
        self.assertEqual(len(result), 2)
        
        data2 = result[1].root.data
        components = data2["updateComponents"]["components"]
        button = next((c for c in components if c.get("component") == "Button"), None)
        self.assertIsNotNone(button)
        self.assertEqual(button["action"]["event"]["name"], "analyze_sales_performance")
        self.assertEqual(button["action"]["event"]["context"]["query"], "Show Enterprise segment breakdown")

    def test_invalid_component_rejection(self):
        """Verify that invalid component types are rejected by converter."""
        invalid_text = """
<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "subagent_sales_perfbarchart/test_invalid",
    "catalogId": "https://a2ui.org/specification/v0_9/catalogs/charts/chart_catalog.json"
  }
}
</a2ui-json>
<a2ui-json>
{
  "version": "v0.9",
  "updateComponents": {
    "surfaceId": "subagent_sales_perfbarchart/test_invalid",
    "components": [
      {
        "id": "invalid_table",
        "component": "NonExistentTableComponent",
        "title": "Test"
      }
    ]
  }
}
</a2ui-json>
"""
        part = Part(text=invalid_text)
        result = a2ui_converter.convert(part)
        # Invalid component fails schema validation, yielding 0 converted parts
        self.assertEqual(len(result), 0)


if __name__ == '__main__':
    unittest.main()
