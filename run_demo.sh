#!/bin/bash
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

# run_demo.sh - run the orchestrator and all subagents with prefixes

# Kill all child processes when this script exits
trap 'kill $(jobs -p) 2>/dev/null' INT TERM EXIT

BLUE='\033[0;34m'
MAGENTA='\033[0;35m'
YELLOW='\033[0;33m'
GREEN='\033[0;32m'
CYAN='\033[0;36m'
RED_BOLD='\033[1;31m'
NC='\033[0m'

run_with_prefix() {
  local color=$1
  local prefix=$2
  shift 2
  "$@" 2>&1 | while IFS= read -r line; do
    echo -e "${color}[${prefix}]${NC} ${line}"
  done &
}

uv sync

run_with_prefix "$CYAN"     "SALES ANALYST" uv run --no-sync subagent_sales_performance_analyst.py --port=10015
run_with_prefix "$MAGENTA"  "COMPETITOR   " uv run --no-sync subagent_competitor_benchmark.py --port=10016
run_with_prefix "$YELLOW"   "FINANCIALS   " uv run --no-sync subagent_financial_analyst.py --port=10017
run_with_prefix "$GREEN"    "RECOMMENDER  " uv run --no-sync subagent_product_recommender.py --port=10018

run_orchestrator() {
  sleep 2 && \
  uv run --no-sync . --port=10002 \
    --subagent_urls=http://localhost:10015 \
    --subagent_urls=http://localhost:10016 \
    --subagent_urls=http://localhost:10017 \
    --subagent_urls=http://localhost:10018
}
run_with_prefix "$RED_BOLD" "ORCHESTRATOR" run_orchestrator

wait
