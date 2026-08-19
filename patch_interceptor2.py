import re

with open("orchestrator_agent_executor.py", "r") as f:
    content = f.read()

replacement = """                clean_name = ""
                if agent_card:
                    clean_name = re.sub(r"[^0-9a-zA-Z_]+", "_", agent_card.name)
                    if clean_name == "":
                        clean_name = "_"
                    if clean_name[0].isdigit():
                        clean_name = f"_{clean_name}"
                prefix_to_strip = f"{clean_name}/" if clean_name else ""
"""

content = content.replace(
    'prefix_to_strip = f"{re.sub(r\'[^0-9a-zA-Z_]+\', \'_\', agent_card.name)}/" if agent_card else ""',
    replacement
)

with open("orchestrator_agent_executor.py", "w") as f:
    f.write(content)
