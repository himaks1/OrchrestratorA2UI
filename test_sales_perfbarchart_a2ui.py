import unittest
from google.genai.types import Part
from subagent_sales_perfbarchart import a2ui_converter, my_catalog

class TestSalesPerfBarChartA2UI(unittest.TestCase):
    def test_bargraph_output_conversion(self):
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

if __name__ == '__main__':
    unittest.main()
