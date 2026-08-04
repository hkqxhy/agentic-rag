# Knowledge workspace

Git only stores knowledge governance metadata and sanitized test fixtures. Raw school documents, QQ records, parsed artifacts, and indexes stay outside source control.

## Layout

- `manifests/`: versioned metadata that records provenance, authority, validity, review status, and checksums.
- `schemas/`: machine-readable contracts for manifests and ingestion outputs.
- `fixtures/`: small, sanitized, non-production documents used by tests and local smoke runs.
- `raw/`: original files in local/object storage; ignored by Git.
- `normalized/`: parsed and cleaned documents; ignored by Git.
- `artifacts/`: chunks, tables, embeddings, and graph artifacts; ignored by Git.
- `rejected/`: rejected or review-pending files; ignored by Git.

## Publishing boundary

1. Upload or synchronize a source into `raw/` or object storage.
2. Parse and normalize it without modifying the original.
3. Validate provenance, authorization, privacy, authority, applicable audience, and validity period.
4. Generate a manifest and preview the diff.
5. Publish a versioned shadow index.
6. Run retrieval and answer regression suites.
7. Atomically promote the index only after all gates pass.

QQ chat exports may contain personal information and informal advice. They must be desensitized and marked as `community` or `opinion`; they must never override active official documents.
