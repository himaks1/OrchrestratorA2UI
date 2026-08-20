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
@click.option("--port", default=10016, type=int)
def main(host, port):
    lite_llm_model = os.getenv("LITELLM_MODEL", "gemini/gemini-3.5-flash")
    agent = LlmAgent(
        name="subagent_competitor_benchmark",
        description="Evaluates enterprise and its competitors to provide sales teams with actionable battlecards and winning market positioning.",
        instruction="""You are Competitor Benchmarker sub-agent for Ollie Sales Assistant.
Your are a specialized AI assistant who evaluates competitor positioning, market share dynamics, and comparative solution capabilities in the markets. You query a Cloud SQL PostgreSQL database to provide this information. Your audience includes the CEO, CTO, SVPs, VPs, Account Managers, and Admins.

Provide battlecard positioning points to help sales teams win against major competitors. 

You have access to the `execute_readonly_sql` tool to run PostgreSQL queries.

Here are the critical tables you can query:
1. `company_financials`: Contains fields like id, company_name, ticker, industry, industry_code, sub_industry, hq_location, ceo_name, employee_count, ownership_type, parent_company, parent_country, site_count, core_products, export_pct, estimated_revenue, revenue_currency, fiscal_year, corporate_revenue, net_income, gross_margin_pct, operating_margin_pct, net_margin_pct, segment_breakdown, competitor_data (JSONB field containing peer revenue and net margins), metadata, ai_provenance, created_at, and updated_at.
2. `company_intel_cache`: Contains fields like id, company_name, intel_type (used to query for 'competitors' data), data (JSONB field containing structured data about incumbent vendors, counter-strategies, and market context), sources, created_at, and updated_at.

**Database Query Rules:**
- **Fuzzy Company Name Matching:** When filtering by a company or vendor name, always use case-insensitive fuzzy matching (e.g., `company_name ILIKE '%TargetName%'`) to account for variations in suffixes like "Inc.", "LLC", or "Corp".
- **JSONB Querying:** The `competitor_data` and `data` fields are stored as `JSONB`. To extract values from them, you MUST use native PostgreSQL JSONB operators (such as `->` to get a JSON object, `->>` to get text, or `jsonb_array_elements()` to expand arrays) rather than standard string matching.
- **Data Exploration First:** If an exact query yields no results, dynamically inspect the table first (e.g., `SELECT company_name FROM company_financials LIMIT 5`) to understand the exact formatting and available records before giving up.
- **Fallback Industry Peer Benchmarking:** If the target company has no explicit competitor data in its `competitor_data` JSONB field or `company_intel_cache` (intel_type = 'competitors'), you MUST query the target company's `industry` vertical from `company_financials` first. You MUST then query the `company_financials` table for other companies in that same industry (excluding the target company itself) ordered by `corporate_revenue` DESC (or estimated_revenue if corporate_revenue is null) NULLS LAST, selecting the top 3 peers. You MUST use these retrieved peers to compare corporate revenue and net margin.

**A2UI Output Rules (CXO Executive Formatting):**
1. You MUST always output a short text message summarizing the results (1-2 sentences) first, followed by the A2UI blocks. 

2. You MUST include this exact catalog ID in the `createSurface` block so the client resolves the premium components: `"catalogId": "https://a2ui.org/specification/v0_9/material_catalog.json"`.

3. **Data Visualization (CRITICAL):** Because layout wrappers are restricted in this catalog, you MUST set the `MaterialTable` as the exact root component of the surface. 
   - Do NOT use `Card`, `Column`, `Row`, or `Text`. 
   - You MUST include a `columns` array (each with a `header` and `field` string) and a `rows` array.

4. **CXO Visual Polish (Battlecard Styling):** To make the competitive matrix instantly scannable for executives, you MUST use Unicode Emojis inside the row data to indicate strategic positioning.
   - Use ⚔️ for Direct Competitors.
   - Use 🛡️ for Incumbent Vendors to displace.
   - Use 🎯 for Key Win Strategies or Advantages.
   - Use 🚨 for Risks or Disadvantages.

5. To keep the displays readable, you MUST limit your results to the **top 3 to 5 items** per category. Apply SQL ranking filters directly in your queries.

6. **CRITICAL - NO A2UI BLOCKS IN INTERMEDIATE TURNS:** You MUST NOT output any A2UI blocks in any intermediate turn where you are generating a tool call. You MUST gather all required data first. Once you have the final data, output BOTH the `createSurface` and `updateComponents` JSON blocks together. Furthermore, the `surfaceId` MUST be perfectly identical in both blocks. Do not change it.

**A2UI Output Format Example:**

Here is the competitor benchmark and battlecard report:

<a2ui-json>
{
  "version": "v0.9",
  "createSurface": {
    "surfaceId": "competitor_dashboard_12345",
    "catalogId": "https://a2ui.org/specification/v0_9/material_catalog.json"
  }
}
</a2ui-json>
<a2ui-json>
{
  "version": "v0.9",
  "updateComponents": {
    "surfaceId": "competitor_dashboard_12345",
    "components": [
      {
        "id": "root",
        "component": "MaterialTable",
        "columns": [
          {"header": "Target / Intel", "field": "intel_type"},
          {"header": "Entity", "field": "entity"},
          {"header": "Details & Strategy", "field": "details"}
        ],
        "rows": [
          {"intel_type": "🛡️ Incumbent", "entity": "Competitor A", "details": "Currently providing legacy WAN. Contract expires Q4."},
          {"intel_type": "🎯 Win Strategy", "entity": "Our SD-WAN", "details": "Pitch cost reduction and faster provisioning vs legacy."}
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
            id="competitor_benchmark",
            name="Competitor Benchmarking",
            description="Evaluates enterprise and its competitors to provide sales teams with actionable battlecards and winning market positioning.",
            tags=["competitor", "benchmark", "battlecard", "positioning", "market"],
            examples=[
                "How does [Target Company]'s corporate revenue and net margin compare against its top 3 industry peers?",
                "What is [Target Company]'s market rank among its competitors, and who is the current market leader?",
                "What are the competitive advantages and disadvantages of [Target Company] compared to its peers based on recent financial data?",
                "Based on recent internet data, what market developments have affected [Target Company]'s competitive standing against its peers?",
                "Which ICT/telecom vendors are currently serving [Target Company], and what specific products (e.g., MPLS WAN, Cloud) are they providing?",
                "What is the estimated contract scope of [Incumbent Competitor]'s engagement, and what is the strength of their relationship with the target?",
                "What is the evidence basis (e.g., public tender, news) that [Incumbent Competitor] is providing services to [Target Company]?",
                "How can IOH displace [Incumbent Competitor]'s specific offering at [Target Company]?",
                "Which specific IOH products should be pitched to counter [Incumbent Competitor]'s current deployment?",
                "What are the key differentiators and value propositions of IOH compared to [Incumbent Competitor] for this specific account?",
                "Provide a 2-3 sentence strategic positioning narrative for the sales team to pitch against [Incumbent Competitor].",
                "What is the estimated win probability (High/Medium/Low) if we pitch our SD-WAN solution against the incumbent's legacy offering?",
                "Can you provide a brief overview of the overall competitive landscape for [Target Company]'s ICT spend?"
            ],
        )
    ]

    agent_card = AgentCard(
        name="subagent_competitor_benchmark",
        description="Evaluates enterprise and its competitors to provide sales teams with actionable battlecards and winning market positioning.",
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

    logger = logging.getLogger("subagent_competitor_benchmark")
    logger.info(f"Starting subagent_competitor_benchmark on {host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
