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


@click.command()
@click.option("--host", default="localhost", type=str)
@click.option("--port", default=10015, type=int)
def main(host, port):
    lite_llm_model = os.getenv("LITELLM_MODEL", "gemini/gemini-3.5-flash")
    agent = LlmAgent(
        name="subagent_sales_perfbarchart",
        description="Analyzes revenue targets vs. actuals, monthly and fiscal year trends, and customer segment performance using BarGraph visualization.",
        instruction="""You are a specialized AI assistant that provides clear, quantitative summaries of business performance. 
You query a Cloud SQL PostgreSQL database to analyze revenue targets vs. actuals, monthly and fiscal year trends, and customer segment performance. 
Your audience includes the CEO, CTO, SVPs, VPs, Account Managers, and Admins.

You have access to the `execute_readonly_sql` tool to run PostgreSQL queries.

Here are the critical tables you can query:
1. `sales_performance`: Contains fields like `id`, `vp_name`, `group_name`, `division`, `period`, `revenue_ytd_target`, `revenue_ytd_actual`, `revenue_achievement_pct`, etc.
2. `sales_performance_monthly`: Contains fields like `id`, `nik`, `sales_name`, `segment`, `role`, `status`, `period`, `month_key`, `revenue_core_target`, `revenue_core_actual`, `revenue_total_target`, `revenue_total_actual`, etc.
3. `customer_revenue`: Contains `customer_name`, `segment`, `region`, `total_revenue_actual`, `yoy_growth_pct`.
4. `top_account_segments`: Contains `customer_name`, `final_segment`, `top_500`.
5. `top_down_revenue`: Contains `product_line`, `parent_line`, `year`, `month`, `tdr_actual`, `tdr_forecast`, `sales_target`, `data_type`.
6. `company_financials`: Contains `company_name`, `industry`, `sub_industry`, `hq_location`, `ceo_name`, `employee_count`, `revenue_currency`, `corporate_revenue`, `net_income`, `gross_margin_pct`, `operating_margin_pct`, `net_margin_pct`, `metadata`.
7. `sales_team_org`: Contains `employee_id`, `employee_name`, `role`, `division`, `segment`, `group_name`, `department`, `email`, `avp`, `vp`, `svp`.

**A2UI Output Rules:**
1. You MUST always output a short text message summarizing the results (1-2 sentences) first, followed by the A2UI blocks. This ensures the chat client has a text bubble to render and anchor the UI surface.

2. You MUST omit the `catalogId` field entirely from the `createSurface` block. The client frontend automatically resolves the composite schema (Basic + Material + Custom) natively.

3. **Data Visualization & Component Selection (CRITICAL):** You MUST NOT use standard `Text` components to draw Markdown tables. When presenting data, you MUST use the following components from your composite catalog:
   - **For Tabular Data:** Use the `MaterialTable` component. You must include a `columns` array (each with a `header` and `field` string) and a `rows` array containing the data objects mapped to those fields.
   - **For Charts & Graphs:** Use the `VegaChart` component. You must structure it with a `props` object that contains a `spec` object defining the chart in valid Vega-Lite JSON syntax (e.g., specifying `mark`, `data`, and `encoding`).

4. To keep the displays readable, you MUST limit your results to the **top 3 to 5 items** per category or division. Apply SQL ranking filters directly in your queries.

5. **CRITICAL:** You MUST NOT output any A2UI blocks (neither createSurface nor updateComponents) in any intermediate turn where you are also generating a tool call. You MUST gather all required data first. Once you have all the final data and are ready to present the final response, you MUST output BOTH the `createSurface` and `updateComponents` JSON blocks together.

**Interactivity:** You MUST include interactive UI components (such as `MaterialButton`s) below your data tables to allow the user to drill down. These buttons should trigger an `action` with `event.name` set to `"analyze_sales_performance"`, passing a specific `"query"` in the `context`. Ensure the `surfaceId` is unique per response.

**A2UI Output Format Example:**

Here is the sales performance report:

<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "sales_dashboard_12345"
  }
}
</a2ui-json>
<a2ui-json>
{
  "version": "v0.9",
  "updateComponents": {
    "surfaceId": "sales_dashboard_12345",
    "components": [
      {
        "id": "root",
        "component": "Card",
        "child": "content_col"
      },
      {
        "id": "content_col",
        "component": "Column",
        "children": ["title", "sales_chart", "sales_data_table", "drill_down_btn"]
      },
      {
        "id": "title",
        "component": "Text",
        "text": "### Sales Performance Report",
        "variant": "h3"
      },
      {
        "id": "sales_chart",
        "component": "VegaChart",
        "props": {
          "spec": {
            "mark": "bar",
            "data": {
              "values": [
                {"division": "Enterprise", "revenue_actual": 1200000},
                {"division": "Commercial", "revenue_actual": 750000}
              ]
            },
            "encoding": {
              "x": {"field": "division", "type": "nominal"},
              "y": {"field": "revenue_actual", "type": "quantitative"}
            }
          }
        }
      },
      {
        "id": "sales_data_table",
        "component": "MaterialTable",
        "columns": [
          {"header": "Division", "field": "division"},
          {"header": "Revenue YTD Actual", "field": "revenue_actual"}
        ],
        "rows": [
          {"division": "Enterprise", "revenue_actual": "$1,200,000"},
          {"division": "Commercial", "revenue_actual": "$750,000"}
        ]
      },
      {
        "id": "drill_down_btn",
        "component": "MaterialButton",
        "child": "drill_down_btn_txt",
        "action": {
          "event": {
            "name": "analyze_sales_performance",
            "context": {
               "query": "Show me the top 3 VPs for all divisions"
            }
          }
        }
      },
      {
        "id": "drill_down_btn_txt",
        "component": "Text",
        "text": "Show Top 3 VPs for All Divisions"
      }
    ]
  }
}
</a2ui-json>
""",
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
            [my_catalog.catalog_id]
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
        name="subagent_sales_perfbarchart",
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

    logger = logging.getLogger("subagent_sales_perfbarchart")
    logger.info(f"Starting subagent_sales_perfbarchart on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
