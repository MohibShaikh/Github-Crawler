"""Database infrastructure implementation using asyncpg."""

import asyncio
import json
import csv
from datetime import datetime
from typing import List, Optional, Dict, Any, Union, Set
from pathlib import Path

import asyncpg
import structlog
from asyncpg import Connection, Pool

from ..domain.entities import (
    Repository, Issue, PullRequest, Comment, CICheck, 
    CrawlJob, RateLimitInfo, RepositoryOwnerType, 
    IssueState, PullRequestState, CommentType,
    CheckStatus, CheckConclusion
)
from ..domain.repositories import (
    RepositoryRepository as RepositoryRepositoryInterface,
    IssueRepository as IssueRepositoryInterface,
    PullRequestRepository as PullRequestRepositoryInterface,
    CommentRepository as CommentRepositoryInterface,
    CICheckRepository as CICheckRepositoryInterface,
    CrawlJobRepository as CrawlJobRepositoryInterface,
    RateLimitRepository as RateLimitRepositoryInterface,
    DatabaseRepository as DatabaseRepositoryInterface
)

logger = structlog.get_logger()


class DatabasePool:
    """Database connection pool manager."""
    
    def __init__(self, database_url: str, min_size: int = 10, max_size: int = 20):
        self.database_url = database_url
        self.min_size = min_size
        self.max_size = max_size
        self._pool: Optional[Pool] = None
    
    async def initialize(self) -> None:
        """Initialize the connection pool."""
        try:
            self._pool = await asyncpg.create_pool(
                self.database_url,
                min_size=self.min_size,
                max_size=self.max_size,
                command_timeout=60
            )
            logger.info("Database pool initialized", min_size=self.min_size, max_size=self.max_size)
        except Exception as e:
            logger.error("Failed to initialize database pool", error=str(e))
            raise
    
    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool:
            await self._pool.close()
            logger.info("Database pool closed")
    
    async def acquire(self) -> Connection:
        """Acquire a connection from the pool."""
        if not self._pool:
            raise RuntimeError("Database pool not initialized")
        return await self._pool.acquire()
    
    async def release(self, connection: Connection) -> None:
        """Release a connection back to the pool."""
        if self._pool:
            await self._pool.release(connection)


