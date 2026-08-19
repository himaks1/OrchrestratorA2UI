#!/bin/bash
uv run --no-sync subagent_front_desk.py --port=10011 &
uv run --no-sync subagent_housekeeping.py --port=10012 &
uv run --no-sync subagent_maintenance.py --port=10013 &
uv run --no-sync subagent_room_service.py --port=10014 &
uv run --no-sync subagent_sales_performance_analyst.py --port=10015 &

for port in 10011 10012 10013 10014 10015; do
  echo "Waiting for subagent on port $port..."
  while ! python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$port/.well-known/agent-card.json')" 2>/dev/null; do
    sleep 1
  done
done

uv run --no-sync . --host=0.0.0.0 --port=${PORT:-8080} \
    --subagent_urls=http://127.0.0.1:10011 \
    --subagent_urls=http://127.0.0.1:10012 \
    --subagent_urls=http://127.0.0.1:10013 \
    --subagent_urls=http://127.0.0.1:10014 \
    --subagent_urls=http://127.0.0.1:10015
