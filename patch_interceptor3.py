with open("orchestrator_agent_executor.py", "r") as f:
    content = f.read()

replacement = """                # Strip the prefix before sending to the subagent so it matches their internal state
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
"""

import re
content = re.sub(r'                clean_name = ""\n.*?(?=                params\["message"\] =)', replacement, content, flags=re.DOTALL)

with open("orchestrator_agent_executor.py", "w") as f:
    f.write(content)
