import json
from a2ui.schema.constants import VERSION_0_9
from a2ui.schema.catalog import CatalogConfig
from a2ui.inference_formats.direct_json import DirectJsonFormat
from a2ui.adk.a2a.part_converter import A2uiPartConverter
from a2ui.schema.common_modifiers import remove_strict_validation
from google.genai.types import Part

def test_conversion():
    catalog_config = CatalogConfig.from_path(
        name="custom_catalog",
        catalog_path="custom_catalog_definition.json",
        examples_path="examples/custom_catalog/0.9"
    )
    
    inference_format = DirectJsonFormat(
        version=VERSION_0_9,
        catalogs=[catalog_config],
        schema_modifiers=[remove_strict_validation],
    )
    
    selected_catalog = inference_format.get_selected_catalog()
    converter = A2uiPartConverter(a2ui_catalog=selected_catalog, version=VERSION_0_9)

    datatable_sample = {
        "version": "v0.9",
        "updateComponents": {
            "surfaceId": "sales_surface_1",
            "components": [
                {
                    "id": "root",
                    "component": "Card",
                    "child": "col_1"
                },
                {
                    "id": "col_1",
                    "component": "Column",
                    "children": ["my_table", "my_chart"]
                },
                {
                    "id": "my_table",
                    "component": "DataTable",
                    "title": "Sales Table",
                    "columns": [
                        {"key": "segment", "label": "Segment", "type": "string"},
                        {"key": "revenue", "label": "Revenue", "type": "currency"}
                    ],
                    "rows": [
                        {"segment": "Enterprise", "revenue": 1000000},
                        {"segment": "SMB", "revenue": 250000}
                    ]
                },
                {
                    "id": "my_chart",
                    "component": "Chart",
                    "type": "bar",
                    "title": "Revenue by Segment",
                    "chartData": [
                        {"label": "Enterprise", "value": 1000000},
                        {"label": "SMB", "value": 250000}
                    ]
                }
            ]
        }
    }

    raw_text = f"<a2ui-json>\n{json.dumps(datatable_sample)}\n</a2ui-json>"
    part = Part.from_text(text=raw_text)
    converted_part = converter.convert(part)
    print("Conversion result:", type(converted_part), converted_part)
    print("Test passed successfully!")

if __name__ == "__main__":
    test_conversion()
