# Security

## Threat model

An MCP server sits between a language model and a system holding customer data. Three risks
matter most here.

**1. Tool poisoning.** A malicious or compromised MCP server can hide instructions in tool
descriptions or change them after approval. *Mitigations:* this server's tool descriptions only
describe what each tool does and name the LACRM function it calls; there are no instructions to
the model, no remote-loaded descriptions, and no dynamic tool registration beyond the
read-only switch at start-up. Install from source you have reviewed and pin the version.

**2. Prompt injection via CRM data.** Notes, names, task descriptions and background info can be
written by third parties (a web-form lead, an inbound email logged to a contact) and are
returned to the model. *Mitigations:* every result that contains CRM text carries a
`data_notice` marking it as untrusted data; text fields are truncated (2,000 chars), lists are
capped (50 items, 8 levels deep), and control characters are stripped. This reduces exposure but
cannot make injected text harmless: a model may still follow it. Keep a human in the loop for
write actions and prefer read-only mode when summarising untrusted records.

**3. Over-broad write access.** An agent that can write can also be tricked into writing.
*Mitigations:* `LACRM_READ_ONLY` / `--read-only` removes all write tools; there are no delete or
bulk tools; writes are narrow (one note, one task, one contact, one status change); pipeline
status automations are explicitly not triggered; inputs are strictly validated (ids must be
digits, length and range limits, enums, dates, email format) before any request is made; write
tools are annotated as non-read-only so clients can prompt for approval. LACRM API keys can be
scoped to read/create/edit/delete: use the least privilege that fits.

## Secret handling

- The API key is read only from `LACRM_API_KEY`; it is never accepted as a tool argument, written
  to disk, or included in the settings `repr`.
- Error text from the API and transport is passed through redaction that removes the key value
  and any `Authorization` header content. Unexpected exceptions are reduced to their type name.
- The server logs only retry events to stderr, never request bodies or headers. Stdout is
  reserved for the MCP protocol.

## Reliability controls

Per-request timeout (default 20 s); HTTP 429 retried with bounded exponential backoff and the
wait surfaced in the error; 5xx/timeouts retried for reads only so a write is never silently
repeated.

## Known limits

- Searches return whatever the API key can see; scope the key, not just the prompt.
- Results are not scanned for injection phrases; mitigation is bounding and labelling.
- The CRM's own custom fields are passed through (truncated) by `get_contact`.

## Reporting a vulnerability

Email hello@northkeellabs.com with details. Please do not open a public issue for security
reports.
