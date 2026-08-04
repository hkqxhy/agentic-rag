# Knowledge manifests

Each production document must have a manifest that conforms to `../schemas/document-manifest.schema.json`. Manifests may be committed only when they contain no credentials or personal data and when the source may legally be referenced by the project.

The future ingestion service will generate and validate these records before a document version can move from `draft` to `active`.