class PostgreSQLRepositoryRepository(RepositoryRepositoryInterface):
    """PostgreSQL implementation of repository data access."""
    
    def __init__(self, db_pool: DatabasePool):
        self.db_pool = db_pool
    
    async def get_by_repo_id(self, repo_id: int) -> Optional[Repository]:
        """Get repository by GitHub repo ID."""
        connection = await self.db_pool.acquire()
        try:
            row = await connection.fetchrow(
                "SELECT * FROM repositories WHERE repo_id = $1", repo_id
            )
            return self._row_to_repository(row) if row else None
        finally:
            await self.db_pool.release(connection)
    
    async def get_existing_repo_ids(self, repo_ids: List[int]) -> Set[int]:
        """Get set of existing repository IDs from a list of IDs."""
        if not repo_ids:
            return set()
            
        connection = await self.db_pool.acquire()
        try:
            # Use ANY() for efficient batch lookup
            rows = await connection.fetch(
                "SELECT repo_id FROM repositories WHERE repo_id = ANY($1)", 
                repo_ids
            )
            return {row['repo_id'] for row in rows}
        finally:
            await self.db_pool.release(connection)
    
    async def get_by_owner_and_name(self, owner: str, name: str) -> Optional[Repository]:
        """Get repository by owner and name."""
        connection = await self.db_pool.acquire()
        try:
            full_name = f"{owner}/{name}"
            row = await connection.fetchrow(
                "SELECT * FROM repositories WHERE full_name = $1", full_name
            )
            return self._row_to_repository(row) if row else None
        finally:
            await self.db_pool.release(connection)
    
    async def save(self, repository: Repository) -> Repository:
        """Save repository (insert or update)."""
        connection = await self.db_pool.acquire()
        try:
            await connection.execute("""
                INSERT INTO repositories (
                    repo_id, name, full_name, owner_login, owner_type,
                    stars_count, forks_count, watchers_count, language, description,
                    is_private, is_fork, is_archived, is_disabled,
                    created_at, updated_at, pushed_at, crawled_at, last_modified
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19)
                ON CONFLICT (repo_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    full_name = EXCLUDED.full_name,
                    owner_login = EXCLUDED.owner_login,
                    owner_type = EXCLUDED.owner_type,
                    stars_count = EXCLUDED.stars_count,
                    forks_count = EXCLUDED.forks_count,
                    watchers_count = EXCLUDED.watchers_count,
                    language = EXCLUDED.language,
                    description = EXCLUDED.description,
                    is_private = EXCLUDED.is_private,
                    is_fork = EXCLUDED.is_fork,
                    is_archived = EXCLUDED.is_archived,
                    is_disabled = EXCLUDED.is_disabled,
                    updated_at = EXCLUDED.updated_at,
                    pushed_at = EXCLUDED.pushed_at,
                    crawled_at = EXCLUDED.crawled_at,
                    last_modified = NOW()
            """, 
                repository.repo_id,
                repository.name,
                repository.full_name,
                repository.owner_login,
                repository.owner_type.value,
                repository.stars_count,
                repository.forks_count,
                repository.watchers_count,
                repository.language,
                repository.description,
                repository.is_private,
                repository.is_fork,
                repository.is_archived,
                repository.is_disabled,
                repository.created_at,
                repository.updated_at,
                repository.pushed_at,
                repository.crawled_at or datetime.utcnow(),
                datetime.utcnow()
            )
            return repository
        finally:
            await self.db_pool.release(connection)
    
    async def save_batch(self, repositories: List[Repository]) -> List[Repository]:
        """Save multiple repositories efficiently."""
        if not repositories:
            return []
            
        connection = await self.db_pool.acquire()
        try:
            # Use staging table + COPY for much faster batch upsert
            await connection.execute("""
                CREATE TEMP TABLE IF NOT EXISTS repositories_staging (
                    repo_id BIGINT,
                    name TEXT,
                    full_name TEXT,
                    owner_login TEXT,
                    owner_type TEXT,
                    stars_count INTEGER,
                    forks_count INTEGER,
                    watchers_count INTEGER,
                    language TEXT,
                    description TEXT,
                    is_private BOOLEAN,
                    is_fork BOOLEAN,
                    is_archived BOOLEAN,
                    is_disabled BOOLEAN,
                    created_at TIMESTAMPTZ,
                    updated_at TIMESTAMPTZ,
                    pushed_at TIMESTAMPTZ,
                    crawled_at TIMESTAMPTZ,
                    last_modified TIMESTAMPTZ
                ) ON COMMIT DROP;
            """)

            # Build CSV-like payload in-memory for COPY
            rows = []
            now = datetime.utcnow()
            for repo in repositories:
                rows.append('\t'.join([
                    str(repo.repo_id),
                    repo.name or '',
                    repo.full_name or '',
                    repo.owner_login or '',
                    repo.owner_type.value,
                    str(repo.stars_count),
                    str(repo.forks_count or 0),
                    str(repo.watchers_count or 0),
                    repo.language or '',
                    repo.description.replace('\t',' ').replace('\n',' ') if repo.description else '',
                    't' if repo.is_private else 'f',
                    't' if repo.is_fork else 'f',
                    't' if repo.is_archived else 'f',
                    't' if repo.is_disabled else 'f',
                    (repo.created_at or now).isoformat(),
                    (repo.updated_at or now).isoformat(),
                    (repo.pushed_at or now).isoformat() if repo.pushed_at else '',
                    (repo.crawled_at or now).isoformat(),
                    now.isoformat()
                ]))

            copy_payload = ('\n'.join(rows)).encode('utf-8')
            await connection.copy_to_table('repositories_staging', source=copy_payload, format='csv', delimiter='\t', header=False)

            # Merge from staging into main table
            await connection.execute("""
                INSERT INTO repositories (
                    repo_id, name, full_name, owner_login, owner_type,
                    stars_count, forks_count, watchers_count, language, description,
                    is_private, is_fork, is_archived, is_disabled,
                    created_at, updated_at, pushed_at, crawled_at, last_modified
                )
                SELECT 
                    repo_id, name, full_name, owner_login, owner_type,
                    stars_count, forks_count, watchers_count, language, description,
                    is_private, is_fork, is_archived, is_disabled,
                    created_at, updated_at, pushed_at, crawled_at, last_modified
                FROM repositories_staging
                ON CONFLICT (repo_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    full_name = EXCLUDED.full_name,
                    owner_login = EXCLUDED.owner_login,
                    owner_type = EXCLUDED.owner_type,
                    stars_count = EXCLUDED.stars_count,
                    forks_count = EXCLUDED.forks_count,
                    watchers_count = EXCLUDED.watchers_count,
                    language = EXCLUDED.language,
                    description = EXCLUDED.description,
                    is_private = EXCLUDED.is_private,
                    is_fork = EXCLUDED.is_fork,
                    is_archived = EXCLUDED.is_archived,
                    is_disabled = EXCLUDED.is_disabled,
                    updated_at = EXCLUDED.updated_at,
                    pushed_at = EXCLUDED.pushed_at,
                    crawled_at = EXCLUDED.crawled_at,
                    last_modified = NOW();
            """)

            return repositories
        finally:
            await self.db_pool.release(connection)
    
    async def get_all_paginated(
        self, 
        offset: int = 0, 
        limit: int = 1000,
        updated_since: Optional[datetime] = None
    ) -> List[Repository]:
        """Get repositories with pagination and optional filtering."""
        connection = await self.db_pool.acquire()
        try:
            if updated_since:
                rows = await connection.fetch("""
                    SELECT * FROM repositories 
                    WHERE updated_at > $1 
                    ORDER BY repo_id 
                    LIMIT $2 OFFSET $3
                """, updated_since, limit, offset)
            else:
                rows = await connection.fetch("""
                    SELECT * FROM repositories 
                    ORDER BY repo_id 
                    LIMIT $1 OFFSET $2
                """, limit, offset)
            
            return [self._row_to_repository(row) for row in rows]
        finally:
            await self.db_pool.release(connection)
    
    async def count(self, updated_since: Optional[datetime] = None) -> int:
        """Count repositories with optional filtering."""
        connection = await self.db_pool.acquire()
        try:
            if updated_since:
                result = await connection.fetchval(
                    "SELECT COUNT(*) FROM repositories WHERE updated_at > $1", 
                    updated_since
                )
            else:
                result = await connection.fetchval("SELECT COUNT(*) FROM repositories")
            return result
        finally:
            await self.db_pool.release(connection)
    
    async def get_stale_repositories(
        self, 
        older_than: datetime,
        limit: int = 1000
    ) -> List[Repository]:
        """Get repositories that haven't been updated recently."""
        connection = await self.db_pool.acquire()
        try:
            rows = await connection.fetch("""
                SELECT * FROM repositories 
                WHERE crawled_at < $1 
                ORDER BY crawled_at ASC 
                LIMIT $2
            """, older_than, limit)
            
            return [self._row_to_repository(row) for row in rows]
        finally:
            await self.db_pool.release(connection)
    
    def _row_to_repository(self, row) -> Repository:
        """Convert database row to Repository entity."""
        return Repository(
            repo_id=row['repo_id'],
            name=row['name'],
            full_name=row['full_name'],
            owner_login=row['owner_login'],
            owner_type=RepositoryOwnerType(row['owner_type']),
            stars_count=row['stars_count'],
            forks_count=row['forks_count'],
            watchers_count=row['watchers_count'],
            language=row['language'],
            description=row['description'],
            is_private=row['is_private'],
            is_fork=row['is_fork'],
            is_archived=row['is_archived'],
            is_disabled=row['is_disabled'],
            created_at=row['created_at'],
            updated_at=row['updated_at'],
            pushed_at=row['pushed_at'],
            crawled_at=row['crawled_at']
        )


