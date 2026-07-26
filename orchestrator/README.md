# Perplexity Agent Orchestrator

The main orchestrator behind this app's chat UI: a [Strands Agent](https://strandsagents.com/)
running as a durable, long-horizon [Temporal](https://temporal.io/) chat session, built to
Temporal's official Strands Agents integration guide —
**https://docs.temporal.io/develop/python/integrations/strands-agents** — followed exactly.

(The `/Applications/strands-temporal` local skill guide describes a different, non-working
architecture — passing a live `Model` object to `TemporalAgent(model=...)` inside `__init__` —
that fails at runtime with `PydanticSerializationError`, because Temporal has to serialize
activity inputs and a live `Model` instance isn't serializable. Don't follow that guide for
this integration. Everything here is verified against the installed `temporalio==1.30.0` /
`strands-agents==1.48.0` source and run end-to-end, including live in the actual chat UI.)

## Architecture

- **`run_worker.py`** builds `models: dict[str, Callable[[], Model]]` — **one factory per
  model id returned by Perplexity's live `GET /v1/models`**, fetched once at worker startup.
  This is the officially documented `models` mapping, populated dynamically instead of
  hand-typed, so every model the frontend's model picker can select has a matching factory.
  Each factory builds a `PerplexityModel` (`perplexity_model.py`) — a native Strands `Model`
  provider built on Perplexity's own official SDK (`perplexityai`), not the generic `openai`
  package. See that module's docstring and `CLAUDE.md`'s "Model provider" section for why.
- **`workflow.py`**'s `ChatWorkflow` is the docs' own "Handle long-running chat sessions"
  pattern:
  - `@workflow.update turn(prompt) -> str` — one message in, the reply out, same call.
  - `@workflow.signal end_chat()` — ends the session.
  - `@workflow.query messages()` — inspect history without sending a turn.
  - `@workflow.run run(input: ChatInput)` — builds `TemporalAgent(model=input.model_id, ...)`
    once, then waits until the session ends or Temporal suggests `continue_as_new`, drains any
    turn in flight, and hands off — carrying `agent.messages` forward so **conversation memory
    survives indefinitely; there's no length cap on a session.**
- **Resilience is explicit, not accidental** (all in `workflow.py`, all tunable constants at the
  top of the file):
  - `RetryPolicy(maximum_attempts=6, backoff_coefficient=2.0, maximum_interval=30s)` on every
    model call — rides out rate limits and brief network blips without retrying forever.
  - `heartbeat_timeout=30s` — this is what *activates* the SDK's built-in `auto_heartbeater` on
    the model activities (confirmed in `temporalio/contrib/strands/_model_activity.py`); without
    it, no heartbeating happens at all, and a genuinely-dead worker process isn't detected until
    the full `start_to_close_timeout` elapses.
  - `start_to_close_timeout=10min` per attempt (generous — long horizon tasks may involve real
    reasoning time) and `schedule_to_close_timeout=30min` as a hard ceiling across every retry,
    so a truly-broken upstream still fails within a bounded time instead of hanging.
  - `agent_hooks.py`'s `ModelCallBudget` caps model calls to 25 **within a single turn** — this
    is not theoretical, it closed a real bug: a malformed tool call once made the agent repeat
    the identical mistake 315+ times in one turn, live, with no built-in Strands cap to stop it.
- **`server.py`** — the FastAPI bridge the Next.js app actually talks to:
  - `POST /sessions` — `{model_id?}` → `{session_id}`
  - `POST /sessions/{id}/turns` — `{prompt}` → `{reply}`
  - `POST /sessions/{id}/end` — signals `end_chat`
  - `GET /health`

## Running it (4 processes)

```bash
cd orchestrator
uv venv .venv && source .venv/bin/activate
uv pip install -r requirements.txt

# Terminal 1
temporal server start-dev

# Terminal 2 (fetches the live Perplexity model catalog at startup — needs network + the key)
export PERPLEXITY_API_KEY=pplx-...
python run_worker.py

# Terminal 3
export PERPLEXITY_API_KEY=pplx-...
uvicorn server:app --port 8787

# Terminal 4, from the app root (not orchestrator/)
cd ..
pnpm run dev
```

Open `http://localhost:3000` — the main chat page (`app/page.tsx`) is wired to
`app/api/orchestrator/route.ts`, which talks to the FastAPI bridge above. It starts a session on
your first message, gets a `session_id` back as a custom `data-session` UI-message part, and
reuses that same session for every subsequent turn in the conversation — so context (like "my
favorite number is 42") is genuinely remembered across turns, not just within one request.

If Temporal / the worker / the bridge aren't running, `/api/orchestrator` reports a clear error
in the chat UI ("Orchestrator unreachable...") rather than hanging or silently falling back to
anything else. `app/api/chat/route.ts` (direct-to-Perplexity, no orchestrator, no session
memory) still exists as a separate, simpler route, but nothing currently points at it.

## Internal smoke test (not the app's interface)

```bash
python run_workflow.py "Explain Temporal in one paragraph" anthropic/claude-opus-4-8
```

Starts a session, sends one turn, prints the reply, ends the session. Useful for checking the
worker/Temporal connection in isolation; the real interface is `server.py`'s HTTP API above.

## Extending

If you add tools, human-in-the-loop approval, MCP servers, or OpenTelemetry tracing, follow the
official docs' patterns for each exactly — they're all in the same guide, further down the
page — rather than the local skill guide or anything invented ad hoc.
