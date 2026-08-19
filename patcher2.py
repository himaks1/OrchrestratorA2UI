import re
import json

with open("orchestrator_agent_executor.py", "r") as f:
    content = f.read()

helpers = """
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

class A2UIMetadataInterceptor"""

content = re.sub(r'def prefix_surface_ids.*?class A2UIMetadataInterceptor', 'class A2UIMetadataInterceptor', content, flags=re.DOTALL)
content = content.replace("class A2UIMetadataInterceptor", helpers)

# Now fix the interceptor part
interceptor_block = """
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
                prefix_to_strip = f"{agent_card.name}/" if agent_card else ""
                if prefix_to_strip:
                    for part in message.parts:
                        if is_a2ui_part(part) and getattr(part.root, "data", None):
                            strip_prefix_from_surface_ids(part.root.data, prefix_to_strip)
"""
content = re.sub(
    r'# Data Model Stripping to prevent data leakage.*?(?=                params\["message"\] = message.model_dump\()',
    interceptor_block.strip() + '\n\n',
    content,
    flags=re.DOTALL
)

with open("orchestrator_agent_executor.py", "w") as f:
    f.write(content)

