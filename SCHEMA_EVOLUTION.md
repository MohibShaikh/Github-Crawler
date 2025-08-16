# Schema Evolution Strategy

## Overview

This document outlines how the database schema can evolve to accommodate additional metadata (issues, pull requests, commits, comments, reviews, CI checks) while maintaining efficient updates and minimal row impact.

## Current Schema Design

The initial schema is designed with extensibility in mind:

### Core Principles
1. **Normalized Design**: Separate tables for different entity types
2. **Efficient Indexing**: Strategic indexes for common query patterns
3. **Timestamp Tracking**: `created_at`, `updated_at`, `crawled_at`, `last_modified`
4. **Flexible Arrays**: PostgreSQL arrays for multi-valued fields
5. **Foreign Key Relationships**: Maintains data integrity
6. **Upsert-Friendly**: Uses `ON CONFLICT` for efficient updates

## Schema Evolution Phases

### Phase 1: Repository Metadata (Current)
```sql
repositories
├── Basic info (name, owner, description)
├── Metrics (stars, forks, watchers)
├── Flags (private, fork, archived, disabled)
└── Timestamps (created, updated, pushed, crawled)
```

### Phase 2: Issues and Pull Requests
```sql
issues
├── Core fields (title, body, state, author)
├── Relationships (repo_id, assignees, labels, milestone)
├── Arrays (assignee_logins[], label_names[])
└── Timestamps with crawl tracking

pull_requests  
├── Core fields (title, body, state, author)
├── Git info (head_ref, base_ref, mergeable, merged)
├── Relationships (repo_id, assignees, reviewers, labels)
├── Arrays (assignee_logins[], reviewer_logins[], label_names[])
└── Timestamps with merge tracking
```

### Phase 3: Comments and Reviews
```sql
comments
├── Polymorphic relationship (issue_id OR pr_id)
├── Comment type (issue, pr, review)
├── Content (author, body)
└── Efficient parent tracking

reviews (PR-specific comments)
├── Review-specific fields (state: approved/changes_requested/commented)
├── Relationship to PR
└── Review comments as separate entities
```

### Phase 4: Advanced Metadata
```sql
commits
├── SHA, message, author info
├── Relationship to PR (if applicable)
├── File changes summary
└── CI check references

ci_checks
├── Check details (name, status, conclusion)
├── Relationships (repo_id, pr_id, commit_sha)
├── External references (url, external_id)
└── Timing information
```

## Efficient Update Strategies

### 1. Incremental Update Pattern

```sql
-- Update only changed records using timestamp comparison
UPDATE repositories SET 
    stars_count = EXCLUDED.stars_count,
    updated_at = EXCLUDED.updated_at,
    crawled_at = NOW(),
    last_modified = NOW()
FROM (VALUES 
    (123, 1500, '2024-01-15 10:30:00'),
    (456, 2300, '2024-01-15 11:45:00')
) AS new_data(repo_id, stars_count, updated_at)
WHERE repositories.repo_id = new_data.repo_id 
  AND repositories.updated_at < new_data.updated_at;
```

### 2. Batch Upsert with Conflict Resolution

```sql
-- Efficient batch upsert for comments
INSERT INTO comments (
    comment_id, repo_id, issue_id, pr_id, comment_type,
    author_login, body, created_at, updated_at, crawled_at
) VALUES 
    ($1, $2, $3, NULL, 'issue', $4, $5, $6, $7, NOW()),
    ($8, $9, NULL, $10, 'pr', $11, $12, $13, $14, NOW())
ON CONFLICT (comment_id) DO UPDATE SET
    body = EXCLUDED.body,
    updated_at = EXCLUDED.updated_at,
    crawled_at = NOW(),
    last_modified = NOW()
WHERE comments.updated_at < EXCLUDED.updated_at;
```

### 3. Delta Processing for Large Collections

```python
class CommentDeltaProcessor:
    async def process_comments_delta(self, issue_id: int, new_comments: List[Comment]):
        """Process only new/updated comments for an issue."""
        
        # Get current comment timestamps from DB
        existing_comments = await self.get_comment_timestamps(issue_id)
        existing_map = {c.comment_id: c.updated_at for c in existing_comments}
        
        # Identify new and updated comments
        to_insert = []
        to_update = []
        
        for comment in new_comments:
            if comment.comment_id not in existing_map:
                to_insert.append(comment)
            elif comment.updated_at > existing_map[comment.comment_id]:
                to_update.append(comment)
        
        # Batch operations
        if to_insert:
            await self.batch_insert_comments(to_insert)
        if to_update:
            await self.batch_update_comments(to_update)
            
        return len(to_insert), len(to_update)
```