class PostgreSQLCrawlJobRepository(CrawlJobRepositoryInterface):
    """PostgreSQL implementation of crawl job tracking."""
    
    def __init__(self, db_pool: DatabasePool):
        self.db_pool = db_pool
    
    async def create_job(self, job: CrawlJob) -> CrawlJob:
        """Create a new crawl job."""
        connection = await self.db_pool.acquire()
        try:
            job_id = await connection.fetchval("""
                INSERT INTO crawl_jobs (
                    job_type, started_at, status, records_processed,
                    records_created, records_updated, last_cursor, metadata
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING id
            """, 
                job.job_type, job.started_at, job.status,
                job.records_processed, job.records_created, job.records_updated,
                job.last_cursor, json.dumps(job.metadata) if job.metadata else None
            )
            
            return CrawlJob(
                job_id=job_id,
                job_type=job.job_type,
                started_at=job.started_at,
                completed_at=job.completed_at,
                status=job.status,
                records_processed=job.records_processed,
                records_created=job.records_created,
                records_updated=job.records_updated,
                last_cursor=job.last_cursor,
                error_message=job.error_message,
                metadata=job.metadata
            )
        finally:
            await self.db_pool.release(connection)
    
    async def update_job(self, job: CrawlJob) -> CrawlJob:
        """Update an existing crawl job."""
        connection = await self.db_pool.acquire()
        try:
            await connection.execute("""
                UPDATE crawl_jobs SET
                    completed_at = $2,
                    status = $3,
                    records_processed = $4,
                    records_created = $5,
                    records_updated = $6,
                    last_cursor = $7,
                    error_message = $8,
                    metadata = $9
                WHERE id = $1
            """,
                job.job_id, job.completed_at, job.status,
                job.records_processed, job.records_created, job.records_updated,
                job.last_cursor, job.error_message,
                json.dumps(job.metadata) if job.metadata else None
            )
            return job
        finally:
            await self.db_pool.release(connection)
    
    async def get_job(self, job_id: int) -> Optional[CrawlJob]:
        """Get crawl job by ID."""
        connection = await self.db_pool.acquire()
        try:
            row = await connection.fetchrow("SELECT * FROM crawl_jobs WHERE id = $1", job_id)
            return self._row_to_crawl_job(row) if row else None
        finally:
            await self.db_pool.release(connection)
    
    async def get_latest_job(self, job_type: str) -> Optional[CrawlJob]:
        """Get the latest job of a specific type."""
        connection = await self.db_pool.acquire()
        try:
            row = await connection.fetchrow("""
                SELECT * FROM crawl_jobs 
                WHERE job_type = $1 
                ORDER BY started_at DESC 
                LIMIT 1
            """, job_type)
            return self._row_to_crawl_job(row) if row else None
        finally:
            await self.db_pool.release(connection)
    
    async def get_active_jobs(self) -> List[CrawlJob]:
        """Get all currently running jobs."""
        connection = await self.db_pool.acquire()
        try:
            rows = await connection.fetch("""
                SELECT * FROM crawl_jobs 
                WHERE status = 'running' 
                ORDER BY started_at
            """)
            return [self._row_to_crawl_job(row) for row in rows]
        finally:
            await self.db_pool.release(connection)
    
    def _row_to_crawl_job(self, row) -> CrawlJob:
        """Convert database row to CrawlJob entity."""
        metadata = json.loads(row['metadata']) if row['metadata'] else None
        return CrawlJob(
            job_id=row['id'],
            job_type=row['job_type'],
            started_at=row['started_at'],
            completed_at=row['completed_at'],
            status=row['status'],
            records_processed=row['records_processed'],
            records_created=row['records_created'],
            records_updated=row['records_updated'],
            last_cursor=row['last_cursor'],
            error_message=row['error_message'],
            metadata=metadata
        )


