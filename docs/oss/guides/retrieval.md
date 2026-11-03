---
audience: operations-security
---

# Retrieval add-on

Atlas always has keyword (full-text) search. The **retrieval add-on** adds semantic search on top:
questions in your own words, in another language, or matching a paragraph deep inside a document.
It works by sending node text to an **embedding endpoint** and storing the vectors it returns in
Postgres.

The add-on is optional and off by default. Atlas is complete without it, and a broken add-on never
stops Atlas: it reports itself as degraded or unavailable, and search falls back to keywords.

This page covers the three modes, how to turn each one on, how Atlas authenticates to the endpoint,
the network requirement for a hosted provider, and **exactly which text leaves Atlas**. Every
variable named here is described in the [configuration reference](../CONFIG.md#retrieval-add-on).
For memory, CPU and disk sizing, see [Server requirements](server-requirements.md#retrieval-add-on).

## How it works

- The add-on runs **inside `atlas-api`**. There is no separate retrieval service, and **no model
  ships with Atlas**: you bring the embedding endpoint.
- The endpoint is any **OpenAI-compatible `POST …/embeddings`** API. Atlas names no provider in its
  code (`backend/retrieval/embedder.py`), so a hosted service and a model on your own hardware differ
  only in configuration.
- A background loop in `atlas-api` (the reconciler, `backend/retrieval/reconciler.py`) compares what
  the graph holds with what is indexed, and embeds only what is new or changed. Writes to the graph
  never wait for it: the index can lag by up to one interval.
- Vectors are stored in two tables of derived data, `retrieval_source` and `retrieval_chunk`
  (`retrieval_chunk.embedding` is `vector(ATLAS_EMBED_DIM)`, from `backend/retrieval/setup.py`).
  There is no approximate (ANN) index: the scan is exact. Both tables can be rebuilt from the graph
  at any time, so losing them costs a reindex, not data.
- Search uses the vectors only when a caller asks for `mode=semantic` or `mode=hybrid` on
  `/api/search`, and for `/api/context` (which defaults to `hybrid`). The MCP tools
  `atlas_node_search` and `atlas_context` ask for `hybrid` by default.

## The three modes

| Mode | What it is | Text leaves the Atlas host? |
|---|---|---|
| **Off** (default) | Keyword search only. | No. |
| **Hosted provider** | An OpenAI-compatible embeddings API run by a cloud provider, for example an Azure OpenAI deployment in Azure AI Foundry. | Yes, to the provider, through a private endpoint (see [Network](#network-requirement-for-a-hosted-provider)). |
| **Local model** | An embedding model on hardware you control, behind an OpenAI-compatible API (for example Ollama, vLLM or Text Embeddings Inference), on a separate node over TLS. | It leaves the Atlas host, but stays on your network. |

### Off

Nothing to do: `ATLAS_RETRIEVAL` unset or `off` is the default. With the add-on off, `atlas-api`
reads only the switch at boot (`enabled()` in `backend/retrieval/__init__.py`), mounts no `/api/retrieval/*`
route, creates no table and starts no loop, and `/api/health` has no `retrieval` field. A search
that asks for `mode=semantic` or `hybrid` still answers, with keyword results and a `degraded` note
saying retrieval is off.

The only value that stops `atlas-api` from starting is a typo in the switch itself:
`ATLAS_RETRIEVAL` must be `on` or `off`.

To turn the add-on off again after using it, set `ATLAS_RETRIEVAL=off` and recreate `atlas-api` from
`docker-compose.yml` alone. The two retrieval tables stay in the database, unread. You can drop them
(`DROP TABLE retrieval_chunk; DROP TABLE retrieval_source;`) if you want the space back.

### Turning it on: the common steps

These apply to both the hosted and the local mode. Work in `deploy/oss/`.

1. **Fill in the Retrieval block of `.env`** (copied from `.env.example`):

   ```sh
   ATLAS_RETRIEVAL=on
   ATLAS_EMBED_URL=https://<endpoint>/...      # see the mode sections below
   ATLAS_EMBED_MODEL=<model name>
   ATLAS_EMBED_DIM=<the vector length the model returns>
   # authentication: see "Authentication" below
   ```

   - `ATLAS_EMBED_URL` must be `https://`. Plain `http://` is accepted only to a loopback host, and
     inside the `atlas-api` container loopback is the container itself, so in practice every
     endpoint is HTTPS. A base URL ending in `/v1` gets `/embeddings` appended; a URL whose path
     already ends in `/embeddings` is used as given, query string included
     (`embeddings_url()` in `backend/retrieval/embedder.py`).
   - `ATLAS_EMBED_MODEL` is sent as `model` in every request and stored on every vector. Changing it
     re-embeds everything.
   - `ATLAS_EMBED_DIM` must equal the length of the vectors the endpoint returns; a mismatch is
     reported, never stored. pgvector's limit is 16,000.
   - `ATLAS_EMBED_DOC_PREFIX` and `ATLAS_EMBED_QUERY_PREFIX` are prepended verbatim to every indexed
     text and every search query. Set them only if your model expects them (for example
     `nomic-embed-text` expects `search_document: ` and `search_query: `, trailing space included).
     Changing the document prefix re-embeds everything.

2. **Check the configuration** with the one-shot check under the `retrieval` profile. It reads the
   same variables, refuses what cannot be right (an unknown switch value, plain `http://` off
   loopback, a key in a plain variable), never contacts the endpoint and never opens the key file
   (`deploy/oss/retrieval_check.py`):

   ```sh
   docker compose --profile retrieval run --rm atlas-retrieval
   ```

3. **Start `atlas-api` with the overlay** `retrieval.yml`:

   ```sh
   docker compose -f docker-compose.yml -f retrieval.yml up -d
   ```

   The overlay adds two things and nothing else: `atlas-api` joins the `egress` network, its only
   route out of the host, and receives the key file as the compose secret `embed_key`
   (`./secrets/embed_key`, mounted at `/run/secrets/embed_key`). It also gives `atlas-api` the
   install CA as `/run/secrets/atlas_ca`, for an endpoint whose certificate that CA issued. The base
   install has neither the route nor the secret. Keep using both `-f` files for every later
   `docker compose` command on this install, or `atlas-api` goes back to the base file.

4. **Check that it is working.** `GET /api/health` now carries a `retrieval` field:

   | Value | Meaning |
   |---|---|
   | `on` | Configured, storage ready, the last indexing pass succeeded. |
   | `degraded: <last error>` | Configured, but the last pass could not reach or use the endpoint. The index is stale, not wrong; the loop retries with a growing wait (up to 10 minutes). |
   | `unavailable: <reason>` | A configuration or storage problem (no endpoint, a key in a plain variable, a dimension that does not match the stored vectors, no pgvector). No indexing runs. |

   All three are informational: none of them fails a health check or a request. An owner can see
   more at `GET /api/retrieval/status`: the state, the endpoint's host (never the key or a path's
   contents), the model and dimension, the last pass, and how many sources and chunks are indexed or
   still pending. The first pass runs one interval (`ATLAS_RETRIEVAL_INTERVAL_S`, default 60 s)
   after `atlas-api` starts.

**When the endpoint is down**, `/api/health` reports `degraded: …`, and searches still answer
HTTP 200: `hybrid` returns the keyword results and `semantic` returns none, each with a `degraded`
field that says why. The install smoke checks exactly this in leg `8b-retrieval`
(`.github/workflows/install-smoke.yml`): it indexes against a stub endpoint, stops the stub, and
checks that search through the API and MCP still answers 200.

**Changing the dimension** (a new model with a different vector length) needs the tables
recreated. An owner calls `POST /api/retrieval/reindex` with `{"rebuild": true}`; the next pass
re-embeds everything. Without `rebuild`, the same route re-embeds everything at the current
dimension.

### Hosted provider (Azure AI Foundry)

An Azure OpenAI embedding deployment in Azure AI Foundry, reached through a private endpoint:

```sh
ATLAS_RETRIEVAL=on
ATLAS_EMBED_URL=https://<resource>.openai.azure.com/openai/deployments/<deployment>/embeddings?api-version=2024-10-21
ATLAS_EMBED_MODEL=text-embedding-3-small
ATLAS_EMBED_DIM=1536
# then either a key file:
ATLAS_EMBED_AUTH_HEADER=api-key
ATLAS_EMBED_KEY_FILE=/run/secrets/embed_key
# or Entra ID (no key file; see "Entra ID" below):
# ATLAS_EMBED_AUTH_HEADER=entra
# ATLAS_EMBED_KEY_SOURCE=/dev/null
```

The full deployment URL, `?api-version=` included, is used as given. Azure selects the model by
the deployment in the URL; `ATLAS_EMBED_MODEL` is still sent and is what Atlas records on each
vector, so name the deployed model there. Any other OpenAI-compatible hosted API works the same way
with its own base URL (`https://<host>/v1`) and, usually, `ATLAS_EMBED_AUTH_HEADER=bearer`.

Leave `ATLAS_EMBED_SCOPE` at its default `shared` unless you have decided that private projects may
be sent to this provider (see [What is sent](#exactly-which-text-is-sent)).

### Local model

A model on hardware you control, behind any server that answers OpenAI-compatible
`POST /v1/embeddings`. Run it on a **separate node, reached over TLS**, as
[Server requirements](server-requirements.md#retrieval-add-on) recommends: a local embedding model
needs its own memory and usually a GPU, sized from that model's documentation.

```sh
ATLAS_RETRIEVAL=on
ATLAS_EMBED_URL=https://embed.example.com/v1
ATLAS_EMBED_MODEL=<the model name your server expects>
ATLAS_EMBED_DIM=<its vector length>
ATLAS_EMBED_CA_BUNDLE=/run/secrets/atlas_ca     # only for a certificate from a private CA
# a key file only if the server or a proxy in front of it asks for one:
# ATLAS_EMBED_AUTH_HEADER=bearer
# ATLAS_EMBED_KEY_FILE=/run/secrets/embed_key
```

- Most model servers speak plain HTTP. Put a TLS proxy in front of the model on its node, with a
  certificate from your PKI or from this install's CA. Atlas verifies the certificate against the
  system trust store, or against the PEM bundle in `ATLAS_EMBED_CA_BUNDLE`. There is no setting that
  turns verification off.
- `ATLAS_EMBED_DIM` is the model's vector length (for example 768 for `nomic-embed-text`, 1024 for
  `bge-m3`). Check your model's documentation.
- Because the text stays on your network, you may decide that `ATLAS_EMBED_SCOPE=all` is acceptable
  here. That is your decision; the default stays `shared`.

## Authentication

`ATLAS_EMBED_AUTH_HEADER` chooses how Atlas proves itself to the endpoint (`_headers()` in
`backend/retrieval/embedder.py`):

| Value | Header sent | Credential |
|---|---|---|
| `bearer` (default) | `Authorization: Bearer <key>` | the key file |
| `api-key` | `api-key: <key>` (Azure OpenAI's header) | the key file |
| `entra` | `Authorization: Bearer <Entra ID token>` | the identity the container runs as; **no key file** |

With `bearer` and no `ATLAS_EMBED_KEY_FILE`, no authorization header is sent, for an endpoint that
needs none.

### Key file

- **The key is only ever a file.** Write it to `./secrets/embed_key` (the overlay mounts it as
  `/run/secrets/embed_key`) and set `ATLAS_EMBED_KEY_FILE=/run/secrets/embed_key`. A key given as a
  plain `ATLAS_EMBED_KEY` variable makes the add-on `unavailable`, and the configuration check
  refuses it.
- The file is read on **every** call, never cached. Rotating the key is replacing the file; no
  restart is needed. Surrounding whitespace is stripped; an empty or unreadable file fails the call
  with a message that names the path, never the contents.
- Like the other secrets in `./secrets/`, the file must be readable by the container's user. The
  hardened image runs under an arbitrary UID in group 0, which is why `init-secrets.sh` writes
  secret files `0644` inside the `0700` directory. Do the same:
  `chmod 0644 secrets/embed_key`.
- The key appears in one request header and nowhere else: not in a log line, an error message or
  `/api/retrieval/status`.

### Entra ID: managed identity or workload identity

For an Azure OpenAI / Azure AI Foundry resource that accepts only Microsoft Entra ID tokens (key
authentication disabled), set `ATLAS_EMBED_AUTH_HEADER=entra` and leave `ATLAS_EMBED_KEY_FILE`
unset; setting both makes the add-on `unavailable`. Grant the identity a data-plane role on the
resource that allows inference, such as *Cognitive Services OpenAI User*.

Atlas gets the token from the identity the process already runs as (`backend/retrieval/entra.py`),
trying the same sources, in the same order, as Azure's `DefaultAzureCredential`:

1. **Workload identity** (AKS and other federated workloads): when `AZURE_FEDERATED_TOKEN_FILE`,
   `AZURE_CLIENT_ID` and `AZURE_TENANT_ID` are set, the federated token file is exchanged at
   `AZURE_AUTHORITY_HOST` (default `https://login.microsoftonline.com/`). The file is read again on
   every exchange, since the platform rotates it.
2. **Managed identity on App Service or Container Apps**: when `IDENTITY_ENDPOINT` and
   `IDENTITY_HEADER` are set (the platform sets them).
3. **Managed identity on a VM or VM scale set**: the instance metadata service at
   `169.254.169.254`. With more than one user-assigned identity, set `AZURE_CLIENT_ID` to choose
   one.

The token scope is `https://cognitiveservices.azure.com/.default`; set `ATLAS_EMBED_ENTRA_SCOPE`
for a sovereign cloud. The token is cached in memory and fetched again five minutes before it
expires. It is never logged, never written to disk and never put in an error message.

On the compose install:

- **No key file.** Set `ATLAS_EMBED_KEY_SOURCE=/dev/null` in `.env`. `retrieval.yml` makes the
  `embed_key` secret from that host file instead of `./secrets/embed_key`, so the secret is an empty
  mount that nothing reads and `./secrets/embed_key` does not have to exist. Leave
  `ATLAS_EMBED_KEY_FILE` unset.
- **Managed identity** needs nothing more: `atlas-api` reaches the identity endpoint through the
  `egress` network that `retrieval.yml` gives it.
- **Workload identity** needs `AZURE_TENANT_ID` and `AZURE_CLIENT_ID` in `.env` (they reach
  `atlas-api` through `env_file`) and the federated token file inside the container. The shipped
  overlay `retrieval-workload-identity.yml` mounts the **directory** that holds the token,
  read-only, at `/var/run/secrets/azure/tokens`, and sets `AZURE_FEDERATED_TOKEN_FILE` to the file
  in it. It mounts the directory rather than the file because a rotation that replaces the file
  would leave a single-file mount showing the old token. Name the host directory in `.env`:

  ```sh
  ATLAS_EMBED_AUTH_HEADER=entra
  ATLAS_EMBED_KEY_SOURCE=/dev/null
  AZURE_TENANT_ID=<tenant id>
  AZURE_CLIENT_ID=<the federated identity's client id>
  AZURE_FEDERATED_TOKEN_DIR=/path/on/the/host/to/the/token/directory
  # AZURE_FEDERATED_TOKEN_NAME=azure-identity-token   # if the file has another name
  ```

  and start with all three files:

  ```sh
  docker compose -f docker-compose.yml -f retrieval.yml -f retrieval-workload-identity.yml up -d
  ```

  The container's user is not yours, so the directory must be readable (`0755`) and the file too
  (`0644`).

The install smoke's leg 8c runs this setup with no `./secrets/embed_key` at all: the token exchange
with the mounted federated token, then the index filling with Entra tokens.

## Network requirement for a hosted provider

**Reach a hosted provider through a private endpoint, not over the public internet.** For Azure AI
Foundry / Azure OpenAI that means:

- a **private endpoint** (Azure Private Link) for the resource, in a virtual network the Atlas host
  can route to (the same VNet, a peered one, or over VPN or ExpressRoute from your site);
- **public network access disabled** on the resource, so the private endpoint is the only way in;
- **private DNS** for the resource's host name (the `privatelink` zone linked to the Atlas host's
  network), so the normal host name in `ATLAS_EMBED_URL` resolves to the private address and the
  TLS certificate still matches.

Atlas cannot check this for you. It enforces HTTPS with certificate verification, and the overlay
gives `atlas-api` exactly one route out (the `egress` network), but it cannot tell a private address
from a public one. Before turning the add-on on, confirm from the Atlas host that the endpoint's
host name resolves to a private address.

Allow the Atlas host outbound HTTPS to that endpoint only. With `entra`, also allow the token source
the identity uses: the instance metadata service (`169.254.169.254`, on the VM itself), the platform's
identity endpoint, or `login.microsoftonline.com` for workload identity.

A local model on a separate node needs the same thing on a smaller scale: a route from the Atlas
host to that node, and TLS on it.

## Exactly which text is sent

This section is taken from the code, not from other documentation. File and function names are
given so you can check it.

### The request

Every call is one `POST` to the embeddings URL with this JSON body and nothing else
(`embed()` in `backend/retrieval/embedder.py`):

```json
{"model": "<ATLAS_EMBED_MODEL>", "input": ["<prefix><text 1>", "<prefix><text 2>", "…"]}
```

plus the authentication header above. No node id, user name, account or token of the person
searching is part of the request.

### What is sent for indexing

The reconciler (`collect_sources()` in `backend/retrieval/reconciler.py`) indexes two kinds of
source, and only for nodes whose type is in the `object` or `bridge` family (`WORLD_FAMILIES`): the
world nodes, not work items.

**1. The node card**, one per world node (`node_card()` in `backend/retrieval/chunking.py`):

```text
<node name> [<node type>]

<summary>

<abstract>

<detail>
```

Empty fields are left out. A card is one text when it is at most about 1,500 tokens
(`CARD_MAX_TOKENS`). A longer card is split between paragraphs into texts of up to about 1,500
tokens, each starting again with the `<name> [<type>]` line, with no overlap.

**2. Each served document** of a world node (`doc_chunks()` in `backend/retrieval/chunking.py`). A
document is *served* when its path is listed in the node's `docs[]` and not marked `external`
(`models_graph.NODE_DOC_SERVED_SQL`, the same rule that decides whether `GET /api/node/{id}/doc`
returns it). The document is split:

- on markdown headings, then between paragraphs, into texts of roughly 300 to 600 tokens
  (`DOC_MIN_TOKENS`, `DOC_MAX_TOKENS`). Sections shorter than 300 tokens are merged with the next
  section under the same top-level heading;
- the last paragraph of one text is repeated at the start of the next when it is shorter than
  300 tokens, so a passage is not cut off from its context;
- each text starts with a line `<node name> › <document title> › <heading path>`. The title is the
  `title` of the `docs[]` entry, or the path when there is none;
- every fenced block (code, mermaid and so on) is **replaced** by the line
  `[<label> block omitted]`. Its contents are never sent.

Tokens are estimated as characters divided by 4 (`tokens()`); the bounds are soft targets. A single
paragraph longer than the bound becomes one text on its own and is never cut mid-sentence, so a text
can be longer than the bound.

`ATLAS_EMBED_DOC_PREFIX` is prepended to every one of these texts.

### What is sent for a search

When someone searches with `mode=semantic` or `mode=hybrid` (including `/api/context`, and the MCP
tools `atlas_node_search` and `atlas_context`, which default to `hybrid`), **the search text itself**
is sent as a single input, with `ATLAS_EMBED_QUERY_PREFIX` in front (`vector_list()` in
`backend/retrieval/query.py`). This happens whatever `ATLAS_EMBED_SCOPE` says: the scope controls
the indexed text, not what people type into search. A search with `mode=fulltext`, or with no
`mode`, sends nothing.

### When

- The loop's first pass runs one `ATLAS_RETRIEVAL_INTERVAL_S` (default 60 s) after `atlas-api`
  starts, then one pass per interval (`backend/retrieval/service.py`). After a failed pass the wait
  doubles, up to 10 minutes, and resets after a success.
- A pass sends only the sources whose content hash changed (`source_hash()`): a new or edited node
  card, a new or edited served document. The hash covers the chunk texts, the model, the document
  prefix and the chunker version, so changing the model or the document prefix sends everything
  again. So does an owner's `POST /api/retrieval/reindex`.
- Texts are sent at most `ATLAS_EMBED_BATCH` (default 32) per request. Whole sources are grouped
  into a request; a source with more texts than a batch is sent over several requests.
- Writing to the graph never sends anything by itself. Booting, `/api/health` and
  `/api/retrieval/status` never call the endpoint.

### `ATLAS_EMBED_SCOPE`: private projects

- **`shared`** (the default): the text of a node is sent only if a person holding **no grant at
  all** could see it, that is, the node is not marked private and is not under a node that is
  (`in_scope()` in `backend/retrieval/reconciler.py`, using the same walk as Atlas's own visibility
  check). Private projects are then found by keyword search only. An unknown value is treated as
  `shared`, so a typo never widens what is sent.
- **`all`**: every world node and served document is sent, private ones included.

Search results are filtered by the caller's own visibility either way. What is indexed and who may
see a result are two separate controls.

When a node becomes private, or a document stops being served, the next pass deletes its vectors
from Atlas. Text that was already sent to the provider cannot be called back from Atlas; that is
the provider's retention policy.

### What is never sent

- **Work items** (tasks, stories, epics and every other `workitem`-family node), and so their
  acceptance criteria and results.
- A node's **`body`** field, its **attributes** (other than the `docs[]` titles used in the chunk
  header line), its **id**, its **edges** and its **intents**.
- **Stored documents that are not served**: no `docs[]` entry, or marked `external`.
- **The contents of fenced code and diagram blocks** in documents.
- Under `ATLAS_EMBED_SCOPE=shared`, **anything private** or under a private node.
- Accounts, sessions, tokens, groups, grants, audit events and the graph's event history: the
  reconciler reads only `node`, `node_type`, `node_doc`, the private marks and the edges the
  visibility walk follows.
- The embedding key and any Entra token, other than as the request's authentication header.

### What Atlas stores

Each chunk's text and its vector are stored in `retrieval_chunk`, in your own Postgres, next to the
node and document they came from (`_write_source()` in `backend/retrieval/reconciler.py`). The
vector costs about 4 bytes per dimension per chunk; see
[Server requirements](server-requirements.md#disk) for the disk estimate.

## Tuning

| Variable | Default | Effect |
|---|---|---|
| `ATLAS_RETRIEVAL_INTERVAL_S` | `60` | Seconds between indexing passes. Lower means fresher results and more frequent calls. |
| `ATLAS_EMBED_BATCH` | `32` | Texts per embeddings request. Lower it if the endpoint limits the request size. |
| `ATLAS_RETRIEVAL_TOP_K` | `30` | How many nodes the vector search contributes to a `semantic` or `hybrid` search (1 to 200). |

`hybrid` merges the keyword ranking and the vector ranking by Reciprocal Rank Fusion (k = 60), at
node level (`fuse()` in `backend/retrieval/query.py`). See the
[configuration reference](../CONFIG.md#retrieval-add-on) for every retrieval variable.
