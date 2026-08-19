from a2ui.schema.catalog import CatalogConfig
from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.constants import VERSION_0_9
from a2ui.inference_formats.direct_json import DirectJsonFormat
from a2ui.schema.common_modifiers import remove_strict_validation
from a2ui.adk.a2a.part_converter import A2uiPartConverter
from google.genai.types import Part

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
a2ui_converter = A2uiPartConverter(a2ui_catalog=my_catalog, version=VERSION_0_9)

def wrapper_convert(part: Part):
    parts = a2ui_converter.convert(part)
    for p in parts:
        if getattr(p, 'root', None) and getattr(p.root, 'data', None) and isinstance(p.root.data, dict):
            if 'createSurface' in p.root.data:
                p.root.data['createSurface']['inlineCatalog'] = [my_catalog.catalog_schema]
    return parts

text = """
<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "Sales_Performance_Analyst/sales_analyst_26891",
    "catalogId": "https://a2ui.org/samples/community/agent/adk/rizzcharts/catalog_schemas/0.9/rizzcharts_catalog_definition.json"
  }
}
</a2ui-json>
"""
print("Parsing text...")
part = Part(text=text)
result = wrapper_convert(part)
print(f"Result parts count: {len(result)}")
if result:
    for r in result:
        print(r)
