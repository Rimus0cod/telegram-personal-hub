"""Add the user's saved weather location."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "0002_add_last_city"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

def upgrade():
    # Existing installs may have been created by SQLAlchemy create_all(), which
    # does not add columns to already existing tables. Keep this revision safe
    # for both those databases and fresh installs where 0001 used current metadata.
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("users")}
    if "last_city" not in columns:
        op.add_column("users", sa.Column("last_city", sa.String(length=120), nullable=True))

def downgrade():
    columns = {column["name"] for column in inspect(op.get_bind()).get_columns("users")}
    if "last_city" in columns:
        op.drop_column("users", "last_city")
