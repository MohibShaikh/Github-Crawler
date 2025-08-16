"""GitHub GraphQL API client with rate limiting and retry logic."""

import asyncio
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, AsyncIterator, Tuple
import json

import aiohttp
import structlog
from tenacity import (
    retry, stop_after_attempt, wait_exponential,
    retry_if_exception_type
)

from ..domain.entities import Repository, RateLimitInfo, RepositoryOwnerType

logger = structlog.get_logger()


class GitHubRateLimitExceeded(Exception):
    """Raised when GitHub rate limit is exceeded."""
    pass


class GitHubAPIError(Exception):
    """Raised when GitHub API returns an error."""
    pass


class GitHubGraphQLClient:
    """GitHub GraphQL API client with advanced rate limiting."""
    
    GRAPHQL_ENDPOINT = "https://api.github.com/graphql"
    
    def __init__(self, token: str, session: Optional[aiohttp.ClientSession] = None):
        self.token = token
        self.session = session
        self._should_close_session = session is None
        self._rate_limit_info: Optional[RateLimitInfo] = None
        self._secondary_rate_limit_delay = 0
        
    async def __aenter__(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=60),
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "GitHub-Star-Crawler/1.0"
                }
            )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._should_close_session and self.session:
            await self.session.close()
    
    @property
    def rate_limit_info(self) -> Optional[RateLimitInfo]:
        """Get current rate limit information."""
        return self._rate_limit_info
    
    @retry(
        stop=stop_after_attempt(5),
        wait=wait_exponential(multiplier=1, min=4, max=60),
        retry=retry_if_exception_type((aiohttp.ClientError, asyncio.TimeoutError))
    )
    async def execute_query(self, query: str, variables: Optional[Dict] = None) -> Dict[str, Any]:
        """Execute a GraphQL query with retry logic."""
        if not self.session:
            raise RuntimeError("Session not initialized. Use async context manager.")
        
        # Respect secondary rate limits
        if self._secondary_rate_limit_delay > 0:
            logger.info("Waiting for secondary rate limit", delay=self._secondary_rate_limit_delay)
            await asyncio.sleep(self._secondary_rate_limit_delay)
            self._secondary_rate_limit_delay = 0
        
        payload = {"query": query}
        if variables:
            payload["variables"] = variables
        
        async with self.session.post(self.GRAPHQL_ENDPOINT, json=payload) as response:
            # Update rate limit info from headers
            self._update_rate_limit_from_headers(response.headers)
            
            if response.status == 429:
                # Rate limit exceeded
                reset_time = datetime.fromtimestamp(
                    int(response.headers.get("X-RateLimit-Reset", 0))
                )
                wait_time = (reset_time - datetime.utcnow()).total_seconds()
                logger.warning("Primary rate limit exceeded", wait_time=wait_time)
                raise GitHubRateLimitExceeded(f"Rate limit exceeded. Reset at {reset_time}")
            
            if response.status == 403:
                # Check for secondary rate limit
                if "secondary rate limit" in (await response.text()).lower():
                    self._secondary_rate_limit_delay = min(60, self._secondary_rate_limit_delay + 10)
                    logger.warning("Secondary rate limit hit", delay=self._secondary_rate_limit_delay)
                    raise aiohttp.ClientError("Secondary rate limit exceeded")
            
            response.raise_for_status()
            data = await response.json()
            
            if "errors" in data:
                error_msg = "; ".join([error.get("message", "") for error in data["errors"]])
                logger.error("GraphQL errors", errors=data["errors"])
                raise GitHubAPIError(f"GraphQL errors: {error_msg}")
            
            return data
    
    def _update_rate_limit_from_headers(self, headers) -> None:
        """Update rate limit info from response headers."""
        try:
            limit = int(headers.get("X-RateLimit-Limit", 5000))
            remaining = int(headers.get("X-RateLimit-Remaining", 0))
            reset_timestamp = int(headers.get("X-RateLimit-Reset", 0))
            used = int(headers.get("X-RateLimit-Used", 0))
            
            reset_at = datetime.fromtimestamp(reset_timestamp)
            
            self._rate_limit_info = RateLimitInfo(
                limit=limit,
                remaining=remaining,
                reset_at=reset_at,
                used=used
            )
            
            logger.debug("Rate limit updated", 
                        remaining=remaining, limit=limit, reset_at=reset_at.isoformat())
            
        except (ValueError, TypeError) as e:
            logger.warning("Failed to parse rate limit headers", error=str(e))
    
    async def should_wait_for_rate_limit(self) -> bool:
        """Check if we should wait for rate limit reset."""
        if not self._rate_limit_info:
            return False
        
        # Only wait if almost no requests remaining
        if self._rate_limit_info.remaining < 3:
            wait_time = (self._rate_limit_info.reset_at - datetime.utcnow()).total_seconds()
            if wait_time > 0:
                logger.info("Waiting for rate limit reset", 
                           remaining=self._rate_limit_info.remaining,
                           wait_time=wait_time)
                await asyncio.sleep(wait_time + 0.5)  # Reduced buffer for speed
                return True
        
        return False


