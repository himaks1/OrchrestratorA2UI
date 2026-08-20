#!/bin/bash
uv run --no-sync subagent_sales_performance_analyst.py --port=10015 &
uv run --no-sync subagent_competitor_benchmark.py --port=10016 &
uv run --no-sync subagent_financial_analyst.py --port=10017 &
uv run --no-sync subagent_product_recommender.py --port=10018 &

for port in 10015 10016 10017 10018; do
  echo "Waiting for subagent on port $port..."
  while ! python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$port/.well-known/agent-card.json')" 2>/dev/null; do
    sleep 1
  done
done

uv run --no-sync . --host=0.0.0.0 --port=${PORT:-8080} \
    --subagent_urls=http://127.0.0.1:10015 \
    --subagent_urls=http://127.0.0.1:10016 \
    --subagent_urls=http://127.0.0.1:10017 \
    --subagent_urls=http://127.0.0.1:10018