## Advanced Schema Features

### 1. Partitioning for Time-Series Data

```sql
-- Partition crawl jobs by date for efficient cleanup
CREATE TABLE crawl_jobs (
    id SERIAL,
    job_type TEXT NOT NULL,
    started_at TIMESTAMP WITH TIME ZONE NOT NULL,
    -- ... other fields
) PARTITION BY RANGE (started_at);

-- Monthly partitions
CREATE TABLE crawl_jobs_2024_01 PARTITION OF crawl_jobs
    FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');

CREATE TABLE crawl_jobs_2024_02 PARTITION OF crawl_jobs
    FOR VALUES FROM ('2024-02-01') TO ('2024-03-01');

-- Automatic partition management
CREATE OR REPLACE FUNCTION create_monthly_partition()
RETURNS void AS $$
DECLARE
    start_date date;
    end_date date;
    table_name text;
BEGIN
    start_date := date_trunc('month', CURRENT_DATE + interval '1 month');
    end_date := start_date + interval '1 month';
    table_name := 'crawl_jobs_' || to_char(start_date, 'YYYY_MM');
    
    EXECUTE format('CREATE TABLE %I PARTITION OF crawl_jobs 
                    FOR VALUES FROM (%L) TO (%L)',
                   table_name, start_date, end_date);
END;
$$ LANGUAGE plpgsql;
```

### 2. JSON Storage for Flexible Metadata

```sql
-- Add JSON column for extensible metadata
ALTER TABLE repositories ADD COLUMN metadata JSONB;

-- Create GIN index for JSON queries
CREATE INDEX idx_repositories_metadata_gin ON repositories USING GIN (metadata);

-- Example queries
-- Find repositories with specific topics
SELECT * FROM repositories 
WHERE metadata->'topics' ? 'machine-learning';

-- Find repositories with high activity
SELECT * FROM repositories 
WHERE (metadata->>'commit_count')::int > 1000;

-- Update JSON metadata efficiently
UPDATE repositories 
SET metadata = metadata || '{"last_commit_date": "2024-01-15"}'::jsonb
WHERE repo_id = 123;
```

### 3. Computed Columns and Materialized Views

```sql
-- Materialized view for repository statistics
CREATE MATERIALIZED VIEW repository_stats AS
SELECT 
    r.repo_id,
    r.name,
    r.stars_count,
    COUNT(DISTINCT i.issue_id) as issue_count,
    COUNT(DISTINCT pr.pr_id) as pr_count,
    COUNT(DISTINCT c.comment_id) as comment_count,
    MAX(i.created_at) as latest_issue_date,
    MAX(pr.created_at) as latest_pr_date
FROM repositories r
LEFT JOIN issues i ON r.repo_id = i.repo_id
LEFT JOIN pull_requests pr ON r.repo_id = pr.repo_id  
LEFT JOIN comments c ON r.repo_id = c.repo_id
GROUP BY r.repo_id, r.name, r.stars_count;

CREATE UNIQUE INDEX idx_repository_stats_repo_id ON repository_stats (repo_id);

-- Refresh materialized view efficiently
REFRESH MATERIALIZED VIEW CONCURRENTLY repository_stats;
```

## Handling High-Volume Updates

### 1. Write-Ahead Log Optimization

```sql
-- Optimize for high-frequency updates
SET wal_level = replica;
SET max_wal_size = '4GB';
SET checkpoint_completion_target = 0.9;
SET shared_buffers = '2GB';  -- 25% of RAM
SET effective_cache_size = '6GB';  -- 75% of RAM
```

### 2. Connection Pooling Strategy

```python
class SmartConnectionPool:
    def __init__(self):
        # Separate pools for different operations
        self.read_pool = asyncpg.create_pool(min_size=10, max_size=50)
        self.write_pool = asyncpg.create_pool(min_size=5, max_size=20) 
        self.bulk_pool = asyncpg.create_pool(min_size=2, max_size=10)
    
    async def get_read_connection(self):
        return await self.read_pool.acquire()
    
    async def get_write_connection(self):
        return await self.write_pool.acquire()
    
    async def get_bulk_connection(self):
        return await self.bulk_pool.acquire()
```

