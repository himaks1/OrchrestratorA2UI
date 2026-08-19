import unittest
from google.genai.types import Part
from subagent_sales_performance_analyst import a2ui_converter, my_catalog

class TestSalesPerformanceA2UI(unittest.TestCase):
    def test_chart_output_contains_inline_catalog(self):
        text = """
<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "Sales_Performance_Analyst/sales_analyst_26891",
    "catalogId": "https://raw.githubusercontent.com/himaks1/OrchrestratorA2UI/main/custom_catalog_definition.json"
  }
}
</a2ui-json>
<a2ui-json>
{
  "version": "v0.9",
  "updateComponents": {
    "surfaceId": "Sales_Performance_Analyst/sales_analyst_26891",
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
          "sales_table",
          "sales_chart"
        ]
      },
      {
        "id": "sales_table",
        "component": "DataTable",
        "title": "Sales Table",
        "columns": [
          {"key": "segment", "label": "Segment", "type": "string"},
          {"key": "revenue", "label": "Revenue", "type": "currency"}
        ],
        "rows": [
          {"segment": "Enterprise", "revenue": 1000000}
        ]
      },
      {
        "id": "sales_chart",
        "component": "Chart",
        "type": "bar",
        "title": "Q3 2026 Sales by Product Line",
        "chartData": [
          {
            "label": "Total B2B Revenues",
            "value": 100.0
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
        self.assertIn("surfaceId", data["createSurface"])
        self.assertIn("catalogId", data["createSurface"])
        
        # Verify updateComponents contains the Chart and DataTable components
        update_components_part = result[1]
        data2 = update_components_part.root.data
        self.assertIn("updateComponents", data2)
        
        components = data2["updateComponents"]["components"]
        chart_component = next((c for c in components if c.get("component") == "Chart"), None)
        self.assertIsNotNone(chart_component)
        self.assertEqual(chart_component["type"], "bar")

        table_component = next((c for c in components if c.get("component") == "DataTable"), None)
        self.assertIsNotNone(table_component)

if __name__ == '__main__':
    unittest.main()

