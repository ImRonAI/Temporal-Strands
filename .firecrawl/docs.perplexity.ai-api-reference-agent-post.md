> ## Documentation Index
>
> Fetch the complete documentation index at: [/llms.txt](https://docs.perplexity.ai/llms.txt)
>
> Use this file to discover all available pages before exploring further.

[Skip to main content](https://docs.perplexity.ai/api-reference/agent-post#content-area)

Create Agent Response

cURL

```
curl --request POST \
  --url https://api.perplexity.ai/v1/agent \
  --header 'Authorization: Bearer <token>' \
  --header 'Content-Type: application/json' \
  --data '
{
  "input": "<string>",
  "instructions": "<string>"
}
'
```

```
import requests

url = "https://api.perplexity.ai/v1/agent"

payload = {
    "input": "<string>",
    "instructions": "<string>"
}
headers = {
    "Authorization": "Bearer <token>",
    "Content-Type": "application/json"
}

response = requests.post(url, json=payload, headers=headers)

print(response.text)
```

```
const options = {
  method: 'POST',
  headers: {Authorization: 'Bearer <token>', 'Content-Type': 'application/json'},
  body: JSON.stringify({input: '<string>', instructions: '<string>'})
};

fetch('https://api.perplexity.ai/v1/agent', options)
  .then(res => res.json())
  .then(res => console.log(res))
  .catch(err => console.error(err));
```

```
<?php

$curl = curl_init();

curl_setopt_array($curl, [\
  CURLOPT_URL => "https://api.perplexity.ai/v1/agent",\
  CURLOPT_RETURNTRANSFER => true,\
  CURLOPT_ENCODING => "",\
  CURLOPT_MAXREDIRS => 10,\
  CURLOPT_TIMEOUT => 30,\
  CURLOPT_HTTP_VERSION => CURL_HTTP_VERSION_1_1,\
  CURLOPT_CUSTOMREQUEST => "POST",\
  CURLOPT_POSTFIELDS => json_encode([\
    'input' => '<string>',\
    'instructions' => '<string>'\
  ]),\
  CURLOPT_HTTPHEADER => [\
    "Authorization: Bearer <token>",\
    "Content-Type: application/json"\
  ],\
]);

$response = curl_exec($curl);
$err = curl_error($curl);

curl_close($curl);

if ($err) {
  echo "cURL Error #:" . $err;
} else {
  echo $response;
}
```

```
package main

import (
	"fmt"
	"strings"
	"net/http"
	"io"
)

func main() {

	url := "https://api.perplexity.ai/v1/agent"

	payload := strings.NewReader("{\n  \"input\": \"<string>\",\n  \"instructions\": \"<string>\"\n}")

	req, _ := http.NewRequest("POST", url, payload)

	req.Header.Add("Authorization", "Bearer <token>")
	req.Header.Add("Content-Type", "application/json")

	res, _ := http.DefaultClient.Do(req)

	defer res.Body.Close()
	body, _ := io.ReadAll(res.Body)

	fmt.Println(string(body))

}
```

```
HttpResponse<String> response = Unirest.post("https://api.perplexity.ai/v1/agent")
  .header("Authorization", "Bearer <token>")
  .header("Content-Type", "application/json")
  .body("{\n  \"input\": \"<string>\",\n  \"instructions\": \"<string>\"\n}")
  .asString();
```

```
require 'uri'
require 'net/http'

url = URI("https://api.perplexity.ai/v1/agent")

http = Net::HTTP.new(url.host, url.port)
http.use_ssl = true

request = Net::HTTP::Post.new(url)
request["Authorization"] = 'Bearer <token>'
request["Content-Type"] = 'application/json'
request.body = "{\n  \"input\": \"<string>\",\n  \"instructions\": \"<string>\"\n}"

response = http.request(request)
puts response.read_body
```

200

400

```
{
  "created_at": 123,
  "id": "<string>",
  "model": "<string>",
  "object": "response",
  "output": [\
    {\
      "content": [\
        {\
          "text": "<string>",\
          "type": "output_text",\
          "annotations": [\
            {\
              "end_index": 123,\
              "start_index": 123,\
              "title": "<string>",\
              "type": "<string>",\
              "url": "<string>"\
            }\
          ]\
        }\
      ],\
      "id": "<string>",\
      "role": "assistant",\
      "type": "message"\
    }\
  ],
  "error": {
    "message": "<string>",
    "code": "<string>",
    "type": "<string>"
  },
  "usage": {
    "input_tokens": 123,
    "output_tokens": 123,
    "total_tokens": 123,
    "cost": {
      "currency": "USD",
      "input_cost": 123,
      "output_cost": 123,
      "total_cost": 123,
      "cache_creation_cost": 123,
      "cache_read_cost": 123,
      "tool_calls_cost": 123
    },
    "input_tokens_details": {
      "cache_creation_input_tokens": 123,
      "cache_read_input_tokens": 123
    },
    "tool_calls_details": {}
  }
}
```

```
{
  "error": {
    "message": "<string>",
    "code": "<string>",
    "type": "<string>"
  }
}
```

POST

/

v1

/

agent

Try it

Create Agent Response

cURL

```
curl --request POST \
  --url https://api.perplexity.ai/v1/agent \
  --header 'Authorization: Bearer <token>' \
  --header 'Content-Type: application/json' \
  --data '
{
  "input": "<string>",
  "instructions": "<string>"
}
'
```

```
import requests

url = "https://api.perplexity.ai/v1/agent"

payload = {
    "input": "<string>",
    "instructions": "<string>"
}
headers = {
    "Authorization": "Bearer <token>",
    "Content-Type": "application/json"
}

response = requests.post(url, json=payload, headers=headers)

print(response.text)
```

```
const options = {
  method: 'POST',
  headers: {Authorization: 'Bearer <token>', 'Content-Type': 'application/json'},
  body: JSON.stringify({input: '<string>', instructions: '<string>'})
};

fetch('https://api.perplexity.ai/v1/agent', options)
  .then(res => res.json())
  .then(res => console.log(res))
  .catch(err => console.error(err));
```

```
<?php

$curl = curl_init();

curl_setopt_array($curl, [\
  CURLOPT_URL => "https://api.perplexity.ai/v1/agent",\
  CURLOPT_RETURNTRANSFER => true,\
  CURLOPT_ENCODING => "",\
  CURLOPT_MAXREDIRS => 10,\
  CURLOPT_TIMEOUT => 30,\
  CURLOPT_HTTP_VERSION => CURL_HTTP_VERSION_1_1,\
  CURLOPT_CUSTOMREQUEST => "POST",\
  CURLOPT_POSTFIELDS => json_encode([\
    'input' => '<string>',\
    'instructions' => '<string>'\
  ]),\
  CURLOPT_HTTPHEADER => [\
    "Authorization: Bearer <token>",\
    "Content-Type: application/json"\
  ],\
]);

$response = curl_exec($curl);
$err = curl_error($curl);

curl_close($curl);

if ($err) {
  echo "cURL Error #:" . $err;
} else {
  echo $response;
}
```

```
package main

import (
	"fmt"
	"strings"
	"net/http"
	"io"
)

func main() {

	url := "https://api.perplexity.ai/v1/agent"

	payload := strings.NewReader("{\n  \"input\": \"<string>\",\n  \"instructions\": \"<string>\"\n}")

	req, _ := http.NewRequest("POST", url, payload)

	req.Header.Add("Authorization", "Bearer <token>")
	req.Header.Add("Content-Type", "application/json")

	res, _ := http.DefaultClient.Do(req)

	defer res.Body.Close()
	body, _ := io.ReadAll(res.Body)

	fmt.Println(string(body))

}
```

```
HttpResponse<String> response = Unirest.post("https://api.perplexity.ai/v1/agent")
  .header("Authorization", "Bearer <token>")
  .header("Content-Type", "application/json")
  .body("{\n  \"input\": \"<string>\",\n  \"instructions\": \"<string>\"\n}")
  .asString();
```

```
require 'uri'
require 'net/http'

url = URI("https://api.perplexity.ai/v1/agent")

http = Net::HTTP.new(url.host, url.port)
http.use_ssl = true

request = Net::HTTP::Post.new(url)
request["Authorization"] = 'Bearer <token>'
request["Content-Type"] = 'application/json'
request.body = "{\n  \"input\": \"<string>\",\n  \"instructions\": \"<string>\"\n}"

response = http.request(request)
puts response.read_body
```

200

400

```
{
  "created_at": 123,
  "id": "<string>",
  "model": "<string>",
  "object": "response",
  "output": [\
    {\
      "content": [\
        {\
          "text": "<string>",\
          "type": "output_text",\
          "annotations": [\
            {\
              "end_index": 123,\
              "start_index": 123,\
              "title": "<string>",\
              "type": "<string>",\
              "url": "<string>"\
            }\
          ]\
        }\
      ],\
      "id": "<string>",\
      "role": "assistant",\
      "type": "message"\
    }\
  ],
  "error": {
    "message": "<string>",
    "code": "<string>",
    "type": "<string>"
  },
  "usage": {
    "input_tokens": 123,
    "output_tokens": 123,
    "total_tokens": 123,
    "cost": {
      "currency": "USD",
      "input_cost": 123,
      "output_cost": 123,
      "total_cost": 123,
      "cache_creation_cost": 123,
      "cache_read_cost": 123,
      "tool_calls_cost": 123
    },
    "input_tokens_details": {
      "cache_creation_input_tokens": 123,
      "cache_read_input_tokens": 123
    },
    "tool_calls_details": {}
  }
}
```

```
{
  "error": {
    "message": "<string>",
    "code": "<string>",
    "type": "<string>"
  }
}
```

#### Authorizations

[​](https://docs.perplexity.ai/api-reference/agent-post#authorization-authorization)

Authorization

string

header

required

Bearer authentication header of the form `Bearer <token>`, where `<token>` is your auth token.

#### Body

application/json

[​](https://docs.perplexity.ai/api-reference/agent-post#body-input-one-of-0)

input

string(InputMessage · object \| FunctionCallOutputInput · object \| FunctionCallInput · object)\[\]string(InputMessage · object \| FunctionCallOutputInput · object \| FunctionCallInput · object)\[\]

required

Input content - either a string or array of input items

[​](https://docs.perplexity.ai/api-reference/agent-post#body-background)

background

boolean

Run the response asynchronously. With `stream: false`, the request returns immediately with `status: "queued"`; poll `GET /v1/responses/{id}` until the response reaches a terminal status. Background runs are durable, so you can also stream them and reconnect after a drop.

[​](https://docs.perplexity.ai/api-reference/agent-post#body-instructions)

instructions

string

System instructions for the model

[​](https://docs.perplexity.ai/api-reference/agent-post#body-language-preference)

language\_preference

string

ISO 639-1 language code for response language

[​](https://docs.perplexity.ai/api-reference/agent-post#body-max-output-tokens)

max\_output\_tokens

integer<int32>

Maximum tokens to generate. This is a shared optional Agent API request parameter, but it is required when using anthropic/\* models. If omitted for an Anthropic model, the API returns HTTP 400 with: validation failed: max\_output\_tokens is required when using Anthropic models.

Required range: `x >= 1`

[​](https://docs.perplexity.ai/api-reference/agent-post#body-max-steps)

max\_steps

integer<int32>

Maximum number of research loop steps.
If provided, overrides the preset's max\_steps value.
Must be >= 1 if specified. Maximum allowed is 100.

Required range: `1 <= x <= 100`

[​](https://docs.perplexity.ai/api-reference/agent-post#body-model)

model

string

Model ID in provider/model format (e.g., "openai/gpt-5", "anthropic/claude-sonnet-4-6").
If models is also provided, models takes precedence.
Required if neither models nor preset is provided.

[​](https://docs.perplexity.ai/api-reference/agent-post#body-models)

models

string\[\]

Model fallback chain. Each model is in provider/model format.
Models are tried in order until one succeeds.
Max 5 models allowed. If set, takes precedence over single model field.
The response.model will reflect the model that actually succeeded.

Required array length: `1 - 5` elements

[​](https://docs.perplexity.ai/api-reference/agent-post#body-preset)

preset

string

Preset configuration name (e.g., "fast", "low", "medium", "high", "xhigh").
Pre-configured model with system prompt and search parameters.
Required if model is not provided.

[​](https://docs.perplexity.ai/api-reference/agent-post#body-previous-response-id)

previous\_response\_id

string

OpenAI-compatible previous response id for multi-turn response chains. When set, the new response continues from the completed prior response's saved state. The prior response must belong to the same account and have completed.

[​](https://docs.perplexity.ai/api-reference/agent-post#body-reasoning)

reasoning

ReasoningConfig · object

Showchild attributes

[​](https://docs.perplexity.ai/api-reference/agent-post#body-response-format)

response\_format

ResponseFormat · object

Specifies the desired output format for the model response

Showchild attributes

[​](https://docs.perplexity.ai/api-reference/agent-post#body-store)

store

boolean

OpenAI-compatible storage toggle. When false, the response is hidden from later retrieve calls, and the echoed response reports `store: false`. It can still be used as a `previous_response_id` continuation source.

[​](https://docs.perplexity.ai/api-reference/agent-post#body-stream)

stream

boolean

If true, returns SSE stream instead of JSON

[​](https://docs.perplexity.ai/api-reference/agent-post#body-tools)

tools

(WebSearchTool · object \| FinanceSearchTool · object \| PeopleSearchTool · object \| FetchUrlTool · object \| FunctionTool · object \| SandboxTool · object \| McpTool · object)\[\]

Tools available to the model

Web search tool configuration for the Responses API

- WebSearchTool

- FinanceSearchTool

- PeopleSearchTool

- FetchUrlTool

- FunctionTool

- SandboxTool

- McpTool


Showchild attributes

[​](https://docs.perplexity.ai/api-reference/agent-post#body-skills)

skills

(BuiltinSkill · object \| InlineSkill · object)\[\]

Built-in and request-scoped inline skills available to the model. Skill metadata is disclosed to the model up front; full instructions are loaded on demand through the load\_skill tool. Selecting any skill enables the sandbox tool for the request. Requests with skills run on the durable backend and skills are not echoed back on Response objects.

Maximum array length: `16`

- BuiltinSkill

- InlineSkill


Showchild attributes

[​](https://docs.perplexity.ai/api-reference/agent-post#body-temperature)

temperature

number<double>

OpenAI-compatible sampling temperature forwarded to generation.

Required range: `0 <= x <= 2`

[​](https://docs.perplexity.ai/api-reference/agent-post#body-top-p)

top\_p

number<double>

OpenAI-compatible nucleus sampling parameter forwarded to generation.

Required range: `0 <= x <= 1`

#### Response

200

application/json

Successful response. Content type depends on `stream` parameter:

- `stream: false` (default): `application/json` with Response
- `stream: true`: `text/event-stream` with SSE events

Non-streaming response returned when stream is false

[​](https://docs.perplexity.ai/api-reference/agent-post#response-created-at)

created\_at

integer<int64>

required

Unix timestamp when the response was created

[​](https://docs.perplexity.ai/api-reference/agent-post#response-id)

id

string

required

Unique identifier for the response

[​](https://docs.perplexity.ai/api-reference/agent-post#response-model)

model

string

required

Model used for generation

[​](https://docs.perplexity.ai/api-reference/agent-post#response-object)

object

enum<string>

required

Object type identifier

Available options:

`response`

[​](https://docs.perplexity.ai/api-reference/agent-post#response-output)

output

(MessageOutputItem · object \| SearchResultsOutputItem · object \| FetchUrlResultsOutputItem · object \| FinanceResultsOutputItem · object \| PeopleSearchResultsOutputItem · object \| FunctionCallOutputItem · object \| SandboxResultsOutputItem · object \| McpListToolsOutputItem · object \| McpCallOutputItem · object \| ToolSearchOutputItem · object)\[\]

required

Array of output items (messages, search results, tool calls)

- MessageOutputItem

- SearchResultsOutputItem

- FetchUrlResultsOutputItem

- FinanceResultsOutputItem

- PeopleSearchResultsOutputItem

- FunctionCallOutputItem

- SandboxResultsOutputItem

- McpListToolsOutputItem

- McpCallOutputItem

- ToolSearchOutputItem


Showchild attributes

[​](https://docs.perplexity.ai/api-reference/agent-post#response-status)

status

enum<string>

required

Status of the response

Available options:

`completed`,

`failed`,

`incomplete`,

`in_progress`,

`queued`,

`cancelled`

[​](https://docs.perplexity.ai/api-reference/agent-post#response-error)

error

ErrorInfo · object

Error details if the response failed

Showchild attributes

[​](https://docs.perplexity.ai/api-reference/agent-post#response-usage)

usage

ResponsesUsage · object

Token usage and cost information

Showchild attributes

Was this page helpful?

YesNo

Ctrl+I

Assistant

Responses are generated using AI and may contain mistakes.

Suggestions

What is the Agent API?Which Agent API model should I use?How do I create an API key?