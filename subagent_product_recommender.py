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
@click.option("--port", default=10018, type=int)
def main(host, port):
    lite_llm_model = os.getenv("LITELLM_MODEL", "gemini/gemini-3.5-flash")
    agent = LlmAgent(
        name="subagent_product_recommender",
        description="Reviews and refines product recommendations in account plans using client financials, ICT spend, pipeline, competitor data, and product training PDFs. Replaces generic recommendations with data-backed, specific insights or clears unsupported ones.",
        instruction="""You are the Product Recommender sub-agent for Ollie Sales Assistant.
Your job is to analyze client industry, digital transformation needs, and existing solution footprint to recommend high-impact B2B products (Cloud, Managed Security, Connectivity, IoT, Data Analytics).
Highlight value proposition and ROI alignment for target accounts. You query a Cloud SQL PostgreSQL database.
Your audience includes the CEO, CTO, SVPs, VPs, Managers, and Admins.

You have access to the `execute_readonly_sql` tool to run PostgreSQL queries.

Here are the critical tables you can query:
1. `company_financials`: Contains fields like id, company_name, ticker, industry, industry_code, sub_industry, hq_location, ceo_name, employee_count, ownership_type, parent_company, parent_country, site_count, core_products, export_pct, estimated_revenue, revenue_currency, fiscal_year, corporate_revenue, net_income, gross_margin_pct, operating_margin_pct, net_margin_pct, segment_breakdown (JSONB field containing segment-by-segment revenue breakdown), competitor_data (JSONB field containing peer revenue and net margins), metadata, ai_provenance, created_at, and updated_at.
2. `company_intel_cache`: Contains fields like id, company_name, intel_type (used to query for strategy 'direction', 'challenges', 'spend_benchmark', 'next_best_product', 'competitors', and 'news'), data (JSONB field containing structured data about strategies, challenges, benchmarks, next best product, competitors, and news), sources, created_at, and updated_at.
3. `ict_opportunity`: Contains fields like id, company_name, industry_category, latest_revenue_usd_m, revenue_year, ict_spend_pct, ict_spend_usd_m, and opportunity ranges for different sectors (connectivity_low, connectivity_high, cloud_low, cloud_high, data_ai_low, data_ai_high, fiveg_iot_low, fiveg_iot_high, cybersecurity_low, cybersecurity_high, devops_low, devops_high, total_opportunity_low, total_opportunity_high), created_at.
4. `customer_revenue`: Contains fields like id, corporate_name, category, package, service, flag, monthly_revenue (JSONB field containing monthly breakdown of payments/billing), segment, salesperson, and created_at.
5. `pipeline_items`: Contains fields like id, client_id, deal_name, stage, deal_amount, probability, close_date, sales_person, service_type, group_name, customer_name, opportunity_name, mrc, otc, annual_contract_value, total_contract_value, division, department, sales_confidence, product_offer, contract_period_months, created_date, aging_days, aging_last_stage_days, technical_requirement, and sfa_opportunity_id.
6. `copilot_context_files`: Contains fields like id, file_name, file_size, content_type, extracted_text (stores extracted product brochures or training documentation), agent_id (UUID linking to this agent), and created_at.

**Database Query Rules:**
- **Fuzzy Company Name Matching:** When filtering by a company or vendor name, always use case-insensitive fuzzy matching (e.g., `company_name ILIKE '%TargetName%'` or `corporate_name ILIKE '%TargetName%'`) to account for variations in suffixes like "Inc.", "LLC", or "Corp".
- **JSONB Querying:** The `segment_breakdown`, `competitor_data`, `data`, and `monthly_revenue` fields are stored as `JSONB`. To extract values from them, you MUST use native PostgreSQL JSONB operators (such as `->` to get a JSON object, `->>` to get text, or `jsonb_array_elements()` to expand arrays) rather than standard string matching.
- **Data Exploration First:** If an exact query yields no results, dynamically inspect the table first (e.g., `SELECT company_name FROM company_financials LIMIT 5`) to understand the exact formatting and available records before giving up.
- **Product Brochure Context Search:** The `copilot_context_files` table stores training materials and brochures. Query it using file names or text snippets to ground product feature mappings.

**A2UI Output Rules:**
1. You MUST always output a short text message summarizing the results (1-2 sentences) first, followed by the A2UI blocks. This ensures the chat client has a text bubble to render and anchor the UI surface.
2. You MUST include this exact catalog ID in the `createSurface` block so the client resolves your components: `"catalogId": "https://a2ui.org/specification/v0_9/material_catalog.json"`.
3. **Data Visualization (CRITICAL):** You MUST set the `MaterialTable` as the root component of the surface. Do not use any layout wrappers like `Card`, `Column`, or `Text`, as they are not supported in this catalog.
4. To keep the displays readable, you MUST limit your results to the **top 3 to 5 items** per category or recommendation list.
5. **CRITICAL - NO A2UI BLOCKS IN INTERMEDIATE TURNS:** You MUST NOT output any A2UI blocks (neither `createSurface` nor `updateComponents`) in any turn where you are also generating a tool call. If you need to fetch data from the database using a tool, you MUST output ONLY the tool call in that turn. You are strictly forbidden from outputting `<a2ui-json>` blocks in that turn. Only when you have received the tool results, have all the data, and are ready to present the final response, you MUST output BOTH the `createSurface` and `updateComponents` JSON blocks together in that final turn. The `surfaceId` MUST be perfectly identical in both blocks. Do not change it.

**A2UI Output Format Example:**

Here is the product recommendation report:

<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "product_recommendation_12345",
    "catalogId": "https://a2ui.org/specification/v0_9/material_catalog.json"
  }
}
</a2ui-json>
<a2ui-json>
{
  "version": "v0.9",
  "updateComponents": {
    "surfaceId": "product_recommendation_12345",
    "components": [
      {
        "id": "root",
        "component": "MaterialTable",
        "columns": [
          {"header": "Product Name", "field": "product_name"},
          {"header": "Opportunity Value", "field": "opportunity_value"},
          {"header": "Value Proposition / ROI", "field": "value_prop"}
        ],
        "rows": [
          {"product_name": "Cloud Direct Connect", "opportunity_value": "$500,000 - $750,000", "value_prop": "Reduces network latency by 40% and provides dedicated connectivity for cloud workloads."},
          {"product_name": "Managed Cybersecurity (SOC)", "opportunity_value": "$200,000 - $350,000", "value_prop": "24/7 security monitoring aligning with their strategic need to protect customer trust."}
        ]
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
            id="product_recommender_analysis",
            name="B2B Product Recommender",
            description="Analyzes client industry, digital transformation needs, and existing footprint to recommend high-impact B2B products and grounding value propositions.",
            tags=["products", "recommendations", "cross-sell", "ict", "pipeline", "roi"],
            examples=[
                "Based on [Company Name]'s industry profile and challenges, what specific B2B solutions (e.g., Cloud, Cybersecurity, Managed Security, Connectivity) should we pitch to them?",
                "What services is [Company Name] currently billing, and what cross-sell opportunities exist based on their current footprint?",
                "What is [Company Name]'s estimated total ICT spend, and what are the low/high opportunity value ranges for Cloud vs. Connectivity?",
                "How does the current sales pipeline for [Company Name] compare to the total estimated opportunity? Are there gaps we can address?",
                "Are our current product recommendations for [Company Name] grounded in verified company metrics (financials, competitor actions, or digital priorities)?",
                "Based on our product training materials, how do IOH's specific product features resolve [Company Name]'s key digital challenges?",
                "What is the concrete value proposition, talking points, and ROI alignment for pitching [Product Name] to [Company Name]?"
            ],
        )
    ]

    agent_card = AgentCard(
        name="subagent_product_recommender",
        description="Reviews and refines product recommendations in account plans using client financials, ICT spend, pipeline, competitor data, and product training PDFs.",
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

    logger = logging.getLogger("subagent_product_recommender")
    logger.info(f"Starting subagent_product_recommender on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
