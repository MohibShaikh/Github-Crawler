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
                # Check if errors are due to access restrictions on specific repositories
                access_restricted_errors = []
                other_errors = []
                
                for error in data["errors"]:
                    error_msg = error.get("message", "")
                    if any(keyword in error_msg.lower() for keyword in [
                        "ip allow list", "not permitted", "access this resource", 
                        "saml_failure", "organization has restricted access"
                    ]):
                        access_restricted_errors.append(error)
                    else:
                        other_errors.append(error)
                
                # If all errors are access restrictions, we can continue with partial data
                if access_restricted_errors and not other_errors:
                    logger.warning("Access restricted repositories found, continuing with available data", 
                                 restricted_count=len(access_restricted_errors))
                    # Return partial data without the restricted repositories
                    return data
                
                # If there are other errors, raise exception
                if other_errors:
                    error_msg = "; ".join([error.get("message", "") for error in other_errors])
                    logger.error("GraphQL errors", errors=other_errors)
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
        self.batch_size = min(batch_size, 100)  # GitHub max is 100 for search API
        
        # Ultra-fast search queries - focus on getting maximum unique repos quickly
        self.search_queries = [
            # High-star repos first (most valuable)
            "is:public stars:>1000",
            "is:public stars:500..999",
            "is:public stars:100..499",
            "is:public stars:50..99",
            "is:public stars:10..49",
            "is:public stars:1..9",
            
            # More granular star ranges for better coverage
            "is:public stars:>10000",
            "is:public stars:7000..7999",
            "is:public stars:6000..6999",
            "is:public stars:5000..5999",
            "is:public stars:2000..4999",
            "is:public stars:1000..1999",
            "is:public stars:750..999",
            "is:public stars:600..749",
            "is:public stars:400..599",
            "is:public stars:300..399",
            "is:public stars:200..299",
            "is:public stars:150..199",
            "is:public stars:75..99",
            "is:public stars:25..49",
            "is:public stars:15..24",
            "is:public stars:5..14",
            "is:public stars:2..4",
            "is:public stars:1",
            
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
            
            # More language-specific queries
            "is:public language:javascript stars:50..99",
            "is:public language:python stars:50..99",
            "is:public language:java stars:50..99",
            "is:public language:go stars:50..99",
            "is:public language:rust stars:50..99",
            "is:public language:typescript stars:50..99",
            "is:public language:php stars:50..99",
            "is:public language:ruby stars:50..99",
            "is:public language:cpp stars:50..99",
            "is:public language:c stars:50..99",
            "is:public language:csharp stars:50..99",
            "is:public language:swift stars:50..99",
            "is:public language:kotlin stars:50..99",
            
            # Recent repositories
            "is:public created:>2024-01-01 stars:>10",
            "is:public created:>2023-01-01 stars:>10",
            "is:public created:>2022-01-01 stars:>10",
            "is:public created:>2021-01-01 stars:>10",
            "is:public created:>2020-01-01 stars:>10",
            "is:public topic:react stars:>5",
            "is:public topic:vue stars:>5",
            "is:public topic:angular stars:>5",
            "is:public topic:nodejs stars:>5",
            "is:public topic:django stars:>5",
            "is:public topic:flask stars:>5",
            "is:public topic:express stars:>5",
            "is:public topic:spring stars:>5",
            "is:public topic:dotnet stars:>5",
            "is:public topic:laravel stars:>5",
            "is:public topic:wordpress stars:>5",
            "is:public topic:tensorflow stars:>5",
            "is:public topic:pytorch stars:>5",
            "is:public topic:opencv stars:>5",
            "is:public topic:fastapi stars:>5",
            
            # More recent date ranges
            "is:public created:2024-01-01..2024-12-31 stars:>5",
            "is:public created:2023-01-01..2023-12-31 stars:>5",
            "is:public created:2022-01-01..2022-12-31 stars:>5",
            "is:public created:2021-01-01..2021-12-31 stars:>5",
            "is:public created:2020-01-01..2020-12-31 stars:>5",
            
            # Active repositories
            "is:public pushed:>2024-01-01 stars:>10",
            "is:public pushed:>2023-01-01 stars:>10",
            "is:public pushed:>2022-01-01 stars:>10",
            "is:public pushed:>2021-01-01 stars:>10",
            "is:public pushed:>2020-01-01 stars:>10",
            
            # More push date ranges
            "is:public pushed:2024-01-01..2024-12-31 stars:>5",
            "is:public pushed:2023-01-01..2023-12-31 stars:>5",
            "is:public pushed:2022-01-01..2022-12-31 stars:>5",
            "is:public pushed:2021-01-01..2021-12-31 stars:>5",
            "is:public pushed:2020-01-01..2020-12-31 stars:>5",
            
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
            
            # More topics
            "is:public topic:react stars:>5",
            "is:public topic:vue stars:>5",
            "is:public topic:angular stars:>5",
            "is:public topic:nodejs stars:>5",
            "is:public topic:docker stars:>5",
            "is:public topic:kubernetes stars:>5",
            "is:public topic:aws stars:>5",
            "is:public topic:azure stars:>5",
            "is:public topic:gcp stars:>5",
            "is:public topic:blockchain stars:>5",
            "is:public topic:cryptocurrency stars:>5",
            "is:public topic:game stars:>5",
            "is:public topic:mobile stars:>5",
            "is:public topic:ios stars:>5",
            "is:public topic:android stars:>5",
            "is:public topic:linux stars:>5",
            "is:public topic:windows stars:>5",
            "is:public topic:macos stars:>5",
            
            # Large repositories
            "is:public size:>1000 stars:>1",
            "is:public size:100..999 stars:>1",
            "is:public size:50..99 stars:>1",
            "is:public size:10..49 stars:>1",
            
            # Forked repositories
            "is:public forks:>100 stars:>1",
            "is:public forks:10..99 stars:>1",
            "is:public forks:1..9 stars:>1",
            
            # Repository types
            "is:public is:template stars:>1",
            "is:public is:mirror stars:>1",
            "is:public archived:false stars:>1",
            
            # License-based searches
            "is:public license:mit stars:>5",
            "is:public license:apache-2.0 stars:>5",
            "is:public license:gpl-3.0 stars:>5",
            "is:public license:bsd-3-clause stars:>5",
            "is:public license:isc stars:>5",
            
            # User-based searches (popular developers)
            "is:public user:facebook stars:>1",
            "is:public user:google stars:>1",
            "is:public user:microsoft stars:>1",
            "is:public user:apple stars:>1",
            "is:public user:netflix stars:>1",
            "is:public user:uber stars:>1",
            "is:public user:airbnb stars:>1",
            "is:public user:spotify stars:>1",
            "is:public user:twitter stars:>1",
            "is:public user:github stars:>1",
            
            # Organization-based searches
            "is:public org:google stars:>1",
            "is:public org:microsoft stars:>1",
            "is:public org:facebook stars:>1",
            "is:public org:apple stars:>1",
            "is:public org:netflix stars:>1",
            "is:public org:airbnb stars:>1",
            "is:public org:spotify stars:>1",
            "is:public org:alibaba stars:>1",
            "is:public org:tencent stars:>1",
            "is:public org:baidu stars:>1",
            "is:public org:oracle stars:>1",
            "is:public org:redhat stars:>1",
            "is:public org:jetbrains stars:>1",
            "is:public org:apache stars:>1",
            "is:public org:mozilla stars:>1",
            
            # Description-based searches
            "is:public \"awesome\" stars:>5",
            "is:public \"starter\" stars:>5",
            "is:public \"template\" stars:>5",
            "is:public \"boilerplate\" stars:>5",
            "is:public \"example\" stars:>5",
            "is:public \"demo\" stars:>5",
            "is:public \"tutorial\" stars:>5",
            "is:public \"guide\" stars:>5",
            
            # Fallback: any public repo with stars
            "is:public stars:>0"
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
                           end_cursor=page_info["endCursor"],
                           query=self.search_queries[self.current_query_index],
                           query_index=self.current_query_index)
                
                # Convert to domain entities and filter for repositories with stars
                repositories = []
                for repo_data in repositories_data:
                    try:
                        # Skip null repositories (access restricted)
                        if repo_data is None:
                            continue
                            
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
                                     repo_name=repo_data.get("nameWithOwner", "unknown") if repo_data else "null",
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
                    if self.consecutive_empty_batches >= 5:  # Less aggressive skipping - wait longer
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
                            
                            # Fallback: if we haven't reached target, try broader queries
                            if total_fetched < target_count:  # Keep going until we hit full target
                                logger.warning("Didn't reach target count, trying broader fallback queries")
                                # Add some very broad fallback queries
                                fallback_queries = [
                                    "is:public stars:>0",
                                    "is:public created:>2010-01-01",
                                    "is:public pushed:>2020-01-01",
                                    "is:public language:javascript",
                                    "is:public language:python",
                                    "is:public language:java",
                                    "is:public language:go",
                                    "is:public language:rust"
                                ]
                                self.search_queries.extend(fallback_queries)
                                self.current_query_index = len(self.search_queries) - len(fallback_queries)
                                cursor = None
                                continue
                            else:
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
                # Check if it's an access restriction error
                if any(keyword in str(e).lower() for keyword in [
                    "ip allow list", "not permitted", "access this resource", 
                    "saml_failure", "organization has restricted access"
                ]):
                    logger.warning("Access restriction encountered, skipping to next query", 
                                 error=str(e),
                                 query=self.search_queries[self.current_query_index])
                    # Move to next query
                    self.current_query_index += 1
                    if self.current_query_index < len(self.search_queries):
                        cursor = None  # Reset cursor for new query
                        continue
                    else:
                        logger.error("All queries exhausted due to access restrictions", 
                                   total_fetched=total_fetched)
                        break
                else:
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