> ## Documentation Index
>
> Fetch the complete documentation index at: [/llms.txt](https://docs.perplexity.ai/llms.txt)
>
> Use this file to discover all available pages before exploring further.

[Skip to main content](https://docs.perplexity.ai/docs/agent-api/models#content-area)

## [​](https://docs.perplexity.ai/docs/agent-api/models\#available-models)  Available Models

The Agent API supports direct access to models from multiple providers. All models are accessed directly from first-party providers with transparent token-based pricing.Pricing rates are updated monthly and **reflect direct first-party provider pricing with no markup**. All charges are based on actual token consumption, and every API response includes exact token counts so you know your costs per request.The **Service tiers** column lists optional non-default tiers. An em dash means the model uses default processing only.

Looking for pre-configured model setups? See [**Presets**](https://docs.perplexity.ai/docs/agent-api/presets) — optimized for specific use cases.

- Anthropic

- OpenAI

- Google

- xAI

- DeepSeek

- Z.AI

- Moonshot AI

- NVIDIA

- Perplexity


## Anthropic

Claude Opus (highest reasoning), Sonnet (balanced), and Haiku (fastest, cheapest).

| Model | Input ($/1M) | Output ($/1M) | Cache read ($/1M) | Service tiers | Docs |
| --- | --- | --- | --- | --- | --- |
| `anthropic/claude-fable-5` | 10.00 | 50.00 | 1.00 | — | [Claude Fable 5](https://platform.claude.com/docs/en/about-claude/models/introducing-claude-fable-5-and-claude-mythos-5) |
| `anthropic/claude-opus-5` | 5.00 | 25.00 | 0.50 | — | [Claude Opus 5](https://platform.claude.com/docs/en/about-claude/models/overview) |
| `anthropic/claude-opus-4-8` | 5.00 | 25.00 | 0.50 | — | [Claude Opus 4.8](https://platform.claude.com/docs/en/about-claude/models/overview) |
| `anthropic/claude-opus-4-7` | 5.00 | 25.00 | 0.50 | — | [Claude Opus 4.7](https://www.anthropic.com/news/claude-opus-4-7) |
| `anthropic/claude-opus-4-6` | 5.00 | 25.00 | 0.50 | — | [Claude Opus 4.6](https://www.anthropic.com/news/claude-opus-4-6) |
| `anthropic/claude-opus-4-5` | 5.00 | 25.00 | 0.50 | — | [Claude Opus 4.5](https://www.anthropic.com/news/claude-opus-4-5) |
| `anthropic/claude-sonnet-5` | 2.00 | 10.00 | 0.20 | — | [Claude Sonnet 5](https://platform.claude.com/docs/en/about-claude/models/whats-new-sonnet-5) |
| `anthropic/claude-sonnet-4-6` | 3.00 | 15.00 | 0.30 | — | [Claude Sonnet 4.6](https://www.anthropic.com/news/claude-sonnet-4-6) |
| `anthropic/claude-sonnet-4-5` | 3.00 | 15.00 | 0.30 | — | [Claude Sonnet 4.5](https://www.anthropic.com/news/claude-sonnet-4-5) |
| `anthropic/claude-haiku-4-5` | 1.00 | 5.00 | 0.10 | — | [Claude Haiku 4.5](https://www.anthropic.com/news/claude-haiku-4-5) |

Requests that use an `anthropic/*` model must include `max_output_tokens`. If omitted, the API returns HTTP 400 with `validation failed: max_output_tokens is required when using Anthropic models`. `max_output_tokens` is a shared Agent API parameter, but this required condition applies only to Anthropic models.

## OpenAI

GPT-5 family — flagship, mini, and nano variants.

| Model | Input ($/1M) | Output ($/1M) | Cache read ($/1M) | Service tiers | Docs |
| --- | --- | --- | --- | --- | --- |
| `openai/gpt-5.6-sol` | 5.00 (≤272k)<br>10.00 (>272k) | 30.00 (≤272k)<br>45.00 (>272k) | 90% off input | `flex`, `priority` | [GPT-5.6](https://openai.com/index/gpt-5-6/) |
| `openai/gpt-5.6-terra` | 2.00 (≤272k)<br>4.00 (>272k) | 12.00 (≤272k)<br>18.00 (>272k) | 90% off input | `flex`, `priority` | [GPT-5.6](https://openai.com/index/gpt-5-6/) |
| `openai/gpt-5.6-luna` | 0.20 (≤272k)<br>0.40 (>272k) | 1.20 (≤272k)<br>1.80 (>272k) | 90% off input | `flex`, `priority` | [GPT-5.6](https://openai.com/index/gpt-5-6/) |
| `openai/gpt-5.5` | 5.00 (≤272k)<br>10.00 (>272k) | 30.00 (≤272k)<br>45.00 (>272k) | 0.50 | `flex`, `priority` | [GPT-5.5](https://developers.openai.com/api/docs/models/gpt-5.5) |
| `openai/gpt-5.4` | 2.50 (≤272k)<br>5.00 (>272k) | 15.00 (≤272k)<br>22.50 (>272k) | 0.25 | `flex`, `priority` | [GPT-5.4](https://platform.openai.com/docs/models/gpt-5.4) |
| `openai/gpt-5.4-mini` | 0.75 | 4.50 | 0.075 | `flex`, `priority` | [GPT-5.4 Mini](https://platform.openai.com/docs/models/gpt-5.4-mini) |
| `openai/gpt-5.4-nano` | 0.20 | 1.25 | 0.02 | `flex`, `priority` | [GPT-5.4 Nano](https://platform.openai.com/docs/models/gpt-5.4-nano) |
| `openai/gpt-5.2` | 1.75 | 14 | 0.175 | `flex`, `priority` | [GPT-5.2](https://platform.openai.com/docs/models/gpt-5.2) |
| `openai/gpt-5.1` | 1.25 | 10 | 0.125 | `flex`, `priority` | [GPT-5.1](https://platform.openai.com/docs/models/gpt-5.1) |
| `openai/gpt-5` | 1.25 | 10 | 0.125 | `flex`, `priority` | [GPT-5](https://platform.openai.com/docs/models/gpt-5) |
| `openai/gpt-5-mini` | 0.25 | 2 | 0.025 | `flex`, `priority` | [GPT-5 Mini](https://platform.openai.com/docs/models/gpt-5-mini) |

## Google

Gemini 3 family — Pro for long-context, Flash and Flash Lite for speed.

| Model | Input ($/1M) | Output ($/1M) | Cache read ($/1M) | Service tiers | Docs |
| --- | --- | --- | --- | --- | --- |
| `google/gemini-3.1-pro-preview` | 2.00 (≤200k)<br>4.00 (>200k) | 12.00 (≤200k)<br>18.00 (>200k) | 90% off input | — | [Gemini 3.1 Pro](https://ai.google.dev/gemini-api/docs/models#gemini-3.1-pro-preview) |
| `google/gemini-3.1-flash-lite` | 0.25 | 1.50 | 90% off input | — | [Gemini 3.1 Flash Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite) |
| `google/gemini-3.5-flash` | 1.50 | 9.00 | 0.15 | — | [Gemini 3.5 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash) |
| `google/gemini-3.5-flash-lite` | 0.30 | 2.50 | 0.03 | — | [Gemini 3.5 Flash Lite](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite) |
| `google/gemini-3.6-flash` | 1.50 | 7.50 | 0.15 | — | [Gemini 3.6 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.6-flash) |
| `google/gemini-3.7-flash` | 0.75 | 3.75 | 0.075 | — | [Gemini 3.7 Flash](https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash) |
| `google/gemini-3.8-flash` | 0.75 | 3.75 | 0.075 | — | [Gemini models](https://ai.google.dev/gemini-api/docs/models) |
| `google/gemini-3-flash-preview` | 0.50 | 3.00 | 90% off input | — | [Gemini 3.0 Flash](https://ai.google.dev/gemini-api/docs/models#gemini-3-flash-preview) |

## xAI

Grok 4.6, 4.5, 4.3, and 4.20 variants: flagship, reasoning, non-reasoning, and multi-agent.

| Model | Input ($/1M) | Output ($/1M) | Cache read ($/1M) | Service tiers | Docs |
| --- | --- | --- | --- | --- | --- |
| `xai/grok-4.6` | 2.00 (≤200k)<br>4.00 (>200k) | 6.00 (≤200k)<br>12.00 (>200k) | 0.50 (≤200k)<br>1.00 (>200k) | — | [Grok 4.6](https://docs.x.ai/developers/models/grok-4.6) |
| `xai/grok-4.5` | 2.00 (≤200k)<br>4.00 (>200k) | 6.00 (≤200k)<br>12.00 (>200k) | 0.30 (≤200k)<br>0.60 (>200k) | — | [Grok 4.5](https://docs.x.ai/developers/models) |
| `xai/grok-4.3` | 1.25 (≤200k)<br>2.50 (>200k) | 2.50 (≤200k)<br>5.00 (>200k) | 0.20 | — | [Grok 4.3](https://docs.x.ai/developers/models) |
| `xai/grok-4.20-reasoning` | 1.25 (≤200k)<br>2.50 (>200k) | 2.50 (≤200k)<br>5.00 (>200k) | 0.20 | — | [Grok 4.20 Reasoning](https://docs.x.ai/developers/models) |
| `xai/grok-4.20-non-reasoning` | 1.25 (≤200k)<br>2.50 (>200k) | 2.50 (≤200k)<br>5.00 (>200k) | 0.20 | — | [Grok 4.20 Non Reasoning](https://docs.x.ai/developers/models) |
| `xai/grok-4.20-multi-agent` | 1.25 (≤200k)<br>2.50 (>200k) | 2.50 (≤200k)<br>5.00 (>200k) | 0.20 | — | [Grok 4.20 Multi-Agent](https://docs.x.ai/developers/models) |

## DeepSeek

DeepSeek V4 Flash 0731 — a fast, efficient open reasoning model with a 1M-token context window.

| Model | Input ($/1M) | Output ($/1M) | Cache read ($/1M) | Service tiers | Docs |
| --- | --- | --- | --- | --- | --- |
| `perplexity/deepseek-v4-flash-0731` | 0.13 | 0.26 | 0.028 | — | [DeepSeek](https://huggingface.co/deepseek-ai) |

## Z.AI

GLM 5.2, GLM 5.3, and GLM 5.3 Flash — Z.AI reasoning models.

| Model | Input ($/1M) | Output ($/1M) | Cache read ($/1M) | Service tiers | Docs |
| --- | --- | --- | --- | --- | --- |
| `perplexity/glm-5.2` | 1.40 | 4.40 | 0.14 | — | [GLM](https://docs.z.ai/) |
| `perplexity/glm-5.3` | 1.40 | 4.40 | 0.26 | — | [GLM](https://docs.z.ai/) |
| `perplexity/glm-5.3-flash` | 0.15 | 0.50 | 0.03 | — | [GLM-5.3 Flash](https://huggingface.co/zai-org/GLM-5.3-Flash) |

## Moonshot AI

Kimi K3 — Moonshot AI’s flagship reasoning model — and Kimi K2.7 Code for coding and agentic tasks.

| Model | Input ($/1M) | Output ($/1M) | Cache read ($/1M) | Service tiers | Docs |
| --- | --- | --- | --- | --- | --- |
| `perplexity/kimi-k3` | 3.00 | 15.00 | 0.30 | — | [Kimi K3](https://huggingface.co/moonshotai/Kimi-K3) |
| `perplexity/kimi-k2.7-code` | 0.95 | 4.00 | 0.19 | — | [Kimi K2](https://platform.moonshot.ai/docs) |

Kimi K3 accepts `minimal`, `low`, `medium`, `high`, `xhigh`, and `max` reasoning effort. `minimal` uses low effort, while `xhigh` and `max` use maximum effort. Reasoning tokens are billed at the output-token rate.

## NVIDIA

Nemotron open-weight reasoning models.

| Model | Input ($/1M) | Output ($/1M) | Cache read ($/1M) | Service tiers | Docs |
| --- | --- | --- | --- | --- | --- |
| `perplexity/nemotron-3.5-lightning-30b-a3b` | 0.0115 | 0.17 | 0.00115 | — | [Nemotron 3.5 Lightning](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4) |
| `perplexity/nemotron-3-ultra-550b-a55b` | 0.25 | 2.50 | 0.25 | — | [Nemotron 3 Ultra](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Ultra-550B-A55B-BF16) |

## Perplexity

Sonar — Perplexity’s grounded search model.

| Model | Input ($/1M) | Output ($/1M) | Cache read ($/1M) | Service tiers | Docs |
| --- | --- | --- | --- | --- | --- |
| `perplexity/sonar` | 0.25 | 2.50 | 0.0625 | — | [Sonar](https://docs.perplexity.ai/docs/sonar/models/sonar) |

## [​](https://docs.perplexity.ai/docs/agent-api/models\#service-tiers)  Service tiers

Omit `service_tier`, or set it to `auto` or `default`, to use default processing. `flex` uses lower-cost, best-effort capacity at 0.5× the listed token prices. `priority` uses higher-priority processing at 2× the listed token prices. `fast` is accepted as an alias for `priority`.When you provide `model` or `models`, the Agent API applies `flex` or `priority` only if the selected model—or every model in a fallback list—supports that tier. An unsupported tier does not cause the request to be rejected; the API ignores `service_tier` and uses default processing. Requests that specify only a preset or profile retain the tier until the model is resolved. The response’s `service_tier` field reports the tier that served the request.

Not all third-party models support all features (e.g., reasoning, tools). Check model documentation for specific capabilities.

## [​](https://docs.perplexity.ai/docs/agent-api/models\#estimate-your-cost)  Estimate your cost

Agent API

TotalPer call

Usage
?

Input Tokens (M)

Output Tokens (M)

Tools (total)

Model?base rate

AllAnthropicOpenAIGooglexAIDeepSeekZ.AIMoonshot AINVIDIAPerplexity

Sort:CostName

nemotron-3.5-lightning-30b-a3b
In $0.0115 · Out $0.17 /1M$2.28
deepseek-v4-flash-0731
In $0.13 · Out $0.26 /1M$9.10
gpt-5.6-luna
In $0.20 · Out $1.20 /1M$22
gpt-5.4-nano
In $0.20 · Out $1.25 /1M$22.50
gemini-3.1-flash-lite
In $0.25 · Out $1.50 /1M$27.50
gpt-5-mini
In $0.25 · Out $2 /1M$32.50
nemotron-3-ultra-550b-a55b
In $0.25 · Out $2.50 /1M$37.50
sonar
In $0.25 · Out $2.50 /1M$37.50
gemini-3.5-flash-lite
In $0.30 · Out $2.50 /1M$40
gemini-3-flash-preview
In $0.50 · Out $3 /1M$55
gemini-3.7-flash
In $0.75 · Out $3.75 /1M$75
gemini-3.8-flash
In $0.75 · Out $3.75 /1M$75
gpt-5.4-mini
In $0.75 · Out $4.50 /1M$82.50
grok-4.3
In $1.25 · Out $2.50 /1M$87.50
grok-4.20-reasoning
In $1.25 · Out $2.50 /1M$87.50
grok-4.20-non-reasoning
In $1.25 · Out $2.50 /1M$87.50
grok-4.20-multi-agent
In $1.25 · Out $2.50 /1M$87.50
kimi-k2.7-code
In $0.95 · Out $4 /1M$87.50
claude-haiku-4-5
In $1 · Out $5 /1M$100
glm-5.2
In $1.40 · Out $4.40 /1M$114
glm-5.3
In $1.40 · Out $4.40 /1M$114
gemini-3.6-flash
In $1.50 · Out $7.50 /1M$150
grok-4.6
In $2 · Out $6 /1M$160
grok-4.5
In $2 · Out $6 /1M$160
gpt-5.1
In $1.25 · Out $10 /1M$162.50
gpt-5
In $1.25 · Out $10 /1M$162.50
gemini-3.5-flash
In $1.50 · Out $9 /1M$165
claude-sonnet-5
In $2 · Out $10 /1M$200
gpt-5.6-terra
In $2 · Out $12 /1M$220
gemini-3.1-pro-preview
In $2 · Out $12 /1M$220
gpt-5.2
In $1.75 · Out $14 /1M$227.50
gpt-5.4
In $2.50 · Out $15 /1M$275
claude-sonnet-4-6
In $3 · Out $15 /1M$300
claude-sonnet-4-5
In $3 · Out $15 /1M$300
kimi-k3
In $3 · Out $15 /1M$300
claude-opus-5
In $5 · Out $25 /1M$500
claude-opus-4-8
In $5 · Out $25 /1M$500
claude-opus-4-7
In $5 · Out $25 /1M$500
claude-opus-4-6
In $5 · Out $25 /1M$500
claude-opus-4-5
In $5 · Out $25 /1M$500
gpt-5.6-sol
In $5 · Out $30 /1M$550
gpt-5.5
In $5 · Out $30 /1M$550
claude-fable-5
In $10 · Out $50 /1M$1,000

Agent API estimate: $22 for your total usage

Estimated cost

$22

Total usage · 50M in · 10M out

Model: gpt-5.6-luna

Show breakdown

Copy estimateView formula

Estimate only. Final cost is metered from each response’s usage field - view it in the [API Portal](https://docs.perplexity.ai/docs/getting-started/projects#accessing-the-api-portal).

## [​](https://docs.perplexity.ai/docs/agent-api/models\#using-a-model)  Using a Model

Python

Typescript

cURL

```
from perplexity import Perplexity

client = Perplexity()

response = client.responses.create(
    model="openai/gpt-5.6-sol",
    input="Explain the difference between supervised and unsupervised learning in machine learning.",
    max_output_tokens=300,
)

print(f"Response ID: {response.id}")
print(response.output_text)
```

```
import Perplexity from '@perplexity-ai/perplexity_ai';

const client = new Perplexity();

const response = await client.responses.create({
    model: "openai/gpt-5.6-sol",
    input: "Explain the difference between supervised and unsupervised learning in machine learning.",
    max_output_tokens: 300,
});

console.log(`Response ID: ${response.id}`);
console.log(response.output_text);
```

```
curl https://api.perplexity.ai/v1/agent \
  -H "Authorization: Bearer $PERPLEXITY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai/gpt-5.6-sol",
    "input": "Explain the difference between supervised and unsupervised learning in machine learning.",
    "max_output_tokens": 300
  }' | jq
```

Response

```
{
  "id": "resp_85783af3-39c4-4565-9f09-144482151abf",
  "created_at": 1779391438,
  "model": "openai/gpt-5.1",
  "object": "response",
  "output": [\
    {\
      "results": [\
        {\
          "id": 1,\
          "snippet": "Supervised learning is a machine learning technique that uses labeled data sets to train artificial intelligence (AI) models to identify the underlying patterns and relationships.\nThe goal of the learning process is to create a model that can predict correct outputs on new real-world data.\n...\nLabeled training data provides a “ground truth,” explicitly teaching the model to identify the relationships between features and data labels.\n...\nSupervised learning relies on ground truth data to teach a model the relationships between inputs and outputs.",\
          "title": "What Is Supervised Learning? | IBM",\
          "url": "https://www.ibm.com/think/topics/supervised-learning",\
          "date": "2025-09-12",\
          "last_updated": "2026-03-31",\
          "source": "web"\
        },\
        {\
          "id": 2,\
          "snippet": "Unsupervised learning, also known as unsupervised machine learning, uses machine learning (ML) algorithms to analyze and cluster unlabeled data sets.\nThese algorithms discover hidden patterns or data groupings without the need for human intervention.\n...\nUnsupervised learning and supervised learning are frequently discussed together.\nUnlike unsupervised learning algorithms, supervised learning algorithms use labeled data.\nFrom that data, it either predicts future outcomes or assigns data to specific categories based on the regression or classification problem that it is trying to solve.\nWhile supervised learning algorithms tend to be more accurate than unsupervised learning models, they require upfront human intervention to label the data appropriately.",\
          "title": "What Is Unsupervised Learning? - IBM",\
          "url": "https://www.ibm.com/think/topics/unsupervised-learning",\
          "date": "2021-09-23",\
          "last_updated": "2026-03-31",\
          "source": "web"\
        },\
        {\
          "id": 3,\
          "snippet": "The difference between supervised and unsupervised **learning lies in how they use data and their goals**.\n**Supervised learning** relies on **labeled datasets, where each input is paired with a corresponding output label**.\nThe goal is to learn the relationship between inputs and outputs so the model can predict outcomes for new data, such as classifying emails as spam or not spam.\nIn contrast, **unsupervised learning** works **with unlabeled data aiming to uncover hidden patterns or structures within the dataset** such as grouping customers based on their shopping habits or detecting anomalies in a dataset.\n> Overall, supervised learning excels in predictive tasks with known outcomes, while unsupervised learning is ideal for discovering relationships and trends in raw data.\n...\nLabeled data means that each example in the dataset comes with a correct answer or output.\nIn supervised learning process:\n- Machine is given a dataset with input features (like age, salary, or temperature) and corresponding labels (like \"yes/no,\" \"high/low,\" or \"rainy/sunny\").\n- Then machine learns dataset by finding patterns in the data.\nFor example, it might learn that if the temperature is high, it’s likely to be sunny.\n- Once trained, the machine can predict the label for new input data.\nFor instance, if you give it a new temperature value, it can predict whether it will be sunny or rainy.",\
          "title": "Difference between Supervised and Unsupervised Learning",\
          "url": "https://www.geeksforgeeks.org/machine-learning/difference-between-supervised-and-unsupervised-learning/",\
          "date": "2025-07-11",\
          "last_updated": "2026-05-19",\
          "source": "web"\
        },\
        {\
          "id": 4,\
          "snippet": "Supervised learning is a type of machine learning where a model learns from labelled data, meaning each input has a correct output.\nThe model compares its predictions with actual results and improves over time to increase accuracy.",\
          "title": "Supervised Machine Learning - GeeksforGeeks",\
          "url": "https://www.geeksforgeeks.org/machine-learning/supervised-machine-learning/",\
          "date": "2026-05-09",\
          "last_updated": "2026-05-19",\
          "source": "web"\
        },\
        {\
          "id": 5,\
          "snippet": "Unsupervised Learning is a type of machine learning where the model works without labelled data.\nIt learns patterns on its own by grouping similar data points or finding hidden structures without any human intervention.",\
          "title": "Unsupervised Machine Learning - GeeksforGeeks",\
          "url": "https://www.geeksforgeeks.org/machine-learning/unsupervised-learning/",\
          "date": "2026-04-30",\
          "last_updated": "2026-05-19",\
          "source": "web"\
        },\
        {\
          "id": 6,\
          "snippet": "The biggest difference between supervised and unsupervised machine learning is the type of data used.\nSupervised learning uses labeled training data, and unsupervised learning does not.\nMore simply, supervised learning models have a baseline understanding of what the correct output values *should* be.\nWith supervised learning, an algorithm uses a sample dataset to train itself to make predictions, iteratively adjusting itself to minimize error.\nThese datasets are labeled for context, providing the desired output values to enable a model to give a “correct” answer.",\
          "title": "Supervised vs. unsupervised learning - Google Cloud",\
          "url": "https://cloud.google.com/discover/supervised-vs-unsupervised-learning",\
          "date": null,\
          "last_updated": "2026-05-18",\
          "source": "web"\
        },\
        {\
          "id": 7,\
          "snippet": "Supervised learning is a category of machine learning that uses labeled datasets to train algorithms to predict outcomes and recognize patterns.\nUnlike unsupervised learning, supervised learning algorithms are given labeled training to learn the relationship between the input and the outputs.\n...\nThe data used in supervised learning is labeled — meaning that it contains examples of both inputs (called features) and correct outputs (labels).\n...\nWhen it comes to understanding the difference between supervised learning vs. unsupervised learning, the primary difference is the type of input data used to train the model.\nSupervised learning uses labeled training datasets to try and teach a model a specific, pre-defined goal.",\
          "title": "What is Supervised Learning? | Google Cloud",\
          "url": "https://cloud.google.com/discover/what-is-supervised-learning",\
          "date": "2025-04-12",\
          "last_updated": "2026-05-18",\
          "source": "web"\
        },\
        {\
          "id": 8,\
          "snippet": "Unsupervised learning in artificial intelligence is a type of machine learning that learns from data without human supervision.\nUnlike supervised learning, unsupervised machine learning models are given unlabeled data and allowed to discover patterns and insights without any explicit guidance or instruction.\n...\nAs the name suggests, unsupervised learning uses self-learning algorithms—they learn without any labels or prior training.\nInstead, the model is given raw, unlabeled data and has to infer its own rules and structure the information based on similarities, differences, and patterns without explicit instructions on how to work with each piece of data.\n...\nThe main difference between supervised learning and unsupervised learning is the type of input data that you use.\nUnlike unsupervised machine learning algorithms, supervised learning relies on labeled training data to determine whether pattern recognition within a dataset is accurate.\nThe goals of supervised learning models are also predetermined, meaning that the type of output of a model is already known before the algorithms are applied.\nIn other words, the input is mapped to the output based on the training data.",\
          "title": "What is unsupervised learning? - Google Cloud",\
          "url": "https://cloud.google.com/discover/what-is-unsupervised-learning",\
          "date": null,\
          "last_updated": "2026-05-19",\
          "source": "web"\
        },\
        {\
          "id": 9,\
          "snippet": "- Supervised vs. unsupervised learning serve different purposes: supervised learning uses labeled data to make precise predictions and classifications, while unsupervised learning finds hidden patterns in raw, unlabeled data, making each better suited for different business goals.\n...\nIn supervised learning, models are trained using labeled data, where each input is paired with a known output.\nThe model learns by comparing its predictions against these correct answers and iteratively reducing error.\nAt the core of this process are machine learning models that learn explicit relationships between features and outcomes.\nThe presence of labeled data provides clear guidance, making supervised learning well-suited for problems where accuracy, traceability and repeatability are essential.\n...\nSupervised learning predicts known outcomes using labeled data.\nUnsupervised learning discovers patterns in unlabeled data.\n...\nSupervised machine learning excels when you have labeled data and need precise, accountable predictions or classifications.",\
          "title": "Supervised vs Unsupervised Learning - Databricks",\
          "url": "https://www.databricks.com/blog/supervised-vs-unsupervised-learning",\
          "date": "2026-02-17",\
          "last_updated": "2026-05-20",\
          "source": "web"\
        },\
        {\
          "id": 10,\
          "snippet": "Supervised learning algorithms train on sample data that specifies both the algorithm's input and output.\nFor example, the data could be images of handwritten numbers that are annotated to indicate which numbers they represent.\n...\nIn supervised learning, you train the model with a set of input data and a corresponding set of paired labeled output data.\nThe labeling is typically done manually.\n...\n|What is it?|You train the model with a set of input data and a corresponding set of paired labeled output data.|You train the model to discover hidden patterns in unlabeled data.|",\
          "title": "Supervised vs Unsupervised Learning - Difference Between ... - AWS",\
          "url": "https://aws.amazon.com/compare/the-difference-between-machine-learning-supervised-and-unsupervised/",\
          "date": "2026-05-13",\
          "last_updated": "2026-05-20",\
          "source": "web"\
        },\
        {\
          "id": 11,\
          "snippet": "In a supervised learning model, the algorithm learns on a labeled dataset, providing an answer key that the algorithm can use to evaluate its accuracy on training data.\n...\nIf you’re learning a task under supervision, someone is present judging whether you’re getting the right answer.\nSimilarly, in supervised learning, that means having a full set of labeled data while training an algorithm.\nFully labeled means that each example in the training dataset is tagged with the answer the algorithm should come up with on its own.",\
          "title": "NVIDIA Blog: Supervised Vs. Unsupervised Learning",\
          "url": "https://blogs.nvidia.com/blog/supervised-unsupervised-learning/",\
          "date": "2018-08-02",\
          "last_updated": "2026-04-13",\
          "source": "web"\
        },\
        {\
          "id": 12,\
          "snippet": "**Supervised Learning** is a machine learning approach where models are trained on labeled data—input examples paired with correct output answers.\nThe algorithm learns to map inputs to outputs by studying these examples, adjusting its parameters to minimize errors between its predictions and the known correct answers.",\
          "title": "What is Supervised Learning? - Stanford HAI",\
          "url": "https://hai.stanford.edu/ai-definitions/what-is-supervised-learning",\
          "date": "2024-09-10",\
          "last_updated": "2026-05-06",\
          "source": "web"\
        },\
        {\
          "id": 13,\
          "snippet": "**Unsupervised learning** is a framework in machine learning where, in contrast to supervised learning, algorithms learn patterns exclusively from unlabeled data.",\
          "title": "Unsupervised learning - Wikipedia",\
          "url": "https://en.wikipedia.org/wiki/Unsupervised_learning",\
          "date": "2003-05-25",\
          "last_updated": "2026-03-31",\
          "source": "web"\
        },\
        {\
          "id": 14,\
          "snippet": "The difference between supervised and unsupervised learning is simple: it's about how much human guidance you give the machine learning algorithm.\n...\nIn supervised learning, humans provide more guidance by showing the algorithm examples with the correct answers.\nYou're essentially teaching it by example.\n**How it works:** You give the algorithm lots of data that includes both the question AND the answer, so it can learn the pattern.\n...\n**Supervised Learning:**\n- **Needs labeled training data** (humans must provide the \"right answers\")\n- **More human work upfront** to create training examples\n- **Predictable results** - you know what you're trying to achieve\n...\n**Use Supervised Learning When:**\n- You know what you want to predict\n- You have examples of correct answers\n- You want specific, measurable results\n- You have time to create labeled training data\n...\nSupervised learning is great when you know what you're trying to achieve and have examples to learn from.\nUnsupervised learning is perfect for exploration and discovery when you want to understand what's hidden in your data.",\
          "title": "Supervised vs. Unsupervised Learning: The Basics Explained | TDWI",\
          "url": "https://tdwi.org/blogs/ai-101/2025/09/supervised-vs-unsupervised-learning.aspx",\
          "date": "2025-09-20",\
          "last_updated": "2026-05-21",\
          "source": "web"\
        }\
      ],\
      "type": "search_results",\
      "queries": [\
        "supervised learning definition",\
        "unsupervised learning definition",\
        "difference between supervised and unsupervised learning"\
      ]\
    },\
    {\
      "id": "msg_edef631a-cd00-4ba7-85ab-cea139ca9f13",\
      "content": [\
        {\
          "text": "Supervised learning learns from **labeled** examples to predict known outcomes, while unsupervised learning finds structure in **unlabeled** data without predefined outputs.[web:1][web:2][web:6][web:8]\n\n## Core idea\n\n- Supervised learning uses datasets where each input (features) comes with a correct output label, and the model learns a mapping from inputs to outputs to make predictions on new data.[web:1][web:7][web:12]  \n- Unsupervised learning uses datasets that have inputs only, with no labels, and algorithms try to discover patterns such as clusters, groups, or anomalies in the data.[web:2][web:5][web:8][web:13]\n\n## Data and “answers”\n\n- In supervised learning, training data is “labeled,” meaning each example is paired with the right answer (for example, an email plus a tag “spam” or “not spam”).[web:1][web:4][web:7][web:10][web:12]  \n- In unsupervised learning, the data is “unlabeled”; the model receives raw examples and must organize them or detect structure without being told the correct answer for each case.[web:2][web:5][web:8][web:13]\n\n## Typical goals\n\n- Supervised learning aims at prediction: classification (discrete labels like spam/not spam) and regression (continuous values like house prices), with a clear, predefined target variable.[web:1][web:3][web:9]  \n- Unsupervised learning aims at pattern discovery: clustering similar items, finding hidden groupings, dimensionality reduction, or detecting outliers without a predefined target.[web:2][web:3][web:5][web:8][web:9]\n\n## Examples of each\n\n- Common supervised methods and tasks include logistic regression for spam detection, decision trees for credit approval, and image classifiers trained on labeled pictures of objects.[web:1][web:4][web:7][web:9][web:10]  \n- Common unsupervised methods and tasks include k-means clustering for grouping customers by behavior, anomaly detection in network traffic, and algorithms like PCA for compressing or visualizing high-dimensional data.[web:2][web:3][web:5][web:8][web:9]\n\n## Practical trade‑offs\n\n- Supervised learning typically delivers more accurate, measurable predictions but requires substantial human effort to create labeled datasets and a clear definition of the prediction goal.[web:2][web:6][web:7][web:9][web:14]  \n- Unsupervised learning requires less upfront labeling work and is well suited for exploration and discovering unknown structure, but its results are often harder to evaluate because there is no single “correct” answer.[web:2][web:3][web:8][web:9][web:14]",\
          "type": "output_text",\
          "annotations": [],\
          "logprobs": []\
        }\
      ],\
      "role": "assistant",\
      "status": "completed",\
      "type": "message"\
    }\
  ],
  "status": "completed",
  "error": null,
  "usage": {
    "input_tokens": 6599,
    "output_tokens": 657,
    "total_tokens": 7256,
    "cost": {
      "currency": "USD",
      "input_cost": 0.00377,
      "output_cost": 0.00657,
      "total_cost": 0.01329,
      "cache_creation_cost": null,
      "cache_read_cost": 0.00045,
      "tool_calls_cost": 0.0025
    },
    "input_tokens_details": {
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 3584,
      "cached_tokens": 3584
    },
    "tool_calls_details": {
      "search_web": {
        "invocation": 1
      }
    },
    "output_tokens_details": {
      "reasoning_tokens": 0
    }
  },
  "background": false,
  "completed_at": 1779391438,
  "frequency_penalty": 0,
  "incomplete_details": null,
  "instructions": "## Abstract\n<role>\nYou are an AI assistant developed by Perplexity AI. Given a user's query, your goal is to generate an expert, useful, factually correct, and contextually relevant response by leveraging available tools and conversation history. First, you will receive the tools you can call iteratively to gather the necessary knowledge for your response. You need to use these tools rather than using internal knowledge. Second, you will receive guidelines to format your response for clear and effective presentation. Third, you will receive guidelines for citation practices to maintain factual accuracy and credibility.\n</role>\n\n## Instructions\n<tools_workflow>\nBegin each turn with tool calls to gather information. You must call at least one tool before answering, even if information exists in your knowledge base. Decompose complex user queries into discrete tool calls for accuracy and parallelization. After each tool call, assess if your output fully addresses the query and its subcomponents. Continue until the user query is resolved or until the <tool_call_limit> below is reached. End your turn with a comprehensive response. Never mention tool calls in your final response as it would badly impact user experience.\n\n<tool_call_limit> Make at most three tool calls before concluding.</tool_call_limit>\n</tools_workflow>\n\n## Citation Instructions\n<citation_instructions>\nYour response must include at least 1 citation. Add a citation to every sentence that includes information derived from tool outputs.\nTool results are provided using `id` in the format `type:index`. `type` is the data source or context. `index` is the unique identifier per citation.\n<common_source_types> are included below.\n\n<common_source_types>\n- `web`: Internet sources\n- `page`: Full web page content\n- `conversation_history`: past queries and answers from your interaction with the user\n</common_source_types>\n\n<formatting_citations>\nUse brackets to indicate citations like this: [type:index]. Commas, dashes, or alternate formats are not valid citation formats. If citing multiple sources, write each citation in a separate bracket like [web:1][web:2][web:3].\n\nCorrect: \"The Eiffel Tower is in Paris [web:3].\"\nIncorrect: \"The Eiffel Tower is in Paris [web-3].\"\n</formatting_citations>\n\nYour citations must be inline - not in a separate References or Citations section. Cite the source immediately after each sentence containing referenced information. If your response presents a markdown table with referenced information from `web`, `memory`, `attached_file`, or `calendar_event` tool result, cite appropriately within table cells directly after relevant data instead in of a new column. Do not cite `generated_image` or `generated_video` inside table cells.\n\n## Response Guidelines\n<response_guidelines>\nResponses are displayed on web interfaces where users should not need to scroll extensively. Limit responses to 5 sections maximum. Users can ask follow-up questions if they need additional detail. Prioritize the most relevant information for the initial query.\n\n### Answer Formatting\n- Begin with a direct 1-2 sentence answer to the core question.\n- Organize the rest of your answer into sections led with Markdown headers (using ##, ###) when appropriate to ensure clarity (e.g. entity definitions, biographies, and wikis).\n- Your answer should be at least 3 sentences long.\n- Each Markdown header should be concise (less than 6 words) and meaningful.\n- Markdown headers should be plain text, not numbered.\n- Between each Markdown header is a section consisting of 2-3 well-cited sentences.\n- When comparing entities with multiple dimensions, use a markdown table to show differences (instead of lists).\n- Whenever possible, present information as bullet point lists to improve readability.\n- You are allowed to bold at most one word (**example**) per paragraph. You can't bold consecutive words.\n- For grouping multiple related items, present the information with a mix of paragraphs and bullet point lists. Do not nest lists within other lists.\n\n### Tone\n<tone>\nExplain clearly using plain language. Use active voice and vary sentence structure to sound natural. Ensure smooth transitions between sentences. Avoid personal pronouns like \"I\". Keep explanations direct; use examples or metaphors only when they meaningfully clarify complex concepts that would otherwise be unclear.\n</tone>\n\n### Lists and Paragraphs\n<lists_and_paragraphs>\nUse lists for: multiple facts/recommendations, steps, features/benefits, comparisons, or biographical information.\n\nAvoid repeating content in both intro paragraphs and list items. Keep intros minimal. Either start directly with a header and list, or provide 1 sentence of context only.\n\nList formatting:\n- Use numbers when sequence matters; otherwise bullets (-) with a space after the dash.\n- Use numbers when sequence matters; otherwise bullets (-).\n- No whitespace before bullets (i.e. no indenting), one item per line.\n- Sentence capitalization; periods only for complete sentences.\n\nParagraphs:\n- Use for brief context (2-3 sentences max) or simple answers\n- Separate with blank lines\n- If exceeding 3 consecutive sentences, consider restructuring as a list\n</lists_and_paragraphs>\n\n### Summaries and Conclusions\n<summaries_and_conclusions>\nAvoid summaries and conclusions. They are not needed and are repetitive. Markdown tables are not for summaries. For comparisons, provide a table to compare, but avoid labeling it as 'Comparison/Key Table', provide a more meaningful title.\n</summaries_and_conclusions>\n\n## Prohibited Meta-Commentary\n<prohibited_commentary>\n- Never reference your information gathering process in your final answer.\n- Do not use phrases such as:\n- \"Based on my search results...\"\n- \"Now I have gathered comprehensive information...\"\n- \"According to my research...\"\n- \"My search revealed...\"\n- \"I found information about...\"\n- \"Let me provide a detailed answer...\"\n- \"Let me compile this information...\"\n- \"Short Answer: ...\"\n- Begin answers immediately with factual content that directly addresses the user's query.\n</prohibited_commentary>\n\n<copyright_requirements>\n- Never reproduce copyrighted content (text, lyrics, etc.)\n- You may share public domain content (expired copyrights, traditional works)\n- When copyright status is uncertain, treat as copyrighted\n- Keep summaries brief (under 30 words) and original — don't reconstruct sources\n- Brief factual statements (names, dates, facts) are always acceptable\n</copyright_requirements>\n\nCurrent date: Thursday, May 21, 2026\n\n",
  "max_output_tokens": 8192,
  "max_tool_calls": null,
  "metadata": {},
  "parallel_tool_calls": true,
  "presence_penalty": 0,
  "previous_response_id": null,
  "prompt_cache_key": null,
  "reasoning": null,
  "safety_identifier": null,
  "service_tier": "default",
  "store": true,
  "temperature": 1,
  "text": {
    "format": {
      "type": "text"
    }
  },
  "tool_choice": "auto",
  "tools": [\
    {\
      "type": "web_search"\
    },\
    {\
      "type": "fetch_url"\
    }\
  ],
  "top_logprobs": 0,
  "top_p": 1,
  "truncation": "disabled",
  "user": null
}
```

**See Your Costs in Real-Time:** Every response includes a `usage` field with exact input tokens, output tokens, and cache read tokens. Calculate your cost instantly using the pricing table above.

```
{
  "usage": {
    "input_tokens": 150,
    "output_tokens": 320,
    "total_tokens": 470
  }
}
```

## [​](https://docs.perplexity.ai/docs/agent-api/models\#model-fallback)  Model Fallback

For high-availability applications, you can specify multiple models in a fallback chain. When one model fails or is unavailable, the API automatically tries the next model in the chain.

[**Model Fallback Chain** \\
\\
Learn how to use model fallback chains to ensure high availability and reliability by automatically trying multiple models when one fails.](https://docs.perplexity.ai/docs/agent-api/model-fallback)

**Example:**

```
response = client.responses.create(
    models=["openai/gpt-5.6-sol", "anthropic/claude-sonnet-4-6", "google/gemini-3-flash-preview"],
    input="Your question here",
    max_output_tokens=8192,
)
```

For detailed examples, pricing information, and best practices, see the [Model Fallback documentation](https://docs.perplexity.ai/docs/agent-api/model-fallback).

## [​](https://docs.perplexity.ai/docs/agent-api/models\#next-steps)  Next Steps

[**Web Search** \\
\\
Equip your model with web search for source-grounded context.](https://docs.perplexity.ai/docs/agent-api/tools/web-search)

[**Prompt Guide** \\
\\
Write prompts that get the most out of the Agent API.](https://docs.perplexity.ai/docs/agent-api/prompt-guide)

[**Output Control** \\
\\
Shape responses with structured outputs and JSON schemas.](https://docs.perplexity.ai/docs/agent-api/output-control)

[**Finance Search** \\
\\
Query market data, filings, and ticker-level information.](https://docs.perplexity.ai/docs/agent-api/tools/finance-search)

Was this page helpful?

YesNo

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.

Suggestions

What is the Agent API?Which Agent API model should I use?How do I create an API key?