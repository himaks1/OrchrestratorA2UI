# Monkeypatch A2UI core schema to make catalogId optional (required for unified composite catalog support)
from a2ui.core.schema.server_to_client import CreateSurface, CreateSurfaceMessage, A2uiMessageListWrapper
field_info = CreateSurface.model_fields.get("catalog_id")
if field_info:
    field_info.default = None
    CreateSurface.model_fields["catalog_id"] = field_info
    CreateSurface.model_rebuild(force=True)
    CreateSurfaceMessage.model_rebuild(force=True)
    A2uiMessageListWrapper.model_rebuild(force=True)

import logging
import os
import click
import uvicorn
from dotenv import load_dotenv

from google.adk.agents.llm_agent import LlmAgent
from google.adk.models.google_llm import Gemini
from google.adk.runners import Runner
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutor, A2aAgentExecutorConfig
from google.adk.a2a.converters.event_converter import convert_event_to_a2a_events
from google.adk.artifacts import InMemoryArtifactService
from google.adk.sessions import InMemorySessionService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.tasks import InMemoryTaskStore
from a2a.server.apps import A2AStarletteApplication
from a2a.types import AgentCard, AgentSkill, AgentCapabilities
from starlette.middleware.cors import CORSMiddleware
from a2ui.schema.constants import VERSION_0_8, VERSION_0_9
from a2ui.a2a.extension import get_a2ui_agent_extension

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.material_catalog.provider import MaterialCatalog
from a2ui.schema.catalog import CatalogConfig
from a2ui.inference_formats.direct_json import DirectJsonFormat
from a2ui.adk.a2a.part_converter import A2uiPartConverter
from a2ui.schema.common_modifiers import remove_strict_validation

def add_vega_chart(schema: dict) -> dict:
    if "components" in schema:
        schema["components"]["VegaChart"] = {
            "type": "object",
            "properties": {
                "component": {"const": "VegaChart"},
                "props": {"type": "object"}
            },
            "required": ["component"]
        }
        if "$defs" in schema and "anyComponent" in schema["$defs"]:
            schema["$defs"]["anyComponent"]["oneOf"].append({"$ref": "#/components/VegaChart"})
    return schema

def make_catalog_id_optional(schema: any) -> any:
    if isinstance(schema, dict):
        new_schema = {k: make_catalog_id_optional(v) for k, v in schema.items()}
        if "createSurface" in new_schema and "required" in new_schema["createSurface"]:
            reqs = new_schema["createSurface"]["required"]
            if "catalogId" in reqs:
                new_schema["createSurface"]["required"] = [r for r in reqs if r != "catalogId"]
        return new_schema
    elif isinstance(schema, list):
        return [make_catalog_id_optional(item) for item in schema]
    return schema

inference_format = DirectJsonFormat(
    version=VERSION_0_9,
    catalogs=[
        MaterialCatalog.get_config(version=VERSION_0_9),
        BasicCatalog.get_config(version=VERSION_0_9)
    ],
    schema_modifiers=[remove_strict_validation, make_catalog_id_optional],
)
my_catalog = inference_format.get_selected_catalog()
a2ui_converter = A2uiPartConverter(a2ui_catalog=my_catalog, version=VERSION_0_9)

load_dotenv()

# --- START: A2UI Schema Manager for Charting ---
from a2ui.schema.manager import A2uiSchemaManager, CatalogConfig
from pathlib import Path

# Establish the workspace base directory
BASE_DIR = Path(__file__).resolve().parent # Use .parent to get the orchestrator directory
# Reference your specific sales/chart catalog config
sales_chart_catalog = CatalogConfig.from_path(
    name="sales_charts",
    catalog_path=str(BASE_DIR / "examples" / "custom_catalog" / "0.9" / "chart.json")
)

# Compile the schema manager for this Sub-agent
# This manager will now understand Column, MaterialTable, AND VegaChart
sales_schema_manager = A2uiSchemaManager(
    version=VERSION_0_9,
    catalogs=[
        BasicCatalog.get_config(version=VERSION_0_9),
        MaterialCatalog.get_config(version=VERSION_0_9),
        sales_chart_catalog
    ]
)
# --- END: A2UI Schema Manager for Charting ---

logging.basicConfig(level=logging.INFO)


def execute_readonly_sql(query: str) -> str:
    """
    Executes a read-only SQL query against the Cloud SQL PostgreSQL database and returns the results as a string.
    
    Args:
        query: A standard PostgreSQL query string. Do NOT run INSERT, UPDATE, DELETE, or DROP.
    """
    import sqlalchemy
    from google.cloud.sql.connector import Connector
    import decimal
    
    logging.info(f"Executing SQL: {query}")
    connector = Connector()
    def getconn():
        conn = connector.connect(
            "ssrg-agents:us-west1:sales-instance",
            "pg8000",
            user="sales_app_user",
            password="OllieAppDbPass2026!",
            db="sales_db"
        )
        return conn

    engine = sqlalchemy.create_engine("postgresql+pg8000://", creator=getconn)
    try:
        with engine.connect() as conn:
            result = conn.execute(sqlalchemy.text(query))
            # Strict limit to 50 rows to prevent LLM context explosion
            rows = result.fetchmany(50) 
            columns = result.keys()
            
            def convert_val(val):
                if isinstance(val, decimal.Decimal):
                    return float(val)
                return val
                
            dict_rows = [{k: convert_val(v) for k, v in zip(columns, row)} for row in rows]
            return str(dict_rows)
    except Exception as e:
        return f"Error executing query: {str(e)}"
    finally:
        connector.close()


