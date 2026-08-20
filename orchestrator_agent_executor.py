# Copyright 2024 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#      https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging
import json
import asyncio
import re
import os
import httpx
from typing import Optional, Any, List, override, Union

from google.adk.agents.invocation_context import new_invocation_context_id, InvocationContext
from google.adk.events.event_actions import EventActions
from google.adk.events.event import Event
from google.adk.artifacts import InMemoryArtifactService
from google.adk.memory.in_memory_memory_service import InMemoryMemoryService
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.a2a.converters.request_converter import AgentRunRequest
from google.adk.a2a.executor.a2a_agent_executor import A2aAgentExecutorConfig, A2aAgentExecutor
from google.adk.a2a.executor.executor_context import ExecutorContext
from google.adk.a2a.executor.config import ExecuteInterceptor
from google.adk.a2a.converters import event_converter, part_converter
from google.adk.agents.callback_context import CallbackContext
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.agents.remote_a2a_agent import RemoteA2aAgent, DEFAULT_TIMEOUT, convert_genai_part_to_a2a_part
from google.adk.models.google_llm import Gemini
from google.adk.agents.llm_agent import LlmAgent
from google.adk.planners.built_in_planner import BuiltInPlanner
from google.genai import types as genai_types

from a2a.server.agent_execution import RequestContext
from a2a.server.events.event_queue import EventQueue
from a2a.server.events import Event as A2AEvent
from a2a.client import A2ACardResolver
from a2a.client.client import Client, Consumer
from a2a.client.client import ClientConfig as A2AClientConfig
from a2a.client.client_factory import ClientFactory as A2AClientFactory
from a2a.client.middleware import ClientCallContext, ClientCallInterceptor
from a2a.extensions.common import HTTP_EXTENSION_HEADER
from a2a.types import TransportProtocol as A2ATransport, AgentCard, AgentCapabilities, Message

from a2ui.a2a.extension import (
    try_activate_a2ui_extension,
    get_a2ui_extension_uri,
    get_a2ui_agent_extension,
    A2UI_EXTENSION_BASE_URI,
    AGENT_EXTENSION_SUPPORTED_CATALOG_IDS_KEY,
    AGENT_EXTENSION_ACCEPTS_INLINE_CATALOGS_KEY,
)
from a2ui.a2a.parts import is_a2ui_part
from a2ui.schema.constants import (
    VERSION_0_8,
    VERSION_0_9,
    A2UI_CLIENT_CAPABILITIES_KEY,
    A2UI_CLIENT_DATA_MODEL_KEY,
    A2UI_CLIENT_DATA_MODEL_SURFACES_KEY,
    A2UI_ACTIONS_KEY,
    A2UI_ERROR_KEY,
    A2UI_SURFACE_ID_KEY,
    A2UI_VERSION_KEY,
    A2UI_CODE_KEY,
    A2UI_MESSAGE_KEY,
    A2UI_BEGIN_RENDERING_KEY,
    A2UI_DELETE_SURFACE_KEY,
)

from a2ui.adk.orchestration.a2ui_subagent_map import A2uiSubagentMap, SurfaceIdAlreadyExistsError

# Monkeypatch A2UI core schema to make catalogId optional (required for unified composite catalog support)
from a2ui.core.schema.server_to_client import CreateSurface, CreateSurfaceMessage, A2uiMessageListWrapper
field_info = CreateSurface.model_fields.get("catalog_id")
if field_info:
    field_info.default = None
    CreateSurface.model_fields["catalog_id"] = field_info
    CreateSurface.model_rebuild(force=True)
    CreateSurfaceMessage.model_rebuild(force=True)
    A2uiMessageListWrapper.model_rebuild(force=True)

logger = logging.getLogger(__name__)

ACTIVE_UI_VERSION_STATE_KEY = "active_ui_version"
CLIENT_CAPABILITIES_STATE_KEY = "client_capabilities"




def prefix_surface_ids(data, prefix: str):
    if isinstance(data, dict):
        for k, v in data.items():
            if k == A2UI_SURFACE_ID_KEY and isinstance(v, str):
                if not v.startswith(prefix):
                    data[k] = f"{prefix}{v}"
            else:
                prefix_surface_ids(v, prefix)
    elif isinstance(data, list):
        for item in data:
            prefix_surface_ids(item, prefix)

