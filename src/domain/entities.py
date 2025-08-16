"""Domain entities representing core business objects."""

from datetime import datetime
from typing import Optional, List
from dataclasses import dataclass
from enum import Enum


class RepositoryOwnerType(Enum):
    """Type of repository owner."""
    USER = "User"
    ORGANIZATION = "Organization"


class IssueState(Enum):
    """State of an issue."""
    OPEN = "open"
    CLOSED = "closed"


class PullRequestState(Enum):
    """State of a pull request."""
    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class CommentType(Enum):
    """Type of comment."""
    ISSUE = "issue"
    PR = "pr"
    REVIEW = "review"


class CheckStatus(Enum):
    """Status of a CI check."""
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class CheckConclusion(Enum):
    """Conclusion of a CI check."""
    SUCCESS = "success"
    FAILURE = "failure"
    NEUTRAL = "neutral"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    ACTION_REQUIRED = "action_required"


@dataclass(frozen=True)
class Repository:
    """Core repository entity."""
    repo_id: int
    name: str
    full_name: str
    owner_login: str
    owner_type: RepositoryOwnerType
    stars_count: int
    forks_count: int = 0
    watchers_count: int = 0
    language: Optional[str] = None
    description: Optional[str] = None
    is_private: bool = False
    is_fork: bool = False
    is_archived: bool = False
    is_disabled: bool = False
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    pushed_at: Optional[datetime] = None
    crawled_at: Optional[datetime] = None

    def with_updated_stars(self, new_stars_count: int) -> 'Repository':
        """Create a new repository instance with updated star count."""
        return Repository(
            repo_id=self.repo_id,
            name=self.name,
            full_name=self.full_name,
            owner_login=self.owner_login,
            owner_type=self.owner_type,
            stars_count=new_stars_count,
            forks_count=self.forks_count,
            watchers_count=self.watchers_count,
            language=self.language,
            description=self.description,
            is_private=self.is_private,
            is_fork=self.is_fork,
            is_archived=self.is_archived,
            is_disabled=self.is_disabled,
            created_at=self.created_at,
            updated_at=self.updated_at,
            pushed_at=self.pushed_at,
            crawled_at=datetime.utcnow()
        )


@dataclass(frozen=True)
class Issue:
    """Issue entity for future extensibility."""
    issue_id: int
    repo_id: int
    number: int
    title: str
    body: Optional[str]
    state: IssueState
    author_login: str
    assignee_logins: List[str]
    label_names: List[str]
    milestone_title: Optional[str]
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    crawled_at: Optional[datetime] = None


@dataclass(frozen=True)
class PullRequest:
    """Pull request entity for future extensibility."""
    pr_id: int
    repo_id: int
    number: int
    title: str
    body: Optional[str]
    state: PullRequestState
    author_login: str
    assignee_logins: List[str]
    reviewer_logins: List[str]
    label_names: List[str]
    milestone_title: Optional[str]
    head_ref: str
    base_ref: str
    mergeable: Optional[bool]
    merged: bool
    merged_by_login: Optional[str]
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime] = None
    merged_at: Optional[datetime] = None
    crawled_at: Optional[datetime] = None


@dataclass(frozen=True)
class Comment:
    """Comment entity for future extensibility."""
    comment_id: int
    repo_id: int
    issue_id: Optional[int]
    pr_id: Optional[int]
    comment_type: CommentType
    author_login: str
    body: str
    created_at: datetime
    updated_at: datetime
    crawled_at: Optional[datetime] = None


@dataclass(frozen=True)
class CICheck:
    """CI check entity for future extensibility."""
    check_id: int
    repo_id: int
    pr_id: Optional[int]
    check_suite_id: Optional[int]
    name: str
    status: CheckStatus
    conclusion: Optional[CheckConclusion]
    head_sha: str
    external_id: Optional[str]
    url: Optional[str]
    created_at: datetime
    updated_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    crawled_at: Optional[datetime] = None


@dataclass(frozen=True)
class RateLimitInfo:
    """Rate limit information."""
    limit: int
    remaining: int
    reset_at: datetime
    used: int

    @property
    def is_exhausted(self) -> bool:
        """Check if rate limit is exhausted."""
        return self.remaining <= 0

    @property
    def usage_percentage(self) -> float:
        """Calculate usage percentage."""
        return (self.used / self.limit) * 100 if self.limit > 0 else 0


@dataclass(frozen=True)
class CrawlJob:
    """Crawl job tracking entity."""
    job_id: Optional[int]
    job_type: str
    started_at: datetime
    completed_at: Optional[datetime]
    status: str
    records_processed: int = 0
    records_created: int = 0
    records_updated: int = 0
    last_cursor: Optional[str] = None
    error_message: Optional[str] = None
    metadata: Optional[dict] = None
