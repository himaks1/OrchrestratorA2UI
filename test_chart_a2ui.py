import json
from a2ui.schema.catalog import CatalogConfig
from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.constants import VERSION_0_9
from a2ui.inference_formats.direct_json import DirectJsonFormat
from a2ui.schema.common_modifiers import remove_strict_validation

inference_format = DirectJsonFormat(
    version=VERSION_0_9,
    catalogs=[
        CatalogConfig.from_path(
            name="rizzcharts",
            catalog_path="rizzcharts_catalog_definition.json",
            examples_path="examples/rizzcharts_catalog/0.9"
        ),
        BasicCatalog.get_config(version=VERSION_0_9)
    ],
    schema_modifiers=[remove_strict_validation],
)
my_catalog = inference_format.get_selected_catalog()

# Sample JSON from the Cloud Run logs
a2ui_json = {
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "Sales_Performance_Analyst/sales_analyst_26891",
    "catalogId": "https://a2ui.org/samples/community/agent/adk/rizzcharts/catalog_schemas/0.9/rizzcharts_catalog_definition.json"
  },
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
          "title",
          "sales_chart",
          "note_text"
        ]
      },
      {
        "id": "title",
        "component": "Text",
        "text": "### Q3 2026 Sales Breakdown by Product Category"
      },
      {
        "id": "sales_chart",
        "component": "Chart",
        "type": "pie",
        "title": "Q3 2026 Sales by Product Line",
        "chartData": [
          {
            "label": "Total B2B PCO Revenues",
            "value": 0.0
          },
          {
            "label": "Non-GPU Revenues",
            "value": 0.0
          }
        ]
      },
      {
        "id": "note_text",
        "component": "Text",
        "text": "No sales data recorded."
      }
    ]
  }
}

print("Validating JSON against catalog...")
try:
    # A2uiCatalog provides a validator
    my_catalog.validator.validate(a2ui_json)
    print("Validation SUCCESSFUL!")
except Exception as e:
    print(f"Validation FAILED: {e}")