# 1. First, define the instruction generator right before the agent definition.
# This dynamically teaches the LLM the exact structure of all components.

charting_example = """
USER_QUERY:
"Compare the monthly sales actuals versus quota target as a bar chart."

LLM_RESPONSE:
Here is a comparison of the monthly sales actuals versus quota target.

<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "sales_comparison_chart_123",
    "catalogId": "https://a2ui.org/catalogs/custom/0.9/custom_catalog_definition.json"
  }
}
</a2ui-json>
<a2ui-json>
{
  "version": "v0.9",
  "updateComponents": {
    "surfaceId": "sales_comparison_chart_123",
    "components": [
      {
        "id": "root",
        "component": "Column",
        "children": [
          {
            "component": "VegaChart",
            "spec": {
              "$schema": "https://vega.github.io/schema/vega-lite/v5.json",
              "description": "Monthly Sales vs Quota",
              "width": "container",
              "height": 250,
              "data": {
                "values": [
                  {"Month": "Jan", "Type": "Actual", "Revenue": 150000},
                  {"Month": "Jan", "Type": "Quota", "Revenue": 120000},
                  {"Month": "Feb", "Type": "Actual", "Revenue": 170000},
                  {"Month": "Feb", "Type": "Quota", "Revenue": 130000}
                ]
              },
              "mark": "bar",
              "encoding": {
                "x": {"field": "Month", "type": "nominal", "axis": {"title": "Month"}},
                "y": {"field": "Revenue", "type": "quantitative", "axis": {"title": "Revenue ($)"}},
                "xOffset": {"field": "Type", "type": "nominal"},
                "color": {"field": "Type", "type": "nominal"}
              }
            }
          }
        ]
      }
    ]
  }
}
</a2ui-json>
"""

sales_analyst_instruction = sales_schema_manager.generate_system_prompt(
    role_description="You are a specialized Sales Performance Analyst Sub-Agent.",
    ui_description="""To present tabular data like lists of top performers or detailed revenue breakdowns, use the `MaterialTable` component. To visualize trends, comparisons, or performance over time (e.g., actuals vs. quota), you MUST use the `VegaChart` component, which should be placed inside a `Column` layout.""",
    include_schema=True,
    include_examples=True # The manager will auto-generate examples.
)
# NOTE: The current SDK version does not support `few_shot_examples` as an argument to generate_system_prompt.
# We append the custom example directly to the string instead.
sales_analyst_instruction += "\n\n### Custom Few-Shot Examples:\n" + charting_example



# 2. Now, update the LlmAgent to use this new dynamic instruction.
@click.command()
@click.option("--host", default="localhost", type=str)
@click.option("--port", default=10015, type=int)
def main(host, port):
    lite_llm_model = os.getenv("LITELLM_MODEL", "gemini/gemini-3.5-flash")
    agent = LlmAgent(
        name="subagent_sales_performance_analyst",
        description="Analyzes revenue targets vs. actuals, monthly and fiscal year trends, and customer segment performance using BarGraph visualization.",
        instruction=sales_analyst_instruction,
        model=Gemini(model=lite_llm_model.replace("vertex_ai/", "").replace("gemini/", "")),
        tools=[execute_readonly_sql],
    )

    base_url = f"http://{host}:{port}"
    runner = Runner(
        app_name=agent.name,
        agent=agent,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )

    executor_config = A2aAgentExecutorConfig(
        event_converter=lambda e, ic, tid=None, cid=None, pcf=None: convert_event_to_a2a_events(
            e, ic, tid, cid, a2ui_converter.convert
        )
    )
    agent_executor = A2aAgentExecutor(runner=runner, config=executor_config)

    extensions = [
        get_a2ui_agent_extension(VERSION_0_8, False, []),
        get_a2ui_agent_extension(
            VERSION_0_9, 
            False, 
            [my_catalog.catalog_id, "https://a2ui.org/catalogs/custom/0.9/custom_catalog_definition.json"] # Added your charts catalog ID here!
        ),
    ]

    capabilities = AgentCapabilities(
        streaming=True,
        push=False,
        history=True,
        extensions=extensions,
    )

    skills = [
        AgentSkill(
            id="sales_performance_barchart_analysis",
            name="Sales Performance BarGraph Analysis",
            description="Analyzes revenue targets vs actuals, monthly trends, VP rankings, and division performance, presenting results as interactive tables.",
            tags=["sales", "revenue", "barchart", "performance", "vps", "division"],
            examples=[
                "Show me the sales performance for Q3 2026",
                "What is the revenue target vs actual by division?",
                "Which customer segments generate the most revenue?",
                "give me top performing VPs per division?",
                "give me top performing VPs in R&E",
                "who are the best performing VPs?"
            ],
        )
    ]

    agent_card = AgentCard(
        name="subagent_sales_performance_analyst",
        description="Analyzes revenue targets vs. actuals, monthly trends, and segment performance using BarGraph visuals.",
        url=base_url,
        version="1.0.0",
        capabilities=capabilities,
        skills=skills,
        default_input_modes=["text"],
        default_output_modes=["text"],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
    )

    app = server.build()



    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    logger = logging.getLogger("subagent_sales_performance_analyst")
    logger.info(f"Starting subagent_sales_performance_analyst on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
