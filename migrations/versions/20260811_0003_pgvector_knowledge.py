"""Add governed knowledge tables and pgvector embeddings.

Revision ID: 20260811_0003
Revises: 20260805_0002
Create Date: 2026-08-11
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260811_0003"
down_revision: str | None = "20260805_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE knowledge_documents (
            id TEXT,
            source_uri TEXT NOT NULL,
            title TEXT NOT NULL,
            status VARCHAR(24) NOT NULL,
            authority_level VARCHAR(24) NOT NULL,
            checksum VARCHAR(71) NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_knowledge_documents PRIMARY KEY (id),
            CONSTRAINT uq_knowledge_documents_source_uri UNIQUE (source_uri)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE knowledge_chunks (
            id TEXT,
            document_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            content_hash VARCHAR(64) NOT NULL,
            status VARCHAR(24) NOT NULL DEFAULT 'active',
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_knowledge_chunks PRIMARY KEY (id),
            CONSTRAINT fk_knowledge_chunks_document_id_knowledge_documents
                FOREIGN KEY (document_id) REFERENCES knowledge_documents(id) ON DELETE CASCADE
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_knowledge_chunks_document_status "
        "ON knowledge_chunks (document_id, status)"
    )
    op.execute(
        """
        CREATE TABLE knowledge_embeddings (
            chunk_id TEXT NOT NULL,
            embedding_model VARCHAR(80) NOT NULL,
            embedding_version VARCHAR(120) NOT NULL,
            dimensions INTEGER NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            embedding vector(1024) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT pk_knowledge_embeddings
                PRIMARY KEY (chunk_id, embedding_model, embedding_version),
            CONSTRAINT fk_knowledge_embeddings_chunk_id_knowledge_chunks
                FOREIGN KEY (chunk_id) REFERENCES knowledge_chunks(id) ON DELETE CASCADE,
            CONSTRAINT ck_knowledge_embeddings_embedding_dimensions_1024
                CHECK (dimensions = 1024)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_knowledge_embeddings_model_version "
        "ON knowledge_embeddings (embedding_model, embedding_version)"
    )
    op.execute(
        """
        CREATE INDEX ix_knowledge_embeddings_hnsw_cosine
        ON knowledge_embeddings
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_knowledge_embeddings_hnsw_cosine")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_embeddings_model_version")
    op.execute("DROP TABLE IF EXISTS knowledge_embeddings")
    op.execute("DROP INDEX IF EXISTS ix_knowledge_chunks_document_status")
    op.execute("DROP TABLE IF EXISTS knowledge_chunks")
    op.execute("DROP TABLE IF EXISTS knowledge_documents")
    op.execute("DROP EXTENSION IF EXISTS vector")