def strip_prefix_from_surface_ids(data, prefix: str):
    if isinstance(data, dict):
        for k, v in data.items():
            if k == A2UI_SURFACE_ID_KEY and isinstance(v, str):
                if v.startswith(prefix):
                    data[k] = v[len(prefix):]
            else:
                strip_prefix_from_surface_ids(v, prefix)
    elif isinstance(data, list):
        for item in data:
            strip_prefix_from_surface_ids(item, prefix)

class A2UIMetadataInterceptor(ClientCallInterceptor):

    @override
    async def intercept(
        self,
        method_name: str,
        request_payload: dict[str, Any],
        http_kwargs: dict[str, Any],
        agent_card: AgentCard | None,
        context: ClientCallContext | None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Enables the A2UI extension header and adds A2UI client capabilities to remote agent message metadata."""
        logger.info(
            "Intercepting client call to method: "
            + method_name
            + " and payload "
            + json.dumps(request_payload)
        )

        if context and context.state and context.state.get(ACTIVE_UI_VERSION_STATE_KEY):
            # Add A2UI extension header
            a2ui_extension_uri = get_a2ui_extension_uri(
                context.state.get(ACTIVE_UI_VERSION_STATE_KEY)
            )
            http_kwargs["headers"] = {HTTP_EXTENSION_HEADER: a2ui_extension_uri}

            # Add A2UI client capabilities (supported catalogs, etc) to message metadata
            if (params := request_payload.get("params")) and params.get("message"):
                message = Message.model_validate(params["message"])
                client_capabilities = context.state.get(CLIENT_CAPABILITIES_STATE_KEY)
                if not message.metadata:
                    message.metadata = {}
                message.metadata[A2UI_CLIENT_CAPABILITIES_KEY] = client_capabilities
                logger.info(
                    "Added client capabilities to remote agent message metadata:"
                    f" {client_capabilities}"
                )

                # Data Model Stripping to prevent data leakage
                if message.metadata and (
                    data_model := message.metadata.get(A2UI_CLIENT_DATA_MODEL_KEY)
                ):
                    await A2uiSubagentMap.strip_unowned_surfaces_from_data_model(
                        agent_card.name if agent_card else None,
                        data_model,
                        context.state,
                    )

                # Strip the prefix before sending to the subagent so it matches their internal state
                                # Strip the prefix before sending to the subagent so it matches their internal state
                clean_name = ""
                if agent_card:
                    import re
                    clean_name = re.sub(r"[^0-9a-zA-Z_]+", "_", agent_card.name)
                    if clean_name == "":
                        clean_name = "_"
                    if clean_name[0].isdigit():
                        clean_name = f"_{clean_name}"
                prefix_to_strip = f"{clean_name}/" if clean_name else ""
                if prefix_to_strip:
                    for part in message.parts:
                        if is_a2ui_part(part) and getattr(part.root, "data", None):
                            strip_prefix_from_surface_ids(part.root.data, prefix_to_strip)
                    
                    if message.metadata and A2UI_CLIENT_DATA_MODEL_KEY in message.metadata:
                        data_model_dict = message.metadata[A2UI_CLIENT_DATA_MODEL_KEY]
                        if isinstance(data_model_dict, dict) and A2UI_CLIENT_DATA_MODEL_SURFACES_KEY in data_model_dict:
                            surfaces = data_model_dict[A2UI_CLIENT_DATA_MODEL_SURFACES_KEY]
                            if isinstance(surfaces, dict):
                                new_surfaces = {}
                                for k, v in surfaces.items():
                                    if k.startswith(prefix_to_strip):
                                        new_surfaces[k[len(prefix_to_strip):]] = v
                                    else:
                                        new_surfaces[k] = v
                                data_model_dict[A2UI_CLIENT_DATA_MODEL_SURFACES_KEY] = new_surfaces
                params["message"] = message.model_dump(
                    mode="json", exclude_none=True, by_alias=True
                )

        return request_payload, http_kwargs


class A2AClientFactoryWithA2UIMetadata(A2AClientFactory):

    @override
    def create(
        self,
        card: AgentCard,
        consumers: list[Consumer] | None = None,
        interceptors: list[ClientCallInterceptor] | None = None,
    ) -> Client:
        # Add A2UI metadata interceptor
        return super().create(
            card, consumers, (interceptors or []) + [A2UIMetadataInterceptor()]
        )


class OrchestratorAgentExecutor(A2aAgentExecutor):
    """Orchestrator AgentExecutor."""

    @classmethod
    async def programmatically_route_client_event_to_subagent(
        cls,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse:
        if (
            llm_request.contents
            and (last_content := llm_request.contents[-1]).parts
            and (a2a_part := convert_genai_part_to_a2a_part(last_content.parts[-1]))
        ):
            if target_agent := await A2uiSubagentMap.get_subagent_name_for_client_event(
                a2a_part, callback_context.state
            ):
                logger.info(
                    "Programmatically routing client event "
                    f"to subagent '{target_agent}'"
                )
                return LlmResponse(
                    content=genai_types.Content(
                        parts=[
                            genai_types.Part(
                                function_call=genai_types.FunctionCall(
                                    name="transfer_to_agent",
                                    args={"agent_name": target_agent},
                                )
                            )
                        ]
                    )
                )

        return None

    @classmethod
    async def create(
        cls, base_url: str, subagent_urls: List[str]
    ) -> tuple["OrchestratorAgentExecutor", AgentCard]:
        """Creates the OrchestratorAgentExecutor and AgentCard."""
        orchestrator_agent, agent_card = await cls._build_agent(
            base_url=base_url, subagent_urls=subagent_urls
        )

        return cls(agent=orchestrator_agent, agent_card=agent_card), agent_card

    @classmethod
    async def _build_agent(
        cls, base_url: str, subagent_urls: List[str]
    ) -> tuple[LlmAgent, AgentCard]:
        """Builds the LLM agent for the orchestrator_agent agent."""

        subagents = []
        supported_catalog_ids = set()
        skills = []
        extensions = []
        accepts_inline_catalogs = False
        for subagent_url in subagent_urls:
            async with httpx.AsyncClient() as httpx_client:
                resolver = A2ACardResolver(
                    httpx_client=httpx_client,
                    base_url=subagent_url,
                )

                subagent_card = await resolver.get_agent_card()
                for extension in subagent_card.capabilities.extensions or []:
                    if extension.uri.startswith(A2UI_EXTENSION_BASE_URI):
                        if extension.params:
                            supported_catalog_ids.update(
                                extension.params.get(
                                    AGENT_EXTENSION_SUPPORTED_CATALOG_IDS_KEY
                                )
                                or []
                            )
                            accepts_inline_catalogs |= bool(
                                extension.params.get(
                                    AGENT_EXTENSION_ACCEPTS_INLINE_CATALOGS_KEY
                                )
                            )
                        # Only append unique extensions
                        if not any(ext.uri == extension.uri for ext in extensions):
                            extensions.append(extension)
                skills.extend(subagent_card.skills)

                logger.info(
                    "Successfully fetched public agent card:"
                    + subagent_card.model_dump_json(indent=2, exclude_none=True)
                )

                # clean name for adk
                clean_name = re.sub(r"[^0-9a-zA-Z_]+", "_", subagent_card.name)
                if clean_name == "":
                    clean_name = "_"
                if clean_name[0].isdigit():
                    clean_name = f"_{clean_name}"

                # make remote agent
                description = json.dumps(
                    {
                        "id": clean_name,
                        "name": subagent_card.name,
                        "description": subagent_card.description,
                        "skills": [
                            {
                                "name": skill.name,
                                "description": skill.description,
                                "examples": skill.examples,
                                "tags": skill.tags,
                            }
                            for skill in subagent_card.skills
                        ],
                    },
                    indent=2,
                )
                remote_a2a_agent = RemoteA2aAgent(
                    clean_name,
                    subagent_card,
                    description=description,  # This will be appended to system instructions
                    a2a_client_factory=A2AClientFactoryWithA2UIMetadata(
                        config=A2AClientConfig(
                            httpx_client=httpx.AsyncClient(
                                timeout=httpx.Timeout(timeout=DEFAULT_TIMEOUT),
                            ),
                            streaming=False,
                            polling=False,
                            supported_transports=[A2ATransport.jsonrpc],
                        )
                    ),
                )
                subagents.append(remote_a2a_agent)

                logger.info(f"Created remote agent with description: {description}")

        LITELLM_MODEL = os.getenv("LITELLM_MODEL", "gemini/gemini-3.5-flash")
        agent = LlmAgent(
            model=Gemini(model=LITELLM_MODEL.replace("vertex_ai/", "").replace("gemini/", "")),
            name="orchestrator_agent",
            description="An agent that orchestrates requests to multiple other agents",
            instruction=(
                "You are Ollie, the Master Sales Assistant Orchestrator for Enterprise B2B Sales.\n"
                "Your primary role is to coordinate and route complex sales queries to your specialized sub-agents:\n"
                "- `subagent_financial_analyst`: Analyzes company financials, revenue, net income, margins, and financial trajectory.\n"
                "- `subagent_sales_performance_analyst`: Analyzes monthly sales performance, core/ICT targets vs actuals, and segment achievement.\n"
                "- `subagent_strategic_profiler`: Analyzes corporate SWOT, strategic priorities, spend outlook, and risk insights.\n"
                "- `subagent_competitor_benchmark`: Benchmarks competitor data and market positioning.\n"
                "- `subagent_product_recommender`: Evaluates product recommendations and cross-sell opportunities.\n"
                "- `subagent_salesteam_org_analyst`: Queries org structure, sales rep assignments, VP/SVP hierarchy, and contact info.\n\n"
                "Synthesize inputs from your sub-agents to deliver comprehensive, highly professional, executive-grade responses for sales reps and leaders.\n\n"
                "CRITICAL GUARDRAILS & SECURITY DIRECTIVES:\n"
                "1. OFF-TOPIC REJECTION: If the user asks non-business, non-sales, or personal questions unrelated to enterprise sales, B2B companies, financials, products, or sales organization hierarchy, politely decline by stating: \"I am specialized exclusively in enterprise B2B sales intelligence and organizational performance.\"\n"
                "2. FACTUAL GROUNDING: Always base factual claims strictly on database records retrieved by your sub-agents. Never fabricate corporate revenue numbers, employee names, or financial metrics."
            ),
            tools=[],
            planner=BuiltInPlanner(
                thinking_config=genai_types.ThinkingConfig(
                    include_thoughts=False,
                )
            ),
            sub_agents=subagents,
            before_model_callback=cls.programmatically_route_client_event_to_subagent,
        )

        orchestrator_extensions = [
            get_a2ui_agent_extension(VERSION_0_8, False, []),
            get_a2ui_agent_extension(
                VERSION_0_9,
                accepts_inline_catalogs,
                list(supported_catalog_ids),
            ),
        ]

        agent_card = AgentCard(
            name="Orchestrator Agent",
            description="This agent orchestrates requests to multiple subagents.",
            url=base_url,
            version="1.0.0",
            default_input_modes=["text", "text/plain"],
            default_output_modes=["text", "text/plain"],
            capabilities=AgentCapabilities(
                streaming=True,
                extensions=orchestrator_extensions,
            ),
            skills=skills,
        )

        return agent, agent_card

    def __init__(self, agent: LlmAgent, agent_card: AgentCard):
        self._agent_card = agent_card
        config = A2aAgentExecutorConfig(
            execute_interceptors=[
                ExecuteInterceptor(
                    after_event=self.after_event_save_surface_id_to_subagent_name
                )
            ],
        )

        runner = Runner(
            app_name=agent.name,
            agent=agent,
            artifact_service=InMemoryArtifactService(),
            session_service=InMemorySessionService(),
            memory_service=InMemoryMemoryService(),
        )

        super().__init__(runner=runner, config=config)

    @classmethod
    async def after_event_save_surface_id_to_subagent_name(
        cls,
        executor_context: ExecutorContext,
        a2a_event: A2AEvent,
        event: Event,
    ) -> Union[A2AEvent, list[A2AEvent], None]:
        agent = executor_context.runner.agent
        
        session = await executor_context.runner.session_service.get_session(
            app_name=executor_context.app_name,
            user_id=executor_context.user_id,
            session_id=executor_context.session_id,
        )
        invocation_context = executor_context.runner._new_invocation_context(session, invocation_id=event.invocation_id)

        # Try to populate subagent agent card if available.
        subagent_obj = None
        subagent_card = None
        if active_subagent_name := event.author:
            # We need to find the subagent by name
            if subagent_obj := next(
                (
                    sub
                    for sub in agent.sub_agents
                    if sub.name == active_subagent_name
                ),
                None,
            ):
                try:
                    subagent_card = json.loads(subagent_obj.description)
                except Exception:
                    logger.warning(
                        f"Failed to parse agent description for {active_subagent_name}"
                    )
        if subagent_card and hasattr(a2a_event, "metadata"):
            if a2a_event.metadata is None:
                a2a_event.metadata = {}
            a2a_event.metadata["a2a_subagent"] = subagent_card

        parts_list = []
        status = getattr(a2a_event, "status", None)
        if status:
            message = getattr(status, "message", None)
            if message:
                parts = getattr(message, "parts", None)
                if parts:
                    parts_list.append((message, parts))

        artifact = getattr(a2a_event, "artifact", None)
        if artifact:
            parts = getattr(artifact, "parts", None)
            if parts:
                parts_list.append((artifact, parts))

        if not parts_list:
            return a2a_event


        for parent, parts in parts_list:
            new_parts = []
            for a2a_part in parts:
                prefix = f"{event.author}/"
                if is_a2ui_part(a2a_part) and getattr(a2a_part.root, "data", None):
                    prefix_surface_ids(a2a_part.root.data, prefix)
                    # When a2a_part is serialized later, it'll use a2a_part.root.data
                    
                try:
                    await A2uiSubagentMap.update_from_server_event(
                        a2a_part,
                        event.author,
                        invocation_context.session_service,
                        invocation_context.session,
                    )
                except SurfaceIdAlreadyExistsError as e:
                    logger.error(str(e))
                    if subagent_obj:
                        error_msg = json.dumps({
                            A2UI_VERSION_KEY: "0.9",
                            A2UI_ERROR_KEY: {
                                A2UI_CODE_KEY: "SURFACE_ID_ALREADY_EXISTS",
                                A2UI_SURFACE_ID_KEY: e.surface_id,
                                A2UI_MESSAGE_KEY: (
                                    f"surfaceId '{e.surface_id}' already exists,"
                                    " surfaceIds must be globally unique"
                                ),
                            },
                        })
                        error_req = LlmRequest(
                            contents=[
                                genai_types.Content(
                                    parts=[genai_types.Part(text=error_msg)],
                                    role="user",
                                )
                            ]
                        )

                        async def _run_subagent_bg():
                            try:
                                await subagent_obj.run_async(error_req, invocation_context)
                            except Exception as ex:
                                logger.exception(f"Background subagent run failed: {ex}")

                        asyncio.create_task(_run_subagent_bg())
                    continue
                new_parts.append(a2a_part)
            parent.parts = new_parts

        logger.info(f"Returning A2A event: {a2a_event.model_dump_json(indent=2)}")
        return a2a_event

    @override
    async def _prepare_session(
        self,
        context: RequestContext,
        run_request: AgentRunRequest,
        runner: Runner,
    ):
        session = await super()._prepare_session(context, run_request, runner)

        active_ui_version = try_activate_a2ui_extension(context, self._agent_card)
        if active_ui_version:
            client_capabilities = (
                context.message.metadata.get(A2UI_CLIENT_CAPABILITIES_KEY)
                if context.message and context.message.metadata
                else {}
            )
            
            if not client_capabilities:
                client_capabilities = {}
            if "supportedCatalogIds" not in client_capabilities:
                client_capabilities["supportedCatalogIds"] = []
                
            canonical_basic = "https://a2ui.org/specification/v0_9/catalogs/basic/catalog.json"
            if canonical_basic not in client_capabilities["supportedCatalogIds"]:
                client_capabilities["supportedCatalogIds"].append(canonical_basic)

            await runner.session_service.append_event(
                session,
                Event(
                    invocation_id=new_invocation_context_id(),
                    author="system",
                    actions=EventActions(
                        state_delta={
                            # These values are used to configure A2UI messages to remote agent calls
                            ACTIVE_UI_VERSION_STATE_KEY: active_ui_version,
                            CLIENT_CAPABILITIES_STATE_KEY: client_capabilities,
                        }
                    ),
                ),
            )

        return session
