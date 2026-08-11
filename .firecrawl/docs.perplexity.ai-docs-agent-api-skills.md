> ## Documentation Index
>
> Fetch the complete documentation index at: [/llms.txt](https://docs.perplexity.ai/llms.txt)
>
> Use this file to discover all available pages before exploring further.

[Skip to main content](https://docs.perplexity.ai/docs/agent-api/skills#content-area)

Skills give the agent domain expertise on demand. The model sees each skill by name and description, and loads the full instructions only when it decides they are needed — a progressive disclosure pattern described in [Designing, Refining, and Maintaining Agent Skills](https://research.perplexity.ai/articles/designing-refining-and-maintaining-agent-skills-at-perplexity).

## [​](https://docs.perplexity.ai/docs/agent-api/skills\#why-use-skills)  Why use skills

- **Specialize.** Add document generation and domain workflows on top of base prompting.
- **Pay context only on use.** Until a skill is loaded, it costs only its name and description.
- **Compose.** Mix built-in skills with inline instructions in one request.

## [​](https://docs.perplexity.ai/docs/agent-api/skills\#how-skills-work)  How skills work

You pass a `skills` array on the request. Each entry is either a **built-in** selection from the catalog or an **inline** skill you define for the request.

| Stage | What the model sees |
| --- | --- |
| Discovery | An index of every skill’s name and description. |
| Load | The full skill body, returned when the model calls `load_skill`. |
| Files | Built-in skills mount supporting files into the sandbox on load. Inline skills return their instructions and mount nothing. |

The description is the routing trigger. Write it to tell the model when to load the skill.Skills run on the durable backend. Submit with `background: true` and poll as shown in [Background mode](https://docs.perplexity.ai/docs/agent-api/background-mode).

## [​](https://docs.perplexity.ai/docs/agent-api/skills\#available-skills)  Available skills

### [​](https://docs.perplexity.ai/docs/agent-api/skills\#built-in-skills)  Built-in skills

Select a built-in skill with one JSON object: `{ "type": "builtin", "name": "office/pdf" }`.

- office


## Office

Generate PDF, Word, PowerPoint, and Excel documents from scratch, with structural validation and visual QA. Select a specific leaf, or select `office` to grant all four at once and let the model pick the format.

| Name | Description | Selection |
| --- | --- | --- |
| `office` | Umbrella that grants all four leaves below. | `{ "type": "builtin", "name": "office" }` |
| `office/pdf` | Create PDF documents with page-by-page visual QA. | `{ "type": "builtin", "name": "office/pdf" }` |
| `office/docx` | Create editable Word documents (OOXML). | `{ "type": "builtin", "name": "office/docx" }` |
| `office/pptx` | Create PowerPoint presentations with slide-by-slide visual QA. | `{ "type": "builtin", "name": "office/pptx" }` |
| `office/xlsx` | Create Excel workbooks with verified formulas, charts, and visual QA. | `{ "type": "builtin", "name": "office/xlsx" }` |

Office skills create documents from scratch. They do not edit files you upload.

### [​](https://docs.perplexity.ai/docs/agent-api/skills\#inline-skills)  Inline skills

Use inline skills for one-off or account-specific guidance the model should load on demand: style guides, playbooks, design systems, house rules.

| Field | Description |
| --- | --- |
| `type` | Required. Must be `"inline"`. |
| `name` | Required. 1-64 characters; lowercase ASCII letters, digits, and single hyphens. |
| `description` | Required. 1-1,024 bytes. Written as the routing trigger. |
| `instructions` | Required. 1-65,536 bytes. The skill body the model reads on load. |

```
{
  "type": "inline",
  "name": "design-system",
  "description": "Load when creating documents that must follow the house design system.",
  "instructions": "Model: a 1970s letterpress broadsheet financial page. Paper #EDE9DE; body ink #232220; ..."
}
```

Inline skills have no files, no dependencies, no sandbox mounts, no reusable library, and are never echoed back in the response.

## [​](https://docs.perplexity.ai/docs/agent-api/skills\#example)  Example

Combine `office/pdf` with an inline `design-system` skill to render a house-styled one-page AI-industry stock report.

Python

Typescript

cURL

```
import time
from perplexity import Perplexity

client = Perplexity()

design_book = """
Model: a 1970s letterpress broadsheet financial page. One ink, gray paper.

Colors
- Paper #EDE9DE; tinted boxes and alternating table rows #E3DFD2.
- Body ink #232220 — soft, never hard black (ink spread on newsprint).
- Headlines and rules may deepen to #141311; faded ink #5C5850 for captions and secondary text.
- No second color anywhere. Up moves: bold with a ▲. Down moves: parentheses with a ▼.

Typography
- Body: low-contrast newspaper serif (Georgia, PT Serif, or Times), 9-10pt, justified and hyphenated.
- Headlines: bold condensed serif with a smaller deck beneath.
- Kickers and table headers: condensed grotesque caps (Franklin Gothic or Oswald), letterspaced.
- Tables: agate style — 7-8pt condensed, tabular figures.

Layout
- One page, ~18mm margins.
- Nameplate in blackletter or heavy serif, with a folio line (date, edition, price) set between an Oxford rule (thick over hairline).
- Ticker summary as a boxed agate strip below the nameplate.
- News timeline in 3-4 narrow justified columns divided by hairline column rules; each item opens with a bold caps dateline ('LONDON, JULY 17 —').
- Data table ruled with hairlines only.
- Pack the page — separate blocks with cutoff rules, not white space.

Imagery
- Grayscale halftone only, with a hairline keyline and an italic caption.

Avoid
- Second colors, gradients, shadows, rounded corners, sans-serif body text, and generous white space.
"""

response = client.responses.create(
    preset="xhigh",
    background=True,
    skills=[\
        {\
            "type": "inline",\
            "name": "design-system",\
            "description": "Load when creating documents that must follow the house design system.",\
            "instructions": design_book,\
        },\
        {"type": "builtin", "name": "office/pdf"},\
    ],
    input=(
        "Create a one-page AI-industry stock report. Include NVDA, MSFT, "
        "GOOGL, AMD, and AVGO with latest price and weekly move. Include "
        "this week's key AI news, labeled by date and tagged to the ticker "
        "it moved. Follow the design-system skill."
    ),
)

while response.status in ("queued", "in_progress"):
    time.sleep(2)
    response = client.responses.retrieve(response.id)

print(response.status)
```

```
import Perplexity from '@perplexity-ai/perplexity_ai';

const client = new Perplexity();

const designBook = `
Model: a 1970s letterpress broadsheet financial page. One ink, gray paper.

Colors
- Paper #EDE9DE; tinted boxes and alternating table rows #E3DFD2.
- Body ink #232220 — soft, never hard black (ink spread on newsprint).
- Headlines and rules may deepen to #141311; faded ink #5C5850 for captions and secondary text.
- No second color anywhere. Up moves: bold with a ▲. Down moves: parentheses with a ▼.

Typography
- Body: low-contrast newspaper serif (Georgia, PT Serif, or Times), 9-10pt, justified and hyphenated.
- Headlines: bold condensed serif with a smaller deck beneath.
- Kickers and table headers: condensed grotesque caps (Franklin Gothic or Oswald), letterspaced.
- Tables: agate style — 7-8pt condensed, tabular figures.

Layout
- One page, ~18mm margins.
- Nameplate in blackletter or heavy serif, with a folio line (date, edition, price) set between an Oxford rule (thick over hairline).
- Ticker summary as a boxed agate strip below the nameplate.
- News timeline in 3-4 narrow justified columns divided by hairline column rules; each item opens with a bold caps dateline ('LONDON, JULY 17 —').
- Data table ruled with hairlines only.
- Pack the page — separate blocks with cutoff rules, not white space.

Imagery
- Grayscale halftone only, with a hairline keyline and an italic caption.

Avoid
- Second colors, gradients, shadows, rounded corners, sans-serif body text, and generous white space.
`;

let response = await client.responses.create({
  preset: 'xhigh',
  background: true,
  skills: [\
    {\
      type: 'inline',\
      name: 'design-system',\
      description: 'Load when creating documents that must follow the house design system.',\
      instructions: designBook,\
    },\
    { type: 'builtin', name: 'office/pdf' },\
  ],
  input:
    'Create a one-page AI-industry stock report. Include NVDA, MSFT, ' +
    'GOOGL, AMD, and AVGO with latest price and weekly move. Include ' +
    'this week\'s key AI news, labeled by date and tagged to the ticker ' +
    'it moved. Follow the design-system skill.',
});

while (response.status === 'queued' || response.status === 'in_progress') {
  await new Promise((r) => setTimeout(r, 2000));
  response = await client.responses.retrieve(response.id);
}

console.log(response.status);
```

```
curl https://api.perplexity.ai/v1/agent \
  -H "Authorization: Bearer $PERPLEXITY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "preset": "xhigh",
    "background": true,
    "skills": [\
      {\
        "type": "inline",\
        "name": "design-system",\
        "description": "Load when creating documents that must follow the house design system.",\
        "instructions": "Model: a 1970s letterpress broadsheet financial page. One ink, gray paper.\nColors: paper #EDE9DE (tinted boxes and alternating table rows #E3DFD2); body ink #232220 (soft, never hard black); headlines and rules may deepen to #141311; faded ink #5C5850 for captions. No second color. Up moves: bold + ▲; down moves: parentheses + ▼.\nTypography: body low-contrast newspaper serif (Georgia, PT Serif, or Times), 9-10pt, justified and hyphenated; headlines bold condensed serif with a smaller deck; kickers and table headers condensed grotesque caps (Franklin Gothic or Oswald), letterspaced; tables agate 7-8pt condensed with tabular figures.\nLayout: one page, ~18mm margins; blackletter or heavy-serif nameplate with a folio line (date, edition, price) between an Oxford rule (thick over hairline); boxed agate ticker strip below; news timeline in 3-4 narrow justified columns with hairline column rules, items opening with bold caps datelines ('LONDON, JULY 17 —'); data table ruled with hairlines only. Pack the page — cutoff rules, not padding.\nImagery: grayscale halftone with hairline keyline and italic caption.\nAvoid: second colors, gradients, shadows, rounded corners, sans body text, generous white space."\
      },\
      { "type": "builtin", "name": "office/pdf" }\
    ],
    "input": "Create a one-page AI-industry stock report. Include NVDA, MSFT, GOOGL, AMD, and AVGO with latest price and weekly move. Include this week'\''s key AI news, labeled by date and tagged to the ticker it moved. Follow the design-system skill."
  }'

curl https://api.perplexity.ai/v1/agent/$RESPONSE_ID \
  -H "Authorization: Bearer $PERPLEXITY_API_KEY"
```

The output contains a `skill_loaded` item for each skill the model loaded. Retrieve file bytes through the files endpoints in [Working with files](https://docs.perplexity.ai/docs/agent-api/working-with-files).

## [​](https://docs.perplexity.ai/docs/agent-api/skills\#next-steps)  Next steps

[**Agent skills cookbook** \\
\\
Full walkthrough of the daily AI stock news PDF, including file download and the complete design book.](https://docs.perplexity.ai/docs/cookbook/articles/agent-skills/README)

[**Working with files**](https://docs.perplexity.ai/docs/agent-api/working-with-files)

[**Background mode**](https://docs.perplexity.ai/docs/agent-api/background-mode)

[**Agent API reference**](https://docs.perplexity.ai/api-reference/agent-post)

Was this page helpful?

YesNo

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.

Suggestions

What is the Agent API?Which Agent API model should I use?How do I create an API key?