# Runbook — Esecuzione e promozione di un modello fine-tuned

Procedura per lanciare la pipeline di fine-tuning per-tenant, promuovere il modello risultante e gestire i fallimenti (anonimizzazione / spaCy / OpenAI).

## Contesto

La catena è **`collect → quality → export → train → evaluate → deploy`**, cablata come job ARQ concatenati e avviata da `fine_tune_run` (`backend/workers/fine_tuning/run.py`). Tutti i job girano nel processo worker unico (`workers.settings.WorkerSettings`). Il modello base è `gpt-4.1-mini` fine-tunato sui log conversazione del tenant.

Requisiti operativi:
- `OPENAI_API_KEY` valida con accesso al fine-tuning.
- Modello spaCy `it_core_news_lg` installato nel worker (lo scarica `infra/docker/worker.Dockerfile`) — necessario per la NER **presidio**, obbligatoria in produzione.
- Bucket Storage `ft-training-data` (creato dalle migrazioni 0003).

## 1. Lanciare un run

1. Dall'**admin panel** (web-admin): la UI di fine-tuning lancia il run via il router `/fine-tuning` (`POST`), passando il `tenant_id` (ed eventuale `target_merchant_id` per un rollout A/B per-merchant).
2. In alternativa, enqueue manuale del job `fine_tune_run` con `{tenant_id, target_merchant_id?}`.
3. Segui i log del worker: ogni stadio logga via structlog con `job=fine_tune_*`.

## 2. Cosa fa ogni stadio

- **collect** — raccoglie le conversazioni del tenant (filtro applicativo `WHERE tenant_id`).
- **quality** — filtra le conversazioni di bassa qualità (`workers/fine_tuning/quality.py`).
- **export** — **anonimizza** (doppio strato: regex + presidio NER) e scrive il dataset su Storage con split train/held-out.
- **train** — invia il job di fine-tuning a OpenAI sul base model.
- **evaluate** — valuta il modello sul set held-out con gate pass-margin vs baseline.
- **deploy** — se l'eval passa, registra il `FTModel` e crea l'esperimento A/B (rollout, non flag-flip tenant-wide), oppure flagga il modello come deployabile.

## 3. Promozione del modello

- Il deploy automatico avviene **solo** se l'evaluator ritorna `pass=True`.
- Se `pass=False` ma vuoi promuovere comunque (es. eval "non valutabile" per dataset troppo piccolo, vedi gap #18), la promozione è **manuale** dall'admin panel: verifica prima i contatori dell'eval nei log.
- Il routing in conversazione passa per `FtModelResolver` (variant-aware): una volta esistente l'esperimento `running` con arm `ft`, i turni assegnati a quell'arm usano il modello fine-tuned.

## 4. Gestione fallimenti

### Anonimizzazione / presidio / spaCy
- **In produzione l'anonimizzazione presidio è obbligatoria**: nessun dataset deve raggiungere OpenAI senza passare la NER (vincolo contrattuale Art. 5.2). Vedi `libs/ai_core/src/ai_core/ft/presidio.py` (`build_presidio_transform`).
- Se il modello spaCy `it_core_news_lg` non è caricabile, `build_presidio_transform` logga l'evento di indisponibilità. **NON** procedere con un export degradato regex-only in produzione: lo stadio di export deve fallire/bloccare il run. Fix: ridistribuisci il worker assicurando che il Dockerfile abbia scaricato il modello (`python -m spacy download it_core_news_lg`), poi rilancia il run.
- In ambiente non-prod, la degradazione regex-only è ammessa solo per sviluppo/test.

### OpenAI fine-tuning
- Errori 4xx/rate-limit dal job `train`: il run si interrompe allo stadio; controlla `OPENAI_API_KEY` e i quota di fine-tuning sul tuo account. Rilancia `fine_tune_run`.
- Job OpenAI che fallisce lato loro: lo stadio `train`/`evaluate` riporta lo stato; nessun `FTModel` viene registrato. Rilancia dopo aver risolto la causa.

### Evaluator
- `pass=False` con dataset valido → il modello **non** viene deployato automaticamente (comportamento corretto). Migliora i dati (più conversazioni, quality filter) e rilancia.

## 5. Rollback

- Per disattivare un modello FT in produzione: chiudi/ferma l'esperimento A/B che instrada sull'arm `ft` dall'admin panel. Il routing torna al base model (`gpt-5-mini` default).

## Riferimenti
- `backend/workers/fine_tuning/{run,collect,quality,export,handlers}.py`
- `libs/ai_core/src/ai_core/ft/{anonymizer,presidio}.py`
- `docs/completion-plan.md` (sez. 2.1–2.5), `docs/audit-completamento-2026-06-22.md` (sez. E)
