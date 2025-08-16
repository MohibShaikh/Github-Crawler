"""Repository interfaces defining data access contracts."""

from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any, Set
from datetime import datetime

from .entities import (
    Repository, Issue, PullRequest, Comment, CICheck, 
    CrawlJob, RateLimitInfo
)


class RepositoryRepository(ABC):
    """Repository interface for repository data access."""

    @abstractmethod
    async def get_by_repo_id(self, repo_id: int) -> Optional[Repository]:
        """Get repository by GitHub repo ID."""
        pass

    @abstractmethod
    async def get_existing_repo_ids(self, repo_ids: List[int]) -> Set[int]:
        """Get set of existing repository IDs from a list of IDs."""
        pass

    @abstractmethod
    async def get_by_owner_and_name(self, owner: str, name: str) -> Optional[Repository]:
        """Get repository by owner and name."""
        pass

    @abstractmethod
    async def save(self, repository: Repository) -> Repository:
        """Save repository (insert or update)."""
        pass

    @abstractmethod
    async def save_batch(self, repositories: List[Repository]) -> List[Repository]:
        """Save multiple repositories efficiently."""
        pass

    @abstractmethod
    async def get_all_paginated(
        self, 
        offset: int = 0, 
        limit: int = 1000,
        updated_since: Optional[datetime] = None
    ) -> List[Repository]:
        """Get repositories with pagination and optional filtering."""
        pass

    @abstractmethod
    async def count(self, updated_since: Optional[datetime] = None) -> int:
        """Count repositories with optional filtering."""
        pass

    @abstractmethod
    async def get_stale_repositories(
        self, 
        older_than: datetime,
        limit: int = 1000
    ) -> List[Repository]:
        """Get repositories that haven't been updated recently."""
        pass


class IssueRepository(ABC):
    """Repository interface for issue data access."""

    @abstractmethod
    async def get_by_issue_id(self, issue_id: int) -> Optional[Issue]:
        """Get issue by GitHub issue ID."""
        pass

    @abstractmethod
    async def get_by_repo_id(
        self, 
        repo_id: int,
        offset: int = 0,
        limit: int = 1000
    ) -> List[Issue]:
        """Get issues for a repository."""
        pass

    @abstractmethod
    async def save_batch(self, issues: List[Issue]) -> List[Issue]:
        """Save multiple issues efficiently."""
        pass


class PullRequestRepository(ABC):
    """Repository interface for pull request data access."""

    @abstractmethod
    async def get_by_pr_id(self, pr_id: int) -> Optional[PullRequest]:
        """Get pull request by GitHub PR ID."""
        pass

    @abstractmethod
    async def get_by_repo_id(
        self, 
        repo_id: int,
        offset: int = 0,
        limit: int = 1000
    ) -> List[PullRequest]:
        """Get pull requests for a repository."""
        pass

    @abstractmethod
    async def save_batch(self, pull_requests: List[PullRequest]) -> List[PullRequest]:
        """Save multiple pull requests efficiently."""
        pass


class CommentRepository(ABC):
    """Repository interface for comment data access."""

    @abstractmethod
    async def get_by_comment_id(self, comment_id: int) -> Optional[Comment]:
        """Get comment by GitHub comment ID."""
        pass

    @abstractmethod
    async def get_by_issue_id(
        self, 
        issue_id: int,
        offset: int = 0,
        limit: int = 1000
    ) -> List[Comment]:
        """Get comments for an issue."""
        pass

    @abstractmethod
    async def get_by_pr_id(
        self, 
        pr_id: int,
        offset: int = 0,
        limit: int = 1000
    ) -> List[Comment]:
        """Get comments for a pull request."""
        pass

    @abstractmethod
    async def save_batch(self, comments: List[Comment]) -> List[Comment]:
        """Save multiple comments efficiently."""
        pass


class CICheckRepository(ABC):
    """Repository interface for CI check data access."""

    @abstractmethod
    async def get_by_check_id(self, check_id: int) -> Optional[CICheck]:
        """Get CI check by GitHub check ID."""
        pass

    @abstractmethod
    async def get_by_pr_id(
        self, 
        pr_id: int,
        offset: int = 0,
        limit: int = 1000
    ) -> List[CICheck]:
        """Get CI checks for a pull request."""
        pass

    @abstractmethod
    async def save_batch(self, checks: List[CICheck]) -> List[CICheck]:
        """Save multiple CI checks efficiently."""
        pass


class CrawlJobRepository(ABC):
    """Repository interface for crawl job tracking."""

    @abstractmethod
    async def create_job(self, job: CrawlJob) -> CrawlJob:
        """Create a new crawl job."""
        pass

    @abstractmethod
    async def update_job(self, job: CrawlJob) -> CrawlJob:
        """Update an existing crawl job."""
        pass

    @abstractmethod
    async def get_job(self, job_id: int) -> Optional[CrawlJob]:
        """Get crawl job by ID."""
        pass

    @abstractmethod
    async def get_latest_job(self, job_type: str) -> Optional[CrawlJob]:
        """Get the latest job of a specific type."""
        pass

    @abstractmethod
    async def get_active_jobs(self) -> List[CrawlJob]:
        """Get all currently running jobs."""
        pass


class RateLimitRepository(ABC):
    """Repository interface for rate limit tracking."""

    @abstractmethod
    async def save_rate_limit_status(
        self, 
        api_type: str, 
        limit_type: str, 
        rate_limit: RateLimitInfo
    ) -> None:
        """Save current rate limit status."""
        pass

    @abstractmethod
    async def get_latest_rate_limit_status(
        self, 
        api_type: str, 
        limit_type: str
    ) -> Optional[RateLimitInfo]:
        """Get the most recent rate limit status."""
        pass


class DatabaseRepository(ABC):
    """Repository interface for database operations."""

    @abstractmethod
    async def execute_migration(self, migration_sql: str) -> None:
        """Execute a database migration script."""
        pass

    @abstractmethod
    async def export_to_csv(self, table_name: str, file_path: str) -> None:
        """Export table data to CSV."""
        pass

    @abstractmethod
    async def export_to_json(self, table_name: str, file_path: str) -> None:
        """Export table data to JSON."""
        pass

    @abstractmethod
    async def get_table_stats(self) -> Dict[str, Any]:
        """Get statistics about all tables."""
        pass
