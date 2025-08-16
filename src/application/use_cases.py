"""Application use cases orchestrating business logic."""

import asyncio
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
import structlog

from ..domain.entities import Repository, CrawlJob, RateLimitInfo
from ..domain.repositories import (
    RepositoryRepository,
    CrawlJobRepository,
    RateLimitRepository,
    DatabaseRepository
)
from ..infrastructure.github_api import GitHubRepositoryCrawler, GitHubGraphQLClient

logger = structlog.get_logger()


class CrawlRepositoriesUseCase:
    """Use case for crawling GitHub repositories."""
    
    def __init__(
        self,
        repository_repo: RepositoryRepository,
        crawl_job_repo: CrawlJobRepository,
        rate_limit_repo: RateLimitRepository,
        github_client: GitHubGraphQLClient
    ):
        self.repository_repo = repository_repo
        self.crawl_job_repo = crawl_job_repo
        self.rate_limit_repo = rate_limit_repo
        self.github_client = github_client
        self.crawler = GitHubRepositoryCrawler(github_client)
    
    async def execute(
        self, 
        target_count: int = 100000,
        batch_size: int = 1000,
        resume_from_last: bool = True
    ) -> CrawlJob:
        """
        Execute repository crawling use case.
        
        Args:
            target_count: Number of repositories to crawl
            batch_size: Size of batches to process at once
            resume_from_last: Whether to resume from last crawl job
            
        Returns:
            Completed crawl job
        """
        # Create crawl job
        job = CrawlJob(
            job_id=None,
            job_type="repositories",
            started_at=datetime.utcnow(),
            completed_at=None,
            status="running",
            metadata={"target_count": target_count, "batch_size": batch_size}
        )
        
        job = await self.crawl_job_repo.create_job(job)
        logger.info("Started repository crawl job", 
                   job_id=job.job_id, target_count=target_count)
        
        try:
            # Determine starting cursor
            start_cursor = None
            if resume_from_last:
                last_job = await self.crawl_job_repo.get_latest_job("repositories")
                if last_job and last_job.last_cursor:
                    start_cursor = last_job.last_cursor
                    logger.info("Resuming from previous crawl", cursor=start_cursor)
            
            # Crawl repositories in batches
            repositories_batch = []
            async for repositories, cursor in self.crawler.crawl_repositories(
                target_count=target_count,
                start_cursor=start_cursor
            ):
                repositories_batch.extend(repositories)
                
                # Process in configured batch sizes
                if len(repositories_batch) >= batch_size:
                    processed, created, updated = await self._process_batch(repositories_batch, job)
                    # Create new job instance with updated counts (immutable)
                    job = CrawlJob(
                        job_id=job.job_id,
                        job_type=job.job_type,
                        started_at=job.started_at,
                        completed_at=job.completed_at,
                        status=job.status,
                        records_processed=job.records_processed + processed,
                        records_created=job.records_created + created,
                        records_updated=job.records_updated + updated,
                        last_cursor=job.last_cursor,
                        error_message=job.error_message,
                        metadata=job.metadata
                    )
                    repositories_batch = []
                
                # Update job progress
                job = CrawlJob(
                    job_id=job.job_id,
                    job_type=job.job_type,
                    started_at=job.started_at,
                    completed_at=job.completed_at,
                    status=job.status,
                    records_processed=job.records_processed,
                    records_created=job.records_created,
                    records_updated=job.records_updated,
                    last_cursor=cursor,
                    error_message=job.error_message,
                    metadata=job.metadata
                )
                await self.crawl_job_repo.update_job(job)
                
                # Save rate limit info
                if self.github_client.rate_limit_info:
                    await self.rate_limit_repo.save_rate_limit_status(
                        "graphql", "primary", self.github_client.rate_limit_info
                    )
            
            # Process remaining repositories
            if repositories_batch:
                processed, created, updated = await self._process_batch(repositories_batch, job)
                # Update job with final counts
                job = CrawlJob(
                    job_id=job.job_id,
                    job_type=job.job_type,
                    started_at=job.started_at,
                    completed_at=job.completed_at,
                    status=job.status,
                    records_processed=job.records_processed + processed,
                    records_created=job.records_created + created,
                    records_updated=job.records_updated + updated,
                    last_cursor=job.last_cursor,
                    error_message=job.error_message,
                    metadata=job.metadata
                )
            
            # Mark job as completed
            job = CrawlJob(
                job_id=job.job_id,
                job_type=job.job_type,
                started_at=job.started_at,
                completed_at=datetime.utcnow(),
                status="completed",
                records_processed=job.records_processed,
                records_created=job.records_created,
                records_updated=job.records_updated,
                last_cursor=job.last_cursor,
                error_message=None,
                metadata=job.metadata
            )
            
            await self.crawl_job_repo.update_job(job)
            
            logger.info("Repository crawl job completed successfully",
                       job_id=job.job_id,
                       records_processed=job.records_processed,
                       records_created=job.records_created,
                       records_updated=job.records_updated)
            
            return job
            
        except Exception as e:
            # Mark job as failed
            error_msg = str(e)
            job = CrawlJob(
                job_id=job.job_id,
                job_type=job.job_type,
                started_at=job.started_at,
                completed_at=datetime.utcnow(),
                status="failed",
                records_processed=job.records_processed,
                records_created=job.records_created,
                records_updated=job.records_updated,
                last_cursor=job.last_cursor,
                error_message=error_msg,
                metadata=job.metadata
            )
            
            await self.crawl_job_repo.update_job(job)
            
            logger.error("Repository crawl job failed",
                        job_id=job.job_id,
                        error=error_msg)
            
            raise
    
    async def _process_batch(self, repositories: List[Repository], job: CrawlJob) -> tuple[int, int, int]:
        """Process a batch of repositories."""
        logger.info("Processing repository batch", count=len(repositories))
        
        # Get all repository IDs for batch existence check
        repo_ids = [repo.repo_id for repo in repositories]
        existing_ids = await self.repository_repo.get_existing_repo_ids(repo_ids)
        
        # Count created vs updated
        created_count = len(repo_ids) - len(existing_ids)
        updated_count = len(existing_ids)
        
        # Save all repositories (upsert)
        await self.repository_repo.save_batch(repositories)
        
        # Note: Job statistics will be updated in the main execute method
        # (can't modify frozen dataclass here)
        
        logger.info("Batch processed successfully",
                   total=len(repositories),
                   created=created_count,
                   updated=updated_count)
        
        return len(repositories), created_count, updated_count


