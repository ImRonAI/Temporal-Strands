"""Debug: dump the workflow registry ToolSpecs and the _format_request tools array."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_worker import workflow_tool_specs

specs = workflow_tool_specs()
for spec in specs:
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
