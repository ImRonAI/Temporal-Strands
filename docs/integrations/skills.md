# Agent Skills — user-supplied collection discovery

Verified 2026-09-06/07 in `orchestrator/.venv` (Python 3.13.13). No wrappers,
no adapters: everything below goes through the installed `agentskills` package
(aws-samples/sample-strands-agents-agentskills) exactly as vendored.

## What changed

| File | Change |
| --- | --- |
| `orchestrator/skills_config.py` | `_DEFAULT_SKILLS_DIRS` now points at the user-supplied collection `../strands-tools/src/skills` (was the never-created `../strands-tools/skills`, which made discovery return 0). The old "src/skills is a dump — do not walk it" comment is superseded: the user explicitly supplied `src/skills` as the collection. `SKILLS_DIR` env override and last-wins name dedupe behavior unchanged. |
| `orchestrator/tests/test_skills_config.py` | Replaced the superseded `test_default_skills_dir_is_not_the_src_skills_dump` with `test_default_skills_dir_is_the_user_supplied_collection` (default == `src/skills`) and added `test_skills_dir_env_override_wins`. |
| `docs/integrations/skills.md` | This record (new). |

Not touched: `workflow.py`, `agent.json`, `run_worker.py`, `requirements.txt`,
`graph_tool.py`/graph work, skill content, `.env*`.

## Discovery results (native `agentskills.discover_skills`)

Run against `/Users/tims-stuff/Desktop/strands-tools/src/skills`:

- **Top-level directories:** 698 (1 without `SKILL.md`: `dist`)
- **Discovered by `agentskills.discover_skills`:** **557** valid skills
- **Skipped as invalid:** **140** (validator logs `Skipping invalid skill in …`).
  All 140 fail YAML frontmatter parsing, three flavors:
  - 137 × `Invalid YAML in frontmatter: While scanning` (e.g. `pa-clinical-policy`,
    `skills_pa-clinical-policy`, `awwwards-animations`, `ai-sdk`, `nextjs`, …)
  - 2 × `mapping values are not allowed here` (`check-agent-compatibility`, one other)
  - 1 × `while scanning a block scalar` (`theneoai-medical-biller`)
- **Duplicate names:** 104 name collisions across different paths (mostly
  `skills_<name>` copies shadowing `<name>`). `skills_config._dedupe_skills`
  keeps the last discovery per name and logs each collision.
- **Registered after dedupe:** **453** unique skills (this is what
  `list_skills` reports and what `configure_skills` registers for graph
  `skill_agent` nodes).

Discovery is metadata-only by design (Progressive Disclosure phase 1, ~100
tokens/skill). **453 registered ≠ 453 executed** — only metadata was loaded at
discovery; full-instruction loading was proven on samples below.

## Native loading proof

1. **`skills_loader` import + `list_skills`** — importing
   `orchestrator/skills_loader.py` ran `ensure_skills_configured()` (453
   registered) and `list_skills_text("conservation")` returned
   `5 skill(s) matching 'conservation' (of 453 registered)` including
   `ucsc-conservation-and-tfbs`.
2. **Pattern 2 inline activation** (`create_skill_tool` product):
   `skill(skill_name="ucsc-conservation-and-tfbs")` returned the full 7,323-char
   SKILL.md instruction body (a skill that ships both `references/` and
   `scripts/`), including its `uv run scripts/list_tracks.py …` usage.
3. **Pattern 3 / graph path** (`_create_skill_agent`, the same function
   `strands_graph_tool.skill_nodes.build_skill_agent` calls): built a real
   `strands.Agent` named `skill-ucsc-conservation-and-tfbs` whose system prompt
   is `generate_skill_instructions_prompt(instructions)`. **No LLM call was
   made** — construction only; no claim of end-to-end skill execution.

### Finding: references/scripts injection is NOT active at the pinned commit

`skill_nodes.py`'s docstring says `_create_skill_agent` "injects the skill's
references/ and scripts/ paths via `build_skill_header`". At the pinned commit
(`c5564fc…`, installed as `strands_agentskills 0.2.0`) that is **not true**:
`build_skill_header` exists in `agentskills/tool_utils.py` (and does list
`scripts/`, `references/`, `assets/` under an "Available Resources" header) but
is exported-only — `_create_skill_agent` builds the system prompt solely from
`generate_skill_instructions_prompt(instructions)` and never calls it.
Verified by source inspection and by asserting the built agent's system prompt
contains no `Available Resources` / resource paths. Skill sub-agents therefore
learn about scripts only from the SKILL.md text itself (which the sampled skill
does reference), plus whatever tools (`file_read`, `file_write`) they carry.

Blocker instruction for the parent (owns shared integration): if resource-path
injection is required, either bump the `strands_agentskills` pin to a commit
where `_create_skill_agent` uses `build_skill_header`, or adopt the first-party
SDK plugin `strands.vended_plugins.skills.AgentSkills` (ships inside
`strands-agents`; its `_format_skill_response` lists `scripts/`, `references/`,
`assets/` up to `max_resource_files=20`). Do NOT wrap or subclass locally —
that violates the no-wrapper rule; fix at the dependency or by switching to the
first-party plugin in the shared integration.

## Dependencies for the shared venv

**None new.** Everything used is already pinned in
`orchestrator/requirements.txt` and installed in `orchestrator/.venv`
(used read-only; no mutation performed):

```
strands-agents[gemini]==1.50.2
strands-agents-tools[local_chromium_browser]==0.8.5
strands_agentskills @ git+https://github.com/aws-samples/sample-strands-agents-agentskills@c5564fcd2e7c249ec57b32027ffbea49e9abeb7b
```

Provenance: `strands_agentskills` resolves to package version `0.2.0` from the
pinned commit. Core SDK pins unchanged.

## Test evidence

```bash
cd orchestrator
.venv/bin/python -m pytest tests/test_skills_config.py -q   # 5 passed
.venv/bin/python -m pytest tests -q                          # 284 passed
```

TDD sequence: new default-dir test failed first
(`…/strands-tools/skills != …/strands-tools/src/skills`), then passed after the
one-line `_DEFAULT_SKILLS_DIRS` fix. The unrelated OTel `collector.example`
export noise after the full run comes from `test_telemetry.py` fixtures, not
skills.

## Official references

- aws-samples/sample-strands-agents-agentskills — README, `docs/API.md`
  (`discover_skills`, `create_skill_tool`, `create_skill_agent_tool`),
  `docs/WHAT_IS_SKILL.md` (frontmatter: required `name` ≤64 kebab-case chars
  matching directory name, `description` ≤1024 chars; invalid entries are
  skipped with a log line, exactly what produced the 140 skips above).
- Strands first-party skills: `strands.vended_plugins.skills` (`Skill`,
  `AgentSkills` plugin) — https://strandsagents.com/docs/api/python/strands.vended_plugins.skills.agent_skills/index.md
- Custom tools guide: https://strandsagents.com/docs/user-guide/concepts/tools/index.md
