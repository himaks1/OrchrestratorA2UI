import unittest
from google.genai.types import Part
from subagent_sales_perfbarchart import a2ui_converter, my_catalog

class TestSalesPerfBarChartA2UI(unittest.TestCase):

    def test_bargraph_output_conversion(self):
        """Test parsing and conversion of BarGraph A2UI surface payload."""
        text = """
<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "subagent_sales_perfbarchart/sales_bargraph_26891",
    "catalogId": "https://a2ui.org/catalogs/bargraph/0.9/bargraph_catalog_definition.json"
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
          "sales_bargraph"
        ]
      },
      {
        "id": "title",
        "component": "Text",
        "text": "### Revenue by Division",
        "variant": "h3"
      },
      {
        "id": "sales_bargraph",
        "component": "BarGraph",
        "title": "Revenue Performance",
        "orientation": "vertical",
        "xAxis": {
          "label": "Division",
          "key": "label"
        },
        "yAxis": {
          "label": "Revenue ($)",
          "key": "value",
          "format": "currency"
        },
        "data": [
          {
            "label": "Enterprise",
            "value": 1200000.0,
            "color": "#1A73E8"
          },
          {
            "label": "Commercial",
            "value": 750000.0,
            "color": "#34A853"
          }
        ]
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
        self.assertEqual(data["createSurface"]["catalogId"], "https://a2ui.org/catalogs/bargraph/0.9/bargraph_catalog_definition.json")
        
        # Verify updateComponents contains the BarGraph component
        update_components_part = result[1]
        data2 = update_components_part.root.data
        self.assertIn("updateComponents", data2)
        
        components = data2["updateComponents"]["components"]
        bargraph_component = next((c for c in components if c.get("component") == "BarGraph"), None)
        self.assertIsNotNone(bargraph_component)
        self.assertEqual(bargraph_component["orientation"], "vertical")
        self.assertEqual(len(bargraph_component["data"]), 2)

    def test_bargraph_interactive_button_action(self):
        """Test drill-down button with event context payload."""
        text = """
<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "subagent_sales_perfbarchart/sales_bargraph_999",
    "catalogId": "https://a2ui.org/catalogs/bargraph/0.9/bargraph_catalog_definition.json"
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
        "children": ["sales_bargraph", "drill_down_btn"]
      },
      {
        "id": "sales_bargraph",
        "component": "BarGraph",
        "title": "Segment Performance",
        "data": [
          {"label": "Enterprise", "value": 500000}
        ]
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
        """Verify that components not defined in bargraph_catalog_definition.json are rejected by converter."""
        invalid_text = """
<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "subagent_sales_perfbarchart/test_invalid",
    "catalogId": "https://a2ui.org/catalogs/bargraph/0.9/bargraph_catalog_definition.json"
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
