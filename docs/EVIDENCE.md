# Evidence architecture

Checkit Health now uses a retrieval-grounded pipeline:

```text
claim extraction
  → Google ClaimReview + PubMed + ClinicalTrials.gov
  → Gemini embeddings + pgvector and hybrid relevance ranking
  → passage IDs and source metadata
  → evidence-only Gemini synthesis
  → supported / contradicted / mixed / insufficient / not applicable
  → human review and immutable audit event
```

The report generator returns an `insufficient` result without calling the
model when no evidence is retrieved. Every accepted model citation is checked
against the supplied passage IDs; unknown IDs are discarded.

## Configuration

- `EVIDENCE_RETRIEVAL_ENABLED`: enable public medical-source retrieval.
- `EVIDENCE_CACHE_TTL_SECONDS`: in-memory retrieval cache lifetime.
- `ANALYST_API_KEY`: optional key required by review mutation and audit APIs.
- Existing `GOOGLE_FACT_CHECK_KEY`, `GOOGLE_API_KEY`, and `DATABASE_URL`
  variables continue to work.

The cache reduces repeated network and model cost. Postgres enables the
`vector` extension and maintains a 768-dimensional HNSW evidence index.
Without model or database credentials, retrieval falls back to a deterministic
local vector and skips persistence. Retrieval failures are
returned as structured source errors and do not become negative evidence.
Postgres stores the evidence snapshot, evidence state, review state, reviewer,
note, and timestamp. The review API appends a separate audit record.

## Evaluation

The checked-in benchmark covers supported, contradicted, mixed, insufficient,
and non-applicable claims, including high-risk misinformation and personal
experiences.

```bash
cd backend
export PYTHONPATH=app
python scripts/evaluate.py
```

The command reports recall at K and mean reciprocal rank. It makes live public
API requests; use the unit tests for deterministic CI.

## Production notes

- `POST /jobs/check` submits expensive processing to the bounded worker pool;
  poll its returned `status_url`. The synchronous `/check` remains compatible.
- The included worker pool is appropriate for a single Render instance. Use a
  Redis-backed distributed queue before scaling to multiple instances or when
  jobs must survive process restarts.
- This system grades retrieved evidence; it does not diagnose, provide
  emergency assessment, infer account authenticity, or automate enforcement.