### 3. Batch Processing Patterns

```python
class BatchProcessor:
    def __init__(self, batch_size: int = 1000):
        self.batch_size = batch_size
        self.pending_repositories = []
        self.pending_issues = []
        self.pending_comments = []
    
    async def add_repository(self, repo: Repository):
        self.pending_repositories.append(repo)
        if len(self.pending_repositories) >= self.batch_size:
            await self.flush_repositories()
    
    async def add_comments(self, comments: List[Comment]):
        self.pending_comments.extend(comments)
        if len(self.pending_comments) >= self.batch_size:
            await self.flush_comments()
    
    async def flush_all(self):
        """Flush all pending batches."""
        await asyncio.gather(
            self.flush_repositories(),
            self.flush_issues(), 
            self.flush_comments()
        )
```

## Migration Strategy

### 1. Zero-Downtime Migrations

```sql
-- Add new column with default value
ALTER TABLE repositories 
ADD COLUMN IF NOT EXISTS default_branch TEXT DEFAULT 'main';

-- Backfill data in batches
DO $$ 
DECLARE 
    batch_size INTEGER := 10000;
    offset_val INTEGER := 0;
BEGIN
    LOOP
        UPDATE repositories 
        SET default_branch = 'main'  -- or fetch from API
        WHERE default_branch IS NULL 
          AND repo_id >= offset_val 
          AND repo_id < offset_val + batch_size;
        
        EXIT WHEN NOT FOUND;
        offset_val := offset_val + batch_size;
        
        -- Brief pause to avoid overwhelming system
        PERFORM pg_sleep(0.1);
    END LOOP;
END $$;

-- Remove default once backfilled
ALTER TABLE repositories ALTER COLUMN default_branch DROP DEFAULT;
```

### 2. Schema Version Management

```sql
-- Schema versioning table
CREATE TABLE schema_versions (
    version INTEGER PRIMARY KEY,
    applied_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    description TEXT NOT NULL
);

-- Migration tracking
INSERT INTO schema_versions (version, description) VALUES 
(1, 'Initial repository schema'),
(2, 'Added issues and pull requests'),
(3, 'Added comments and reviews'),
(4, 'Added CI checks and commits');
```

## Performance Monitoring

### 1. Query Performance Tracking

```sql
-- Enable query statistics
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Monitor slow queries
SELECT query, calls, total_time, mean_time, rows
FROM pg_stat_statements 
WHERE mean_time > 100  -- queries taking > 100ms on average
ORDER BY mean_time DESC;

-- Index usage analysis
SELECT schemaname, tablename, indexname, idx_scan, idx_tup_read, idx_tup_fetch
FROM pg_stat_user_indexes
WHERE idx_scan = 0  -- unused indexes
ORDER BY schemaname, tablename;
```

### 2. Automated Performance Alerts

```python
class PerformanceMonitor:
    async def check_database_health(self):
        """Monitor database performance metrics."""
        metrics = {
            'slow_queries': await self.count_slow_queries(),
            'table_sizes': await self.get_table_sizes(),
            'index_usage': await self.get_index_usage(),
            'connection_count': await self.get_active_connections()
        }
        
        # Alert on performance issues
        if metrics['slow_queries'] > 100:
            await self.send_alert("High number of slow queries detected")
        
        if any(size > 100_000_000 for size in metrics['table_sizes'].values()):
            await self.send_alert("Large table detected - consider partitioning")
        
        return metrics
```

## Summary

The schema evolution strategy focuses on:

1. **Incremental Addition**: Add new tables and columns without breaking existing functionality
2. **Efficient Updates**: Use timestamp-based delta processing to minimize database impact
3. **Scalable Design**: Leverage PostgreSQL features like partitioning, JSON columns, and materialized views
4. **Performance Monitoring**: Continuous monitoring and optimization of query performance
5. **Zero-Downtime Migrations**: Careful migration strategy to avoid service interruption

This approach allows the system to grow from basic repository data to comprehensive GitHub metadata while maintaining high performance and reliability.
