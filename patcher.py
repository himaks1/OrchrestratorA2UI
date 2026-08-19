import re

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

content = content.replace("class A2UIMetadataInterceptor", helpers)

interceptor_addition = """
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
                        a2a_part = convert_genai_part_to_a2a_part(part)
                        if a2a_part and is_a2ui_part(a2a_part) and getattr(a2a_part.root, "data", None):
                            strip_prefix_from_surface_ids(a2a_part.root.data, prefix_to_strip)
                            if part.text:
                                # Re-serialize to string and wrap in tags so genai_types doesn't break
                                part.text = f"<a2ui-json>\\n{json.dumps(a2a_part.root.data)}\\n</a2ui-json>"
"""

content = re.sub(
    r'# Data Model Stripping to prevent data leakage.*?context\.state,\n\s+\)',
    interceptor_addition.strip(),
    content,
    flags=re.DOTALL
)

prefix_addition = """
        for parent, parts in parts_list:
            new_parts = []
            for a2a_part in parts:
                prefix = f"{event.author}/"
                if is_a2ui_part(a2a_part) and getattr(a2a_part.root, "data", None):
                    prefix_surface_ids(a2a_part.root.data, prefix)
                    # When a2a_part is serialized later, it'll use a2a_part.root.data
                    
                try:"""

content = content.replace(
"""        for parent, parts in parts_list:
            new_parts = []
            for a2a_part in parts:
                try:""", prefix_addition)

with open("orchestrator_agent_executor.py", "w") as f:
    f.write(content)

