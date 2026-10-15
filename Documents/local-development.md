# Local Development

## Setup

You need Python 3.11+. Python 3.12 matches Azure.

```bash
make setup                 # python3 -m venv .venv && pip install -r requirements-dev.txt
make seed                  # 40 synthetic calls -> .localdata/
make run                   # http://localhost:8000
```

Pick a demo user on the sign-in page. The Admin persona sees everything; an Agent persona shows the agent-only view.

## How local mode works

| Azure | Local replacement |
|---|---|
| Blob Storage | Folders under `.localdata/storage/<container>/` |
| Cosmos DB | SQLite at `.localdata/callstore.sqlite3` |
| Speech | `MockSpeechClient` returns the call's sample script (from `Data/sample_calls/scenarios.yaml`) as a timed transcript |
| Language | `MockLanguageClient`: regex PII detection and lexicon sentiment |
| OpenAI | `MockInsightsClient`: keyword heuristics that produce the same JSON shape as the LLM |
| Event Grid → Queue → Functions | Uploads in the web app run the pipeline in a background thread; `make worker` watches the recordings folder |
| Entra ID | Demo users (`AUTH_MODE=dev`) |

The sample calls are synthetic, with fictitious names and numbers. Lower-skill agents randomly skip script lines, such as the greeting, the recording disclosure or verification, and sometimes ask for an OTP or CVV. That gives QA scores and compliance failures some variety.

## Useful commands

```bash
python -m callcenter status                     # counts per status, failed calls
python -m callcenter process my_call.wav --meta my_call.json
python -m callcenter retry CALL-20261002-0004
python -m callcenter reset                      # wipe .localdata
make test                                       # pytest (core) + Django tests (web app)
make package                                    # build/webapp.zip, build/functions.zip
```

Dropping a file into `.localdata/storage/recordings/` while `make worker` is running also processes it.

## Using real Azure services from your machine

Swap services in one at a time with `.env` (copy `.env.example`). Mocks and real services can be mixed:

```bash
# real speech + language + OpenAI, local storage and store
SPEECH_BACKEND=azure
SPEECH_ENDPOINT=https://<speech>.cognitiveservices.azure.com
LANGUAGE_BACKEND=azure
LANGUAGE_ENDPOINT=https://<language>.cognitiveservices.azure.com
LLM_BACKEND=azure
AZURE_OPENAI_ENDPOINT=https://<aoai>.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=gpt-5-mini
```

You can authenticate in either of two ways:

- **Keys:** set `SPEECH_KEY`, `LANGUAGE_KEY` and `AZURE_OPENAI_KEY`.
- **`az login`:** omit the keys. Your account needs *Cognitive Services User* on Speech and Language, and *Cognitive Services OpenAI User* on OpenAI. `deploy.sh` grants these to the deploying user.

With real Speech, uploaded audio is actually transcribed. With mock Speech, any uploaded file is replaced by a sample conversation.

## Running the Functions host locally (optional)

```bash
npm i -g azure-functions-core-tools@4
Deployment/scripts/package.sh
cd build/functions && cp ../../Code/functions/local.settings.sample.json local.settings.json
docker run -d -p 10000-10002:10000-10002 mcr.microsoft.com/azure-storage/azurite:3.33.0
func start
```

The sample settings use Azurite for blobs and queues. Put messages on `recordings-events` in the Event Grid shape, `{"subject": "/blobServices/default/containers/recordings/blobs/<file>"}`, to trigger the pipeline.

## Docker

```bash
make docker-up      # builds the image, starts web + Azurite, seeds 40 calls
open http://localhost:8000
make docker-down
```

The container uses the real Azure Storage SDK against Azurite, which checks the blob code path end to end.

## Code map

| Module | Responsibility |
|---|---|
| `callcenter/config.py` | Settings from environment variables |
| `callcenter/factory.py` | Picks the mock or Azure implementation of each dependency |
| `callcenter/pipeline.py` | The two stages, status tracking, retries and the telephony contract |
| `callcenter/speech.py`, `language.py`, `llm.py` | AI service clients (mock and Azure) |
| `callcenter/rules.py` | Loads and validates the rubric, checks and reasons, including runtime overrides |
| `callcenter/storage.py`, `store.py`, `parquet.py`, `events.py` | Outputs |
| `callcenter/analytics.py` | Aggregations for the dashboards |
| `webapp/accounts` | Sign-in (Entra ID / dev), roles and permissions |
| `webapp/portal` | Views, CRM API, Power BI embedding, templates and static files |
