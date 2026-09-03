"""Debug: dump activity_as_tool ToolSpecs and the _format_request tools array."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from temporalio.contrib.strands.workflow import activity_as_tool

import perplexity_operations as ops
from think_activity import think

ACTIVITIES = [
    ops.create_fast_agent_response,
    ops.create_low_agent_response,
    ops.create_medium_agent_response,
    ops.create_high_agent_response,
    ops.create_xhigh_agent_response,
    ops.create_wide_research_agent_response,
    ops.retrieve_agent_response,
    ops.list_agent_response_files,
    ops.download_agent_response_file,
    ops.list_agent_models,
    think,
]

specs = []
for act in ACTIVITIES:
    tool = activity_as_tool(act)
    spec = tool.tool_spec
    specs.append(spec)
    print("=" * 80)
    print(spec["name"])
    print(json.dumps(spec["inputSchema"]["json"], indent=2, default=str))

# Now show what PerplexityModel._format_request would send.
from perplexity_model import PerplexityModel

model = PerplexityModel(model_id="test-model", api_key="test", client=object())
request = model._format_request(
    [{"role": "user", "content": [{"text": "hi"}]}], specs, "sys"
)
print("#" * 80)
print("TOOLS ARRAY SENT TO POST /v1/responses:")
print(json.dumps(request["tools"], indent=2, default=str))
