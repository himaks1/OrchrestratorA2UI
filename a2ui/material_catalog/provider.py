import copy
from typing import Any, Dict, Optional
from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.catalog import CatalogConfig
from a2ui.schema.catalog_provider import A2uiCatalogProvider

# Component schemas to inject
DATATABLE_SCHEMA = {
    "type": "object",
    "properties": {
        "component": {"const": "DataTable"},
        "title": {"type": "string"},
        "pageSize": {"type": "integer"},
        "sortable": {"type": "boolean"},
        "columns": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "label": {"type": "string"},
                    "type": {"type": "string", "enum": ["string", "number", "currency"]}
                },
                "required": ["key", "label"]
            }
        },
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": True
            }
        }
    },
    "required": ["component", "columns", "rows"]
}

METRIC_CARD_SCHEMA = {
    "type": "object",
    "properties": {
        "component": {"const": "MetricCard"},
        "title": {"type": "string"},
        "value": {"type": "string"},
        "subValue": {"type": "string"},
        "trend": {"type": "string", "enum": ["up", "down", "flat"]},
        "trendValue": {"type": "string"}
    },
    "required": ["component", "title", "value"]
}

VEGA_CHART_SCHEMA = {
    "type": "object",
    "properties": {
        "component": {"const": "VegaChart"},
        "props": {"type": "object"}
    },
    "required": ["component"]
}

def inject_material_components(schema: dict) -> dict:
    new_schema = copy.deepcopy(schema)
    if "components" not in new_schema:
        new_schema["components"] = {}
        
    # Inject components
    new_schema["components"]["DataTable"] = DATATABLE_SCHEMA
    new_schema["components"]["MetricCard"] = METRIC_CARD_SCHEMA
    new_schema["components"]["VegaChart"] = VEGA_CHART_SCHEMA
    
    # Inject into anyComponent
    if "$defs" in new_schema and "anyComponent" in new_schema["$defs"]:
        one_of = new_schema["$defs"]["anyComponent"]["oneOf"]
        # Add if not already present
        if not any(item.get("$ref") == "#/components/DataTable" for item in one_of):
            one_of.append({"$ref": "#/components/DataTable"})
        if not any(item.get("$ref") == "#/components/MetricCard" for item in one_of):
            one_of.append({"$ref": "#/components/MetricCard"})
        if not any(item.get("$ref") == "#/components/VegaChart" for item in one_of):
            one_of.append({"$ref": "#/components/VegaChart"})
            
    return new_schema

class MemoryCatalogProvider(A2uiCatalogProvider):
    """Custom provider to load A2UI schema directly from memory."""
    def __init__(self, schema: Dict[str, Any]):
        self.schema = schema
        
    def load(self) -> Dict[str, Any]:
        return self.schema

class MaterialCatalog:
    """Helper for accessing the Material A2UI catalog (mock provider)."""
    
    @staticmethod
    def get_config(version: str, examples_path: Optional[str] = None) -> CatalogConfig:
        basic_config = BasicCatalog.get_config(version, examples_path)
        
        # Load and modify basic catalog schema
        original_schema = basic_config.provider.load()
        modified_schema = inject_material_components(original_schema)
        
        # Set custom catalog ID
        modified_schema["catalogId"] = "https://a2ui.org/specification/v0_9/catalogs/material/catalog.json"
        modified_schema["$id"] = "https://a2ui.org/specification/v0_9/catalogs/material/catalog.json"
        
        # Re-wrap in CatalogConfig
        return CatalogConfig(
            name="material",
            provider=MemoryCatalogProvider(modified_schema),
            examples_path=examples_path
        )
