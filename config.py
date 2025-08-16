"""Configuration management for GitHub star crawler."""

import os
from typing import Optional
from dataclasses import dataclass


@dataclass
class CrawlerConfig:
    """Configuration for the GitHub repository crawler."""
    
    # GitHub API settings
    github_token: str
    
    # Database settings
    database_url: str
    database_min_pool_size: int = 10
    database_max_pool_size: int = 20
    
    # Crawler settings
    default_batch_size: int = 10000  # Much larger default batch size for maximum speed
    default_target_count: int = 100000
    max_concurrent_requests: int = 5
    
    # Rate limiting
    respect_rate_limits: bool = True
    rate_limit_buffer: int = 10
    
    # Retry settings
    max_retries: int = 5
    retry_delay_base: int = 2
    retry_delay_max: int = 60
    
    # Logging
    log_level: str = "INFO"
    log_format: str = "json"
    
    @classmethod
    def from_env(cls) -> "CrawlerConfig":
        """Create configuration from environment variables."""
        github_token = os.getenv("GITHUB_TOKEN")
        if not github_token:
            raise ValueError("GITHUB_TOKEN environment variable is required")
        
        return cls(
            github_token=github_token,
            database_url=os.getenv(
                "DATABASE_URL",
                "postgresql://postgres:password@localhost:5432/github_crawler"
            ),
            database_min_pool_size=int(os.getenv("DATABASE_MIN_POOL_SIZE", "10")),
            database_max_pool_size=int(os.getenv("DATABASE_MAX_POOL_SIZE", "20")),
            default_batch_size=int(os.getenv("DEFAULT_BATCH_SIZE", "1000")),
            default_target_count=int(os.getenv("DEFAULT_TARGET_COUNT", "100000")),
            max_concurrent_requests=int(os.getenv("MAX_CONCURRENT_REQUESTS", "5")),
            respect_rate_limits=os.getenv("RESPECT_RATE_LIMITS", "true").lower() == "true",
            rate_limit_buffer=int(os.getenv("RATE_LIMIT_BUFFER", "10")),
            max_retries=int(os.getenv("MAX_RETRIES", "5")),
            retry_delay_base=int(os.getenv("RETRY_DELAY_BASE", "2")),
            retry_delay_max=int(os.getenv("RETRY_DELAY_MAX", "60")),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            log_format=os.getenv("LOG_FORMAT", "json")
        )
