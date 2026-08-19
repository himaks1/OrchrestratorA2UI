import logging
from a2ui.schema.constants import VERSION_0_9
from a2ui.schema.catalog import CatalogConfig
from a2ui.inference_formats.direct_json import DirectJsonFormat
from a2ui.adk.a2a.part_converter import A2uiPartConverter
from a2ui.schema.common_modifiers import remove_strict_validation

def test_custom_catalog():
    print("Testing custom catalog loading...")
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
    print("Catalog loaded successfully!")
    print(f"Catalog Name: {selected_catalog.name if hasattr(selected_catalog, 'name') else 'custom_catalog'}")

if __name__ == "__main__":
    test_custom_catalog()