class PostgreSQLRateLimitRepository(RateLimitRepositoryInterface):
    """PostgreSQL implementation of rate limit tracking."""
    
    def __init__(self, db_pool: DatabasePool):
        self.db_pool = db_pool
    
    async def save_rate_limit_status(
        self, 
        api_type: str, 
        limit_type: str, 
        rate_limit: RateLimitInfo
    ) -> None:
        """Save current rate limit status."""
        connection = await self.db_pool.acquire()
        try:
            await connection.execute("""
                INSERT INTO rate_limit_status (
                    api_type, limit_type, remaining, reset_at, recorded_at
                ) VALUES ($1, $2, $3, $4, $5)
            """, api_type, limit_type, rate_limit.remaining, rate_limit.reset_at, datetime.utcnow())
        finally:
            await self.db_pool.release(connection)
    
    async def get_latest_rate_limit_status(
        self, 
        api_type: str, 
        limit_type: str
    ) -> Optional[RateLimitInfo]:
        """Get the most recent rate limit status."""
        connection = await self.db_pool.acquire()
        try:
            row = await connection.fetchrow("""
                SELECT * FROM rate_limit_status 
                WHERE api_type = $1 AND limit_type = $2 
                ORDER BY recorded_at DESC 
                LIMIT 1
            """, api_type, limit_type)
            
            if not row:
                return None
                
            # Calculate limit and used from remaining (approximation)
            remaining = row['remaining']
            limit = 5000  # Default GitHub API limit
            used = limit - remaining
            
            return RateLimitInfo(
                limit=limit,
                remaining=remaining,
                reset_at=row['reset_at'],
                used=used
            )
        finally:
            await self.db_pool.release(connection)


