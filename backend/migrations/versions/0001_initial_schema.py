from typing import Sequence, Union

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('doc_chunks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('source', sa.String(length=100), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('embedding', sa.JSON().with_variant(pgvector.sqlalchemy.vector.VECTOR(dim=256), 'postgresql'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('title')
    )
    op.create_index(op.f('ix_doc_chunks_source'), 'doc_chunks', ['source'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('tier', sa.String(length=20), nullable=False),
    sa.Column('is_admin', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)
    op.create_table('github_connections',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('github_login', sa.String(length=100), nullable=False),
    sa.Column('access_token_enc', sa.Text(), nullable=False),
    sa.Column('refresh_token_enc', sa.Text(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id')
    )
    op.create_table('prompt_versions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('version', sa.String(length=40), nullable=False),
    sa.Column('system', sa.Text(), nullable=False),
    sa.Column('user_template', sa.Text(), nullable=False),
    sa.Column('params', sa.JSON(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name', 'version')
    )
    op.create_index(op.f('ix_prompt_versions_name'), 'prompt_versions', ['name'], unique=False)
    op.create_table('repositories',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('url', sa.String(length=500), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('private', sa.Boolean(), nullable=False),
    sa.Column('installation_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'url')
    )
    op.create_index(op.f('ix_repositories_user_id'), 'repositories', ['user_id'], unique=False)
    op.create_table('analysis_jobs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('repository_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('progress', sa.Integer(), nullable=False),
    sa.Column('queue_reason', sa.String(length=200), nullable=True),
    sa.Column('commit_sha', sa.String(length=64), nullable=True),
    sa.Column('summary', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('trace_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_analysis_jobs_repository_id'), 'analysis_jobs', ['repository_id'], unique=False)
    op.create_index(op.f('ix_analysis_jobs_status'), 'analysis_jobs', ['status'], unique=False)
    op.create_index(op.f('ix_analysis_jobs_user_id'), 'analysis_jobs', ['user_id'], unique=False)
    op.create_index('ix_job_user_status', 'analysis_jobs', ['user_id', 'status'], unique=False)
    op.create_table('findings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('repository_id', sa.Integer(), nullable=False),
    sa.Column('fingerprint', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('category', sa.String(length=20), nullable=False),
    sa.Column('file_path', sa.String(length=1000), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('last_seen_job_id', sa.String(length=32), nullable=False),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['repository_id'], ['repositories.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('repository_id', 'fingerprint')
    )
    op.create_index(op.f('ix_findings_repository_id'), 'findings', ['repository_id'], unique=False)
    op.create_table('code_issues',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.String(length=32), nullable=False),
    sa.Column('file_path', sa.String(length=1000), nullable=False),
    sa.Column('line_start', sa.Integer(), nullable=False),
    sa.Column('line_end', sa.Integer(), nullable=False),
    sa.Column('severity', sa.String(length=10), nullable=False),
    sa.Column('category', sa.String(length=20), nullable=False),
    sa.Column('agent', sa.String(length=40), nullable=False),
    sa.Column('title', sa.String(length=300), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('suggestion', sa.Text(), nullable=True),
    sa.Column('rule_ref', sa.String(length=100), nullable=True),
    sa.Column('source', sa.String(length=40), nullable=False),
    sa.Column('prompt_version', sa.String(length=80), nullable=True),
    sa.Column('fingerprint', sa.String(length=40), nullable=True),
    sa.ForeignKeyConstraint(['job_id'], ['analysis_jobs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_code_issues_fingerprint'), 'code_issues', ['fingerprint'], unique=False)
    op.create_index('ix_issue_job_file', 'code_issues', ['job_id', 'file_path'], unique=False)
    op.create_table('job_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.String(length=32), nullable=False),
    sa.Column('level', sa.String(length=10), nullable=False),
    sa.Column('agent', sa.String(length=40), nullable=False),
    sa.Column('message', sa.Text(), nullable=False),
    sa.Column('progress', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['analysis_jobs.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_job_logs_job_id'), 'job_logs', ['job_id'], unique=False)
    op.create_table('llm_calls',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('job_id', sa.String(length=32), nullable=True),
    sa.Column('user_id', sa.Integer(), nullable=True),
    sa.Column('agent', sa.String(length=40), nullable=False),
    sa.Column('provider', sa.String(length=20), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('prompt_version', sa.String(length=80), nullable=True),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('input_tokens', sa.Integer(), nullable=False),
    sa.Column('output_tokens', sa.Integer(), nullable=False),
    sa.Column('cost_usd', sa.Float(), nullable=False),
    sa.Column('latency_ms', sa.Integer(), nullable=False),
    sa.Column('redactions', sa.JSON(), nullable=True),
    sa.Column('trace_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['job_id'], ['analysis_jobs.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_llm_calls_created_at'), 'llm_calls', ['created_at'], unique=False)
    op.create_index(op.f('ix_llm_calls_job_id'), 'llm_calls', ['job_id'], unique=False)
    op.create_index(op.f('ix_llm_calls_user_id'), 'llm_calls', ['user_id'], unique=False)
    op.create_table('issue_feedback',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('issue_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('vote', sa.SmallInteger(), nullable=False),
    sa.Column('comment', sa.Text(), nullable=True),
    sa.Column('source', sa.String(length=40), nullable=False),
    sa.Column('prompt_version', sa.String(length=80), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['issue_id'], ['code_issues.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('issue_id', 'user_id')
    )
    op.create_index(op.f('ix_issue_feedback_issue_id'), 'issue_feedback', ['issue_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_issue_feedback_issue_id'), table_name='issue_feedback')
    op.drop_table('issue_feedback')
    op.drop_index(op.f('ix_llm_calls_user_id'), table_name='llm_calls')
    op.drop_index(op.f('ix_llm_calls_job_id'), table_name='llm_calls')
    op.drop_index(op.f('ix_llm_calls_created_at'), table_name='llm_calls')
    op.drop_table('llm_calls')
    op.drop_index(op.f('ix_job_logs_job_id'), table_name='job_logs')
    op.drop_table('job_logs')
    op.drop_index('ix_issue_job_file', table_name='code_issues')
    op.drop_index(op.f('ix_code_issues_fingerprint'), table_name='code_issues')
    op.drop_table('code_issues')
    op.drop_index(op.f('ix_findings_repository_id'), table_name='findings')
    op.drop_table('findings')
    op.drop_index('ix_job_user_status', table_name='analysis_jobs')
    op.drop_index(op.f('ix_analysis_jobs_user_id'), table_name='analysis_jobs')
    op.drop_index(op.f('ix_analysis_jobs_status'), table_name='analysis_jobs')
    op.drop_index(op.f('ix_analysis_jobs_repository_id'), table_name='analysis_jobs')
    op.drop_table('analysis_jobs')
    op.drop_index(op.f('ix_repositories_user_id'), table_name='repositories')
    op.drop_table('repositories')
    op.drop_index(op.f('ix_prompt_versions_name'), table_name='prompt_versions')
    op.drop_table('prompt_versions')
    op.drop_table('github_connections')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_doc_chunks_source'), table_name='doc_chunks')
    op.drop_table('doc_chunks')
