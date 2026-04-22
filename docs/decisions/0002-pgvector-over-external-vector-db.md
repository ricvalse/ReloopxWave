# ADR 0002 — pgvector on Supabase for RAG, no external vector DB

Status: Accepted (2026-04-21)

## Context

UC-07 needs vector search over per-merchant knowledge bases. Main candidates: Qdrant/Pinecone (dedicated), or Postgres + `pgvector` in the existing Supabase project.

## Decision

Use the `pgvector` extension on Supabase Postgres. Embeddings are 1536-dim (OpenAI `text-embedding-3-small`) on a `kb_chunks.embedding vector(1536)` column with an HNSW index (`vector_cosine_ops`, `m=16`, `ef_construction=64`).

## Consequences

Positive:
- One fewer service to operate, monitor, back up, and secure.
- Tenant isolation is inherited from the same RLS that protects everything else — no second isolation layer to keep in sync.
- Migrations and PITR cover vectors automatically.
- Cross-table joins between `kb_chunks` and `merchants`/`knowledge_base_docs` stay in-engine.

Negative / watch:
- Breaking upgrades to the `vector` extension can require reindexing. Mitigation: pin the extension version and test on staging before every Supabase upgrade (section 15).
- At some future scale, a dedicated vector DB will win on QPS. Revisit before shipping to a tenant with >100k KB docs per merchant.

## Revisit if

- Any single-merchant KB grows past ~500k chunks or >10GB of embeddings.
- RAG latency p99 exceeds 400ms and the bottleneck traces to vector search (not LLM).
