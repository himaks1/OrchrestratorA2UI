import re

with open("orchestrator_agent_executor.py", "r") as f:
    content = f.read()

content = content.replace(
    'prefix_to_strip = f"{agent_card.name}/" if agent_card else ""',
    'prefix_to_strip = f"{re.sub(r\'[^0-9a-zA-Z_]+\', \'_\', agent_card.name)}/" if agent_card else ""'
)

with open("orchestrator_agent_executor.py", "w") as f:
    f.write(content)

