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
            name="bargraph_catalog",
            catalog_path="bargraph_catalog_definition.json"
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

**A2UI Output Rule:**
When you present data summaries, breakdowns, or visual comparisons, you MUST use the native `BarGraph` component defined in the catalog schema.
Example `BarGraph` component:
`{"id": "revenue_bargraph", "component": "BarGraph", "title": "Revenue by Division", "orientation": "vertical", "xAxis": {"label": "Division", "key": "label"}, "yAxis": {"label": "Revenue ($)", "key": "value", "format": "currency"}, "data": [{"label": "Enterprise", "value": 1200000}, {"label": "Mid-Market", "value": 850000}]}`

**Interactivity:** You MUST include interactive UI components (such as `Button`s) below your data bar graphs or cards to allow the user to drill down or analyze further. These buttons should trigger an `action` with `event.name` set to `"analyze_sales_performance"`, passing a specific `"query"` in the `context`.

Set the `catalogId` to `"https://a2ui.org/catalogs/bargraph/0.9/bargraph_catalog_definition.json"`.

Ensure the `surfaceId` is unique per response by appending a unique identifier (e.g., `sales_bargraph_<random_number>`).

A2UI Output format example:
<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "sales_bargraph_12345",
    "catalogId": "https://a2ui.org/catalogs/bargraph/0.9/bargraph_catalog_definition.json"
  }
}
</a2ui-json>
<a2ui-json>
{
  "version": "v0.9",
  "updateComponents": {
    "surfaceId": "sales_bargraph_12345",
    "components": [
      {
        "id": "root",
        "component": "Card",
        "child": "content_col"
      },
      {
        "id": "content_col",
        "component": "Column",
        "children": ["title", "revenue_bargraph", "drill_down_btn"]
      },
      {
        "id": "title",
        "component": "Text",
        "text": "### Sales Performance Report",
        "variant": "h3"
      },
      {
        "id": "revenue_bargraph",
        "component": "BarGraph",
        "title": "Revenue by Division",
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
          {"label": "Enterprise", "value": 1200000, "color": "#1A73E8"},
          {"label": "Commercial", "value": 750000, "color": "#34A853"}
        ]
      },
      {
        "id": "drill_down_btn",
        "component": "Button",
        "child": "drill_down_btn_txt",
        "action": {
          "event": {
            "name": "analyze_sales_performance",
            "context": {
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
</a2ui-json>
""",
        tools=[execute_readonly_sql],
    )

    base_url = f"http://{host}:{port}"
    runner = Runner(
        agent=agent,
        artifact_service=InMemoryArtifactService(),
        session_service=InMemorySessionService(),
        memory_service=InMemoryMemoryService(),
    )

    config = A2aAgentExecutorConfig(
        runner=runner,
        event_converter=convert_event_to_a2a_events,
        lite_llm_model=lite_llm_model,
        a2ui_converter=a2ui_converter,
    )

    agent_executor = A2aAgentExecutor(config=config)

    capabilities = AgentCapabilities(
        streaming=False,
        push=False,
        history=True,
    )

    skills = [
        AgentSkill(
            id="sales_performance_barchart_analysis",
            name="Sales Performance BarGraph Analysis",
            description="Analyzes revenue targets vs actuals, monthly trends, and segment performance, presenting results as interactive BarGraph components.",
            tags=["sales", "revenue", "barchart", "performance"],
            examples=[
                "Show me the sales performance for Q3 2026",
                "What is the revenue target vs actual by division?",
                "Which customer segments generate the most revenue?"
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

    a2ui_extension = get_a2ui_agent_extension(
        agent_card=agent_card,
        supported_catalog_ids=[
            "https://a2ui.org/catalogs/bargraph/0.9/bargraph_catalog_definition.json"
        ],
    )

    request_handler = DefaultRequestHandler(
        agent_executor=agent_executor,
        task_store=InMemoryTaskStore(),
    )

    server = A2AStarletteApplication(
        agent_card=agent_card,
        http_handler=request_handler,
        agent_extensions=[a2ui_extension],
    )

    app = server.build()

    from starlette.responses import FileResponse
    async def serve_bargraph_catalog(request):
        catalog_path = os.path.join(os.path.dirname(__file__), "bargraph_catalog_definition.json")
        return FileResponse(catalog_path, media_type="application/json")
    app.add_route("/bargraph_catalog_definition.json", serve_bargraph_catalog)

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
