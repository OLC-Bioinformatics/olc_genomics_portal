CREATE TABLE IF NOT EXISTS retrieval_requests (
    request_id UUID PRIMARY KEY,
    query TEXT NOT NULL,
    access_context TEXT NOT NULL
        CHECK (access_context IN ('standard', 'internal')),
    result_count INTEGER NOT NULL CHECK (result_count >= 0),
    result_chunk_keys TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS retrieval_requests_created_at_idx
ON retrieval_requests (created_at);

CREATE TABLE IF NOT EXISTS retrieval_feedback (
    id BIGSERIAL PRIMARY KEY,
    request_id UUID NOT NULL
        REFERENCES retrieval_requests(request_id)
        ON DELETE CASCADE,
    target_type TEXT NOT NULL DEFAULT 'retrieval_response'
        CHECK (target_type IN ('retrieval_response')),
    rating TEXT NOT NULL
        CHECK (rating IN ('helpful', 'unhelpful')),
    reason TEXT,
    comment TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (request_id, target_type),
    CHECK (
        (rating = 'helpful' AND reason IS NULL)
        OR
        (rating = 'unhelpful' AND reason IN (
            'irrelevant_results',
            'missing_documentation',
            'unclear_documentation',
            'outdated_documentation',
            'insufficient_detail',
            'other'
        ))
    )
);

CREATE INDEX IF NOT EXISTS retrieval_feedback_rating_idx
ON retrieval_feedback (rating);

CREATE INDEX IF NOT EXISTS retrieval_feedback_created_at_idx
ON retrieval_feedback (created_at);