class PostgreSQLDatabaseRepository(DatabaseRepositoryInterface):
    """PostgreSQL implementation of database operations."""
    
    def __init__(self, db_pool: DatabasePool):
        self.db_pool = db_pool
    
    async def execute_migration(self, migration_sql: str) -> None:
        """Execute a database migration script."""
        connection = await self.db_pool.acquire()
        try:
            await connection.execute(migration_sql)
            logger.info("Migration executed successfully")
        finally:
            await self.db_pool.release(connection)
    
    async def export_to_csv(self, table_name: str, file_path: str) -> None:
        """Export table data to CSV."""
        connection = await self.db_pool.acquire()
        try:
            rows = await connection.fetch(f"SELECT * FROM {table_name}")
            
            with open(file_path, 'w', newline='', encoding='utf-8') as csvfile:
                if rows:
                    # Get fieldnames from the first row
                    fieldnames = list(rows[0].keys())
                    writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                    writer.writeheader()
                    
                    for row in rows:
                        # Convert row to dict and ensure all values are strings
                        row_dict = {}
                        for key, value in row.items():
                            if value is None:
                                row_dict[key] = ''
                            elif isinstance(value, datetime):
                                row_dict[key] = value.isoformat()
                            else:
                                row_dict[key] = str(value)
                        writer.writerow(row_dict)
            
            logger.info("Data exported to CSV", table=table_name, file=file_path, rows=len(rows))
        finally:
            await self.db_pool.release(connection)
    
    async def export_to_json(self, table_name: str, file_path: str) -> None:
        """Export table data to JSON."""
        connection = await self.db_pool.acquire()
        try:
            rows = await connection.fetch(f"SELECT * FROM {table_name}")
            
            # Convert rows to JSON-serializable format
            data = []
            for row in rows:
                row_dict = {}
                for key, value in row.items():
                    if value is None:
                        row_dict[key] = None
                    elif isinstance(value, datetime):
                        row_dict[key] = value.isoformat()
                    elif isinstance(value, (int, float, str, bool)):
                        row_dict[key] = value
                    else:
                        row_dict[key] = str(value)
                data.append(row_dict)
            
            with open(file_path, 'w', encoding='utf-8') as jsonfile:
                json.dump(data, jsonfile, indent=2, ensure_ascii=False)
            
            logger.info("Data exported to JSON", table=table_name, file=file_path, rows=len(rows))
        finally:
            await self.db_pool.release(connection)
    
    async def get_table_stats(self) -> Dict[str, Any]:
        """Get statistics about all tables."""
        connection = await self.db_pool.acquire()
        try:
            stats = {}
            
            # Get row counts for each table
            tables = ['repositories', 'issues', 'pull_requests', 'comments', 'ci_checks', 'crawl_jobs']
            for table in tables:
                count = await connection.fetchval(f"SELECT COUNT(*) FROM {table}")
                stats[f"{table}_count"] = count
            
            # Get latest crawl timestamps
            latest_repo_crawl = await connection.fetchval(
                "SELECT MAX(crawled_at) FROM repositories"
            )
            stats['latest_repository_crawl'] = latest_repo_crawl.isoformat() if latest_repo_crawl else None
            
            # Get database size
            db_size = await connection.fetchval("""
                SELECT pg_size_pretty(pg_database_size(current_database()))
            """)
            stats['database_size'] = db_size
            
            return stats
        finally:
            await self.db_pool.release(connection)
