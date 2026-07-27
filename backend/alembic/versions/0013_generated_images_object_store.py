"""move generated article images out of Postgres into the media bucket

Revision ID: 0013
Revises: 0012
Create Date: 2026-07-27 12:00:00.000000

A few hundred illustrations were most of the database (351 MB of a 459 MB
database on the reference deployment), bloating every dump, backup and
replication stream. The bytes move to the S3-compatible media bucket and the
row keeps only the key.

The copy runs inside this migration's transaction, so a store that is
unreachable or rejects a write aborts the whole thing with every blob still in
Postgres. Object keys are content-addressed, which makes a re-run after such a
failure overwrite exactly what it wrote before.

"""

import sqlalchemy as sa

from alembic import op
from app.config import settings
from app.media_storage import generated_image_key
from app.object_store import ObjectNotFound, s3_object_store_from_settings

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None

# Blobs average ~2 MB; a small batch keeps peak memory bounded on databases
# with thousands of images.
_BATCH = 25


def _media_store():
    return s3_object_store_from_settings(bucket=settings.object_store_media_bucket)


def upgrade() -> None:
    op.add_column("generated_images", sa.Column("object_key", sa.String(512), nullable=True))
    op.add_column("generated_images", sa.Column("byte_size", sa.Integer(), nullable=True))

    conn = op.get_bind()
    pending = conn.execute(sa.text("SELECT count(*) FROM generated_images")).scalar_one()
    if pending:
        # Only built when there is something to move: fresh installs and the
        # test suite must not need object-store credentials to migrate.
        store = _media_store()
        store.ensure_bucket_sync()
        last_id = 0
        while True:
            rows = conn.execute(
                sa.text(
                    "SELECT article_id, content_type, data FROM generated_images "
                    "WHERE article_id > :last ORDER BY article_id LIMIT :limit"
                ),
                {"last": last_id, "limit": _BATCH},
            ).all()
            if not rows:
                break
            for article_id, content_type, data in rows:
                data = bytes(data)
                key = generated_image_key(article_id, data)
                store.put_sync(key, data, content_type=content_type or "image/png")
                conn.execute(
                    sa.text(
                        "UPDATE generated_images SET object_key = :key, byte_size = :size "
                        "WHERE article_id = :id"
                    ),
                    {"key": key, "size": len(data), "id": article_id},
                )
                last_id = article_id

    op.alter_column("generated_images", "object_key", nullable=False)
    op.alter_column("generated_images", "byte_size", nullable=False)
    op.drop_column("generated_images", "data")


def downgrade() -> None:
    op.add_column("generated_images", sa.Column("data", sa.LargeBinary(), nullable=True))

    conn = op.get_bind()
    pending = conn.execute(sa.text("SELECT count(*) FROM generated_images")).scalar_one()
    if pending:
        store = _media_store()
        last_id = 0
        while True:
            rows = conn.execute(
                sa.text(
                    "SELECT article_id, object_key FROM generated_images "
                    "WHERE article_id > :last ORDER BY article_id LIMIT :limit"
                ),
                {"last": last_id, "limit": _BATCH},
            ).all()
            if not rows:
                break
            for article_id, object_key in rows:
                last_id = article_id
                try:
                    data = store.get_sync(object_key)
                except ObjectNotFound:
                    # Nothing to restore; the row is dropped rather than left
                    # with a NULL the NOT NULL below would reject.
                    conn.execute(
                        sa.text("DELETE FROM generated_images WHERE article_id = :id"),
                        {"id": article_id},
                    )
                    continue
                conn.execute(
                    sa.text("UPDATE generated_images SET data = :data WHERE article_id = :id"),
                    {"data": data, "id": article_id},
                )

    op.alter_column("generated_images", "data", nullable=False)
    op.drop_column("generated_images", "byte_size")
    op.drop_column("generated_images", "object_key")