class ExportDataUseCase:
    """Use case for exporting crawled data."""
    
    def __init__(self, database_repo: DatabaseRepository):
        self.database_repo = database_repo
    
    async def export_repositories_csv(self, file_path: str) -> Dict[str, Any]:
        """Export repositories to CSV format."""
        logger.info("Exporting repositories to CSV", file_path=file_path)
        
        await self.database_repo.export_to_csv("repositories", file_path)
        stats = await self.database_repo.get_table_stats()
        
        logger.info("CSV export completed", file_path=file_path, **stats)
        return stats
    
    async def export_repositories_json(self, file_path: str) -> Dict[str, Any]:
        """Export repositories to JSON format."""
        logger.info("Exporting repositories to JSON", file_path=file_path)
        
        await self.database_repo.export_to_json("repositories", file_path)
        stats = await self.database_repo.get_table_stats()
        
        logger.info("JSON export completed", file_path=file_path, **stats)
        return stats
    
    async def get_crawl_summary(self) -> Dict[str, Any]:
        """Get summary of all crawl activities."""
        stats = await self.database_repo.get_table_stats()
        
        # Add more detailed statistics
        summary = {
            "database_stats": stats,
            "export_timestamp": datetime.utcnow().isoformat()
        }
        
        return summary


class SetupDatabaseUseCase:
    """Use case for setting up database schema."""
    
    def __init__(self, database_repo: DatabaseRepository):
        self.database_repo = database_repo
    
    async def execute(self, migration_files: List[str]) -> None:
        """Execute database migrations."""
        logger.info("Setting up database schema", migrations=len(migration_files))
        
        for migration_file in migration_files:
            logger.info("Executing migration", file=migration_file)
            
            # Read migration file
            with open(migration_file, 'r', encoding='utf-8') as f:
                migration_sql = f.read()
            
            # Execute migration
            await self.database_repo.execute_migration(migration_sql)
            
            logger.info("Migration completed", file=migration_file)
        
        logger.info("Database setup completed successfully")


class HealthCheckUseCase:
    """Use case for health checks and monitoring."""
    
    def __init__(
        self,
        database_repo: DatabaseRepository,
        crawl_job_repo: CrawlJobRepository,
        rate_limit_repo: RateLimitRepository
    ):
        self.database_repo = database_repo
        self.crawl_job_repo = crawl_job_repo
        self.rate_limit_repo = rate_limit_repo
    
    async def get_system_health(self) -> Dict[str, Any]:
        """Get comprehensive system health status."""
        logger.info("Performing system health check")
        
        health_status = {
            "timestamp": datetime.utcnow().isoformat(),
            "status": "healthy",
            "components": {}
        }
        
        try:
            # Database health
            db_stats = await self.database_repo.get_table_stats()
            health_status["components"]["database"] = {
                "status": "healthy",
                "stats": db_stats
            }
        except Exception as e:
            health_status["components"]["database"] = {
                "status": "unhealthy",
                "error": str(e)
            }
            health_status["status"] = "degraded"
        
        try:
            # Crawl jobs status
            active_jobs = await self.crawl_job_repo.get_active_jobs()
            latest_job = await self.crawl_job_repo.get_latest_job("repositories")
            
            health_status["components"]["crawl_jobs"] = {
                "status": "healthy",
                "active_jobs": len(active_jobs),
                "latest_job_status": latest_job.status if latest_job else "none",
                "latest_job_time": latest_job.started_at.isoformat() if latest_job else None
            }
        except Exception as e:
            health_status["components"]["crawl_jobs"] = {
                "status": "unhealthy",
                "error": str(e)
            }
            health_status["status"] = "degraded"
        
        try:
            # Rate limit status
            rate_limit = await self.rate_limit_repo.get_latest_rate_limit_status(
                "graphql", "primary"
            )
            
            health_status["components"]["rate_limits"] = {
                "status": "healthy",
                "remaining": rate_limit.remaining if rate_limit else "unknown",
                "reset_at": rate_limit.reset_at.isoformat() if rate_limit else "unknown"
            }
        except Exception as e:
            health_status["components"]["rate_limits"] = {
                "status": "unhealthy", 
                "error": str(e)
            }
        
        logger.info("System health check completed", status=health_status["status"])
        return health_status