class GitHubRepositoryCrawler:
    """High-performance GitHub repository crawler."""
    
    # GraphQL query to fetch repositories with star counts
    REPOSITORIES_QUERY = """
    query($cursor: String, $first: Int!, $query: String!) {
      search(query: $query, type: REPOSITORY, first: $first, after: $cursor) {
        repositoryCount
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          ... on Repository {
            id
            databaseId
            name
            nameWithOwner
            owner {
              login
              __typename
            }
            stargazerCount
            forkCount
            watchers {
              totalCount
            }
            primaryLanguage {
              name
            }
            description
            isPrivate
            isFork
            isArchived
            isDisabled
            createdAt
            updatedAt
            pushedAt
          }
        }
      }
      rateLimit {
        limit
        remaining
        resetAt
        used
      }
    }
    """
    
    def __init__(self, client: GitHubGraphQLClient, batch_size: int = 100):
        self.client = client
        self.batch_size = min(batch_size, 100)  # GitHub max is 100
        
        # Ultra-fast search queries - focus on getting maximum unique repos quickly
        self.search_queries = [
            # High-star repos first (most valuable)
            "is:public stars:>1000",
            "is:public stars:500..999",
            "is:public stars:100..499",
            "is:public stars:50..99",
            "is:public stars:10..49",
            "is:public stars:1..9",
            # Popular languages with star ranges
            "is:public language:javascript stars:100..999",
            "is:public language:python stars:100..999",
            "is:public language:java stars:100..999",
            "is:public language:go stars:100..999",
            "is:public language:rust stars:100..999",
            "is:public language:typescript stars:100..999",
            "is:public language:php stars:100..999",
            "is:public language:ruby stars:100..999",
            "is:public language:cpp stars:100..999",
            "is:public language:c stars:100..999",
            "is:public language:csharp stars:100..999",
            "is:public language:swift stars:100..999",
            "is:public language:kotlin stars:100..999",
            # Recent repositories
            "is:public created:>2024-01-01 stars:>10",
            "is:public created:>2023-01-01 stars:>10",
            "is:public created:>2022-01-01 stars:>10",
            "is:public created:>2021-01-01 stars:>10",
            "is:public created:>2020-01-01 stars:>10",
            # Active repositories
            "is:public pushed:>2024-01-01 stars:>10",
            "is:public pushed:>2023-01-01 stars:>10",
            "is:public pushed:>2022-01-01 stars:>10",
            "is:public pushed:>2021-01-01 stars:>10",
            "is:public pushed:>2020-01-01 stars:>10",
            # Popular topics
            "is:public topic:web stars:>10",
            "is:public topic:api stars:>10",
            "is:public topic:framework stars:>10",
            "is:public topic:library stars:>10",
            "is:public topic:tool stars:>10",
            "is:public topic:cli stars:>10",
            "is:public topic:database stars:>10",
            "is:public topic:security stars:>10",
            "is:public topic:ai stars:>10",
            "is:public topic:machine-learning stars:>10",
            # Large repositories
            "is:public size:>1000 stars:>1",
            "is:public size:100..999 stars:>1",
            # Forked repositories
            "is:public forks:>100 stars:>1",
            "is:public forks:10..99 stars:>1"
        ]
        self.current_query_index = 0
        self.seen_repo_ids = set()  # Track seen repository IDs to avoid duplicates
        self.consecutive_empty_batches = 0  # Track consecutive empty batches
        self.max_concurrent_requests = 5  # Maximum concurrent requests for speed
        
    async def crawl_repositories(
        self, 
        target_count: int = 100000,
        start_cursor: Optional[str] = None
    ) -> AsyncIterator[Tuple[List[Repository], str]]:
        """
        Crawl GitHub repositories with concurrent requests for maximum speed.
        
        Args:
            target_count: Target number of repositories to crawl
            start_cursor: Cursor to resume from (for continued crawling)
            
        Yields:
            Tuple of (repositories_batch, next_cursor)
        """
        cursor = start_cursor
        total_fetched = 0
        
        logger.info("Starting concurrent repository crawl", 
                   target_count=target_count, 
                   batch_size=self.batch_size,
                   max_concurrent=self.max_concurrent_requests,
                   start_cursor=cursor)
        
        # Use semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(self.max_concurrent_requests)
        
        while total_fetched < target_count:
            # Ultra-aggressive rate limit checking - only wait if critical
            rate_info = self.client.rate_limit_info
            if rate_info and rate_info.remaining < 5:
                # Only wait if very few requests remaining
                await self.client.should_wait_for_rate_limit()
            
            # Use maximum batch size for speed
            current_batch_size = min(self.batch_size, target_count - total_fetched)
            
            try:
                # Execute GraphQL query with semaphore for concurrency control
                async with semaphore:
                    variables = {
                        "first": current_batch_size,
                        "cursor": cursor,
                        "query": self.search_queries[self.current_query_index]
                    }
                    
                    logger.debug("Executing repositories query", 
                               batch_size=current_batch_size,
                               cursor=cursor,
                               fetched=total_fetched)
                    
                    response = await self.client.execute_query(
                        self.REPOSITORIES_QUERY, 
                        variables
                    )
                
                # Parse response
                search_data = response["data"]["search"]
                page_info = search_data["pageInfo"]
                repositories_data = search_data["nodes"]
                repository_count = search_data.get("repositoryCount", 0)
                
                logger.info("Search response", 
                           repository_count=repository_count,
                           nodes_count=len(repositories_data),
                           has_next_page=page_info["hasNextPage"],
                           end_cursor=page_info["endCursor"])
                
                # Convert to domain entities and filter for repositories with stars
                repositories = []
                for repo_data in repositories_data:
                    try:
                        repo_id = repo_data.get("databaseId")
                        
                        # Skip if we've already seen this repository
                        if repo_id in self.seen_repo_ids:
                            continue
                            
                        # Include all repositories with stars (fast filtering)
                        if repo_data.get("stargazerCount", 0) > 0:  # Quick star check
                            repository = self._convert_to_repository(repo_data)
                            repositories.append(repository)
                            self.seen_repo_ids.add(repo_id)
                    except Exception as e:
                        logger.warning("Failed to convert repository", 
                                     repo_name=repo_data.get("nameWithOwner", "unknown"),
                                     error=str(e))
                        continue
                
                # Debug: Log if we got no new repositories
                if len(repositories) == 0:
                    self.consecutive_empty_batches += 1
                    logger.warning("No new repositories found in this batch", 
                                 query=self.search_queries[self.current_query_index],
                                 total_nodes=len(repositories_data),
                                 seen_repos=len(self.seen_repo_ids),
                                 consecutive_empty=self.consecutive_empty_batches)
                    
                    # Skip to next query if we've had too many consecutive empty batches
                    if self.consecutive_empty_batches >= 3:  # More aggressive skipping
                        logger.info("Too many consecutive empty batches, skipping to next query")
                        self.current_query_index += 1
                        self.consecutive_empty_batches = 0
                        if self.current_query_index < len(self.search_queries):
                            cursor = None  # Reset cursor for new query
                            logger.info("Switching to next search query", 
                                       query=self.search_queries[self.current_query_index],
                                       query_index=self.current_query_index)
                            continue
                        else:
                            logger.info("All search queries exhausted", 
                                       total_queries_used=len(self.search_queries),
                                       total_fetched=total_fetched)
                            break
                else:
                    # Reset counter when we get new repositories
                    self.consecutive_empty_batches = 0
                
                # Update counters
                total_fetched += len(repositories)
                cursor = page_info["endCursor"]
                
                logger.info("Fetched repository batch", 
                           batch_size=len(repositories),
                           total_fetched=total_fetched,
                           target_count=target_count,
                           has_next_page=page_info["hasNextPage"])
                
                # Yield batch and cursor
                yield repositories, cursor
                
                # Check if we should continue
                if not page_info["hasNextPage"]:
                    logger.info("Reached end of current search query", 
                               query=self.search_queries[self.current_query_index],
                               query_index=self.current_query_index,
                               total_queries=len(self.search_queries))
                    
                    # Try next search query
                    self.current_query_index += 1
                    if self.current_query_index < len(self.search_queries):
                        cursor = None  # Reset cursor for new query
                        logger.info("Switching to next search query", 
                                   query=self.search_queries[self.current_query_index],
                                   query_index=self.current_query_index)
                        continue
                    else:
                        logger.info("All search queries exhausted", 
                                   total_queries_used=len(self.search_queries),
                                   total_fetched=total_fetched)
                        break
                    
                # No delay for maximum speed
                # await asyncio.sleep(0.1)
                
            except GitHubRateLimitExceeded as e:
                logger.warning("Rate limit exceeded, waiting", error=str(e))
                # Wait for rate limit reset
                if self.client.rate_limit_info:
                    wait_time = (
                        self.client.rate_limit_info.reset_at - datetime.utcnow()
                    ).total_seconds()
                    if wait_time > 0:
                        await asyncio.sleep(wait_time + 1)
                continue
                
            except Exception as e:
                logger.error("Error during repository crawl", 
                           error=str(e), 
                           cursor=cursor,
                           total_fetched=total_fetched)
                raise
        
        logger.info("Repository crawl completed", 
                   total_fetched=total_fetched,
                   target_count=target_count)
    
    def _convert_to_repository(self, repo_data: Dict[str, Any]) -> Repository:
        """Convert GitHub API response to Repository entity."""
        # Parse owner type
        owner_type = RepositoryOwnerType.ORGANIZATION
        if repo_data["owner"]["__typename"] == "User":
            owner_type = RepositoryOwnerType.USER
        
        # Parse dates
        created_at = datetime.fromisoformat(
            repo_data["createdAt"].replace("Z", "+00:00")
        ) if repo_data.get("createdAt") else None
        
        updated_at = datetime.fromisoformat(
            repo_data["updatedAt"].replace("Z", "+00:00")
        ) if repo_data.get("updatedAt") else None
        
        pushed_at = datetime.fromisoformat(
            repo_data["pushedAt"].replace("Z", "+00:00")
        ) if repo_data.get("pushedAt") else None
        
        # Extract language
        language = None
        if repo_data.get("primaryLanguage"):
            language = repo_data["primaryLanguage"]["name"]
        
        # Get watcher count
        watchers_count = 0
        if repo_data.get("watchers"):
            watchers_count = repo_data["watchers"]["totalCount"]
        
        return Repository(
            repo_id=repo_data["databaseId"],
            name=repo_data["name"],
            full_name=repo_data["nameWithOwner"],
            owner_login=repo_data["owner"]["login"],
            owner_type=owner_type,
            stars_count=repo_data["stargazerCount"],
            forks_count=repo_data.get("forkCount", 0),
            watchers_count=watchers_count,
            language=language,
            description=repo_data.get("description"),
            is_private=repo_data.get("isPrivate", False),
            is_fork=repo_data.get("isFork", False),
            is_archived=repo_data.get("isArchived", False),
            is_disabled=repo_data.get("isDisabled", False),
            created_at=created_at,
            updated_at=updated_at,
            pushed_at=pushed_at,
            crawled_at=datetime.utcnow()
        )


