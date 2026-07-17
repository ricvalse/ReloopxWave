# ADR 0019 — Media WhatsApp in ingresso: download, inbox, vision AI

**Stato:** Accettato — 2026-07-16
**Contesto ispiratore:** Amalia (`/Users/riccardo/Progetti/Amalia/amalia-ai`, backend Python `services/backend/`), dove le foto in inbox funzionano.

## Problema

Prima di questo lavoro Reloop **non scaricava** i media WhatsApp in ingresso: il
webhook estraeva il `type` ma buttava il media id dentro `raw`, sostituiva il
contenuto con un placeholder testuale (`[Il cliente ha inviato un'immagine]`) e
scartava perfino la **caption** (la domanda del cliente andava persa). Richiesta:
scaricare i media, mostrarli in inbox, e far sì che l'AI/operatore risponda.

## Decisioni

### 1. Storage: bucket privato + signed URL, NON il proxy authless di Amalia
Amalia serve i media da `/image-proxy/{bucket}/{key}` **senza autenticazione**
(valida solo il nome del bucket): chi indovina `{store}/{msg}.jpg` scarica la foto
di qualsiasi cliente. In un single-tenant Shopify è tollerabile; in Reloop
(multitenant a due livelli + RLS ovunque) è una regressione di isolamento.
→ Bucket privato `whatsapp-media` (migration 0046), path
`{merchant_id}/{conversation_id}/{message_id}{ext}`, policy RLS `SELECT` sul
prefisso `merchant_id` (convenzione di 0003). Il FE ottiene un **signed URL** da
un endpoint FastAPI (`GET /conversations/{cid}/messages/{mid}/media`) che firma
con la **service role** dopo un hard-check del prefisso `merchant_id` (guard IDOR,
copiato da `_resolve_header_image_url`). È l'unica opzione che regge anche sotto
impersonation agency→merchant (il token HS256 non firmerebbe una read Storage).

### 2. Download nel worker via protocollo iniettato (dependency inversion)
Il download (client D360 per-merchant), lo storage (Supabase) e la trascrizione
(Whisper) vivono in `workers/conversation/media_pipeline.py`
(`WhatsAppMediaPipeline`), iniettato in `ConversationService` come protocollo
`MediaPipeline` — esattamente come `ReplySender`. `ai_core` resta ignaro di
360dialog/Supabase/OpenAI. `fetch_and_store` è best-effort (mai solleva; ritorna
un patch `meta.media` con `storage_path`/`size_bytes`/`transcription` oppure
`error`). Il download avviene **dentro `handle_inbound_persist`**, in transazione,
dopo aver scritto la riga (two-phase shape di Amalia: `storage_path=null` →
patch). Le immagini scaricano sotto il secondo; video/documenti (che vanno
comunque in handoff) sono l'unico caso lento.

### 3. Dettaglio load-bearing: swap host `lookaside.fbsbx.com` → proxy 360dialog
`download_media` è a due passi (metadata → binary). L'URL restituito da Meta punta
a `lookaside.fbsbx.com`, che richiede un token Facebook che i partner 360dialog
**non hanno**. Bisogna riscrivere l'host sul proxy 360dialog (inietta il token
server-side) + `.replace("\\", "")` per gli artefatti di escaping JSON. Senza
questo swap il download dà 401 anche con la API key giusta. È il motivo per cui
"da Amalia funziona" (`dialog360.py:659-665`).

### 4. Vision: campo `ImagePart` opzionale, NON union su `content`
Il piano suggeriva di allargare `ChatMessage.content` a `str | list[dict]` in tutto
`ai_core` (router, playground, export FT). Troppo invasivo. Scelto: campo
opzionale `image: ImagePart | None` su `ChatMessage`; `content` resta `str`
ovunque e solo i due serializer per-provider guardano `image` (OpenAI
`image_url` data-URI; Anthropic blocco `image` base64 nativo — dialetti diversi,
Amalia essendo Anthropic-only non affrontava questo). L'immagine viene ricaricata
**da storage** nel reply path (`_resolve_current_media`), non passata in memoria:
è l'unico modo che sopravvive al debounce (che ri-risolve il contesto dal DB).
Solo il turno corrente porta l'immagine; la history resta testo (scelta di
costo/contesto, come Amalia).

### 5. `_MEDIA_NOTE` sostituito quando l'immagine è allegata
Il system prompt contiene sempre una nota "NON puoi vedere i media". Quando
alleghiamo davvero l'immagine per la vision va **sostituita** con una direttiva
"la stai vedendo, rispondi nel merito", altrimenti il modello rifiuta la foto che
sta guardando — la stessa classe di regressione di
`project_ai_ignores_automation_context` (un hint stale che sovrascrive il
contesto reale). `render_schema_hint(..., viewable_media=True)` fa lo swap; il
default resta byte-identico (golden test).

### 6. Audio con trascrizione, video/documenti in handoff
Voice note → Whisper (`whisper-1`, `language="it"`) → `meta.media.transcription`,
usata come testo effettivo del turno così l'AI la "legge". Video/documenti
mantengono l'handoff a umano (`_HANDOFF_MEDIA`, policy identica ad Amalia) **ma
ora vengono scaricati e mostrati** in inbox — miglioramento stretto. Gli echo di
Coexistence (foto inviata dal telefono del merchant) sono anch'essi scaricati e
mostrati, senza turno LLM.

## Fuori scope (V1)
- **Invio** di foto in uscita da operatore/AI (Amalia non è un riferimento: ha
  solo `send_text`/`send_template`). Greenfield, rinviato.
- Retention/TTL dei media (foto = dato personale): `SupabaseStorage` non ha
  `delete`; da aggiungere con un cron di cleanup in un secondo momento.
- Re-attach di immagini di turni precedenti (la history resta testo).

## Conseguenze
- Nuova migration `0046` (bucket + RLS) e nuovo setting `supabase_media_bucket`.
- `handle_inbound_message` / `handle_phone_app_echo` (worker + ai_core) accettano
  un descrittore `media`; il webhook lo passa via arg posizionale.
- OpenAPI cambiato (nuovo endpoint `.../media` + `MediaUrlOut`) → client generato
  rigenerato.
- FE: `MessageMedia` + lightbox (fix del bug Escape di Amalia via listener su
  `document`), `useMessageMedia` (signed URL on-demand), memo comparator della
  bolla esteso a `meta.media.storage_path/error/transcription` (altrimenti
  l'UPDATE Realtime del two-phase non ri-renderizza).
