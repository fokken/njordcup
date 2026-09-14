# Configuration

[Back to README](../README.md)

| Flag | Environment | Default |
| --- | --- | --- |
| `--base-url` | `REVIEW_BASE_URL` | `https://api.openai.com/v1` |
| `--model` | `REVIEW_MODEL` | Required for model operations |
| `--api-key-env` | `REVIEW_API_KEY_ENV` | `OPENAI_API_KEY` |
| `--output-mode` | `REVIEW_OUTPUT_MODE` | `json_schema` |

`--api-key-env` names the variable containing the credential, not the credential
itself. Local endpoints can run without a key. HTTP redirects are rejected. Only
the configured endpoint is contacted; there is no fallback to a cloud provider.
For servers without strict schemas, choose `--output-mode json_object` or `prompt`.
Every mode still validates model output locally. The adapter uses Chat Completions
with `max_tokens`; verify compatibility with your deployment.

Defaults: `--max-calls 20`, `--max-tokens 6000`, `--batch-chars 24000`,
`--context-chars 24000`, `--context-rounds 3`, `--max-input-chars 80000`.
The request cap includes messages and output-format schema and rejects oversized
requests before contacting the server. It is a character cap, not exact token
counting; set server limits using the model's tokenizer. Large repositories need
more calls or more resumptions, not a repository-sized context window.

## Response caching

`--cache /private/cache` enables response caching by endpoint, model and complete
request. Clear it when changing weights behind an unchanged model name. Cache
entries contain findings/source excerpts. No automatic retries consume extra budget.

## Provider references

- [OpenAI structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Ollama compatibility](https://docs.ollama.com/api/openai-compatibility)
- [vLLM compatibility](https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/)

## Related guides

- [Getting started](getting-started.md)
- [Resource planning and benchmarks](resource-planning.md)
