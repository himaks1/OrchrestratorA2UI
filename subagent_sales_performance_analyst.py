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
from a2ui.schema.catalog import CatalogConfig
from a2ui.inference_formats.direct_json import DirectJsonFormat
from a2ui.adk.a2a.part_converter import A2uiPartConverter
from a2ui.schema.common_modifiers import remove_strict_validation

inference_format = DirectJsonFormat(
    version=VERSION_0_9,
    catalogs=[
        CatalogConfig.from_path(
            name="custom_catalog",
            catalog_path="custom_catalog_definition.json",
            examples_path="examples/custom_catalog/0.9"
        ),
        BasicCatalog.get_config(version=VERSION_0_9)
    ],
    schema_modifiers=[remove_strict_validation],
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
        name="subagent_sales_performance_analyst",
        description="Analyzes revenue targets vs. actuals, monthly and fiscal year trends, and customer segment performance.",
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

**A2UI Output Rule:**
When you fetch tabular data or trends, you MUST generate an A2UI `DataTable` component.
You should dynamically pick the most relevant columns from the SQL results to populate the columns and rows of the `DataTable`.
Example `DataTable` component:
`{"id": "sales_table", "component": "DataTable", "title": "Performance Summary", "columns": [{"key": "division", "label": "Division", "type": "string"}, {"key": "actual", "label": "Revenue", "type": "currency"}], "rows": [{"division": "Enterprise", "actual": 1000000}]}`

When the user asks for charts, breakdowns, or visual comparisons, you MUST use the native A2UI `Chart` component (supports `bar`, `line`, `pie`, `doughnut`, `area`).
Example `Chart` component:
`{"id": "my_chart", "component": "Chart", "type": "bar", "title": "Segment Revenue Comparison", "chartData": [{"label": "Enterprise", "value": 1000000}, {"label": "Mid-Market", "value": 500000}]}`

**Interactivity:** You MUST include interactive UI components (such as `Button`s) below your data tables or charts to allow the user to drill down or analyze further. These buttons should trigger an `action` with `event.name` set to `"analyze_sales_performance"`, passing a specific `"query"` in the `parameters`.

Set the `catalogId` to `"https://raw.githubusercontent.com/himaks1/OrchrestratorA2UI/main/custom_catalog_definition.json"`.

Ensure the `surfaceId` is unique per response by appending a unique identifier (e.g., `sales_analyst_<random_number>`).

A2UI Output format example:
<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "sales_analyst_12345",
    "catalogId": "https://raw.githubusercontent.com/himaks1/OrchrestratorA2UI/main/custom_catalog_definition.json"
  }
}
</a2ui-json>
<a2ui-json>
{
  "version": "v0.9",
  "updateComponents": {
    "surfaceId": "sales_analyst_12345",
    "components": [
      {
        "id": "root",
        "component": "Card",
        "child": "content_col"
      },
      {
        "id": "content_col",
        "component": "Column",
        "children": ["title", "sales_table", "drill_down_btn"]
      },
      {
        "id": "title",
        "component": "Text",
        "text": "### Sales Performance Report",
        "variant": "h3"
      },
      {
        "id": "sales_table",
        "component": "DataTable",
        "title": "Revenue by Division",
        "columns": [
          {"key": "division", "label": "Division", "type": "string"},
          {"key": "actual", "label": "Actual Revenue", "type": "currency"}
        ],
        "rows": [
          {"division": "Enterprise", "actual": 1000000}
        ]
      },
      {
        "id": "drill_down_btn",
        "component": "Button",
        "child": "drill_down_btn_txt",
        "action": {
          "event": {
            "name": "analyze_sales_performance",
            "parameters": {
               "query": "Show me a detailed breakdown for the Enterprise segment"
            }
          }
        }
      },
      {
        "id": "drill_down_btn_txt",
        "component": "Text",
        "text": "Analyze Details"
      }
    ]
  }
}
</a2ui-json>""",
        model=Gemini(model=lite_llm_model.replace("vertex_ai/", "").replace("gemini/", "")),
        tools=[execute_readonly_sql],
    )

    runner = Runner(
        app_name=agent.name,
        agent=agent,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )

    extensions = [
        get_a2ui_agent_extension(VERSION_0_8, False, []),
        get_a2ui_agent_extension(VERSION_0_9, False, []),
    ]

    agent_card = AgentCard(
        name="Sales Performance Analyst",
        description="Analyzes revenue targets vs. actuals, monthly and fiscal year trends, and customer segment performance.",
        url=f"http://{host}:{port}",
        version="1.0.0",
        default_input_modes=["text"],
        default_output_modes=["text"],
        capabilities=AgentCapabilities(streaming=True, extensions=extensions),
        skills=[
            AgentSkill(
                id="analyze_sales_performance",
                name="analyze_sales_performance",
                description="Analyze revenue targets vs. actuals, monthly trends, and customer segments",
                examples=["How did actual revenue compare to targets for Q3?", "What are the top 5 performing customer segments this year?", "Show me the monthly revenue trend"],
                tags=["sales", "performance", "revenue", "analytics"],
            ),
            AgentSkill(
                id="view_sales_by_category",
                name="View Sales by Category",
                description="Displays a bar chart of sales broken down by product category or segment for a given time period.",
                tags=["sales", "breakdown", "category", "bar chart", "revenue"],
                examples=[
                    "show my sales breakdown by product category for q3",
                    "What's the sales breakdown for last month?",
                ],
            )
        ],
    )

    executor_config = A2aAgentExecutorConfig(
        gen_ai_part_converter=a2ui_converter.convert,
        event_converter=lambda e, ic, tid=None, cid=None, pcf=None: convert_event_to_a2a_events(
            e, ic, tid, cid, a2ui_converter.convert
        )
    )
    executor = A2aAgentExecutor(runner=runner, config=executor_config)
    request_handler = DefaultRequestHandler(
        agent_executor=executor,
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card, http_handler=request_handler
    )

    app = server.build()

    from starlette.responses import FileResponse
    async def serve_custom_catalog(request):
        catalog_path = os.path.join(os.path.dirname(__file__), "custom_catalog_definition.json")
        return FileResponse(catalog_path, media_type="application/json")
    app.add_route("/custom_catalog_definition.json", serve_custom_catalog)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