class AntiCorruptionLayer:
    """
    Anti-corruption layer to translate between GitHub API models and domain models.
    Protects domain from external API changes.
    """
    
    @staticmethod
    def github_repo_to_domain(github_data: Dict[str, Any]) -> Repository:
        """Convert GitHub API repository data to domain Repository."""
        try:
            # Map GitHub field names to domain fields
            domain_data = {
                "repo_id": github_data.get("databaseId") or github_data.get("id"),
                "name": github_data.get("name", ""),
                "full_name": github_data.get("nameWithOwner", github_data.get("full_name", "")),
                "owner_login": github_data.get("owner", {}).get("login", ""),
                "stars_count": github_data.get("stargazerCount", github_data.get("stargazers_count", 0)),
                "forks_count": github_data.get("forkCount", github_data.get("forks_count", 0)),
                "watchers_count": github_data.get("watchers", {}).get("totalCount", 
                                                 github_data.get("watchers_count", 0)),
                "language": github_data.get("primaryLanguage", {}).get("name") if 
                           github_data.get("primaryLanguage") else github_data.get("language"),
                "description": github_data.get("description"),
                "is_private": github_data.get("isPrivate", github_data.get("private", False)),
                "is_fork": github_data.get("isFork", github_data.get("fork", False)),
                "is_archived": github_data.get("isArchived", github_data.get("archived", False)),
                "is_disabled": github_data.get("isDisabled", github_data.get("disabled", False))
            }
            
            # Handle owner type
            owner_type = RepositoryOwnerType.USER
            if github_data.get("owner", {}).get("__typename") == "Organization":
                owner_type = RepositoryOwnerType.ORGANIZATION
            elif github_data.get("owner", {}).get("type") == "Organization":
                owner_type = RepositoryOwnerType.ORGANIZATION
            domain_data["owner_type"] = owner_type
            
            # Handle dates with multiple possible formats
            date_fields = ["createdAt", "updatedAt", "pushedAt"]
            alt_date_fields = ["created_at", "updated_at", "pushed_at"]
            
            for domain_field, github_field, alt_field in zip(
                ["created_at", "updated_at", "pushed_at"], 
                date_fields, 
                alt_date_fields
            ):
                date_str = github_data.get(github_field) or github_data.get(alt_field)
                if date_str:
                    try:
                        # Handle ISO format with Z
                        if date_str.endswith("Z"):
                            date_str = date_str.replace("Z", "+00:00")
                        domain_data[domain_field] = datetime.fromisoformat(date_str)
                    except ValueError:
                        logger.warning(f"Failed to parse date field {domain_field}", value=date_str)
                        domain_data[domain_field] = None
                else:
                    domain_data[domain_field] = None
            
            domain_data["crawled_at"] = datetime.utcnow()
            
            return Repository(**domain_data)
            
        except Exception as e:
            logger.error("Failed to convert GitHub data to domain model", 
                        error=str(e), github_data=github_data)
            raise
    
    @staticmethod
    def validate_github_response(response: Dict[str, Any]) -> bool:
        """Validate GitHub API response structure."""
        if "errors" in response:
            return False
        
        if "data" not in response:
            return False
        
        # Check for required nested structure
        search_data = response.get("data", {}).get("search")
        if not search_data:
            return False
        
        if "nodes" not in search_data or "pageInfo" not in search_data:
            return False
        
        return True
