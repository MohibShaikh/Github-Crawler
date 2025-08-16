# Scaling Analysis: From 100K to 500M Repositories

## Overview

This document outlines the architectural changes and considerations required to scale the GitHub repository crawler from handling 100,000 repositories to 500 million repositories - a 5,000x increase in scale.

## Current Architecture (100K Repositories)

### Current Constraints
- **Single Instance**: Runs on a single machine/container
- **Sequential Processing**: Primarily sequential with some batching
- **Single Database**: One PostgreSQL instance
- **Rate Limits**: GitHub's 5,000 requests/hour per token
- **Memory Usage**: ~50MB for 100K repositories
- **Processing Time**: ~2-4 hours for 100K repositories

### Current Performance Metrics
- **Throughput**: ~500-1,000 repos/minute
- **Database Size**: ~100MB for 100K repositories
- **API Calls**: ~1,000 GraphQL requests for 100K repositories
- **Network Bandwidth**: ~10MB/hour

## Scaling to 500M Repositories

### 1. Distributed Architecture

#### Horizontal Scaling Strategy
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Crawler       │    │   Crawler       │    │   Crawler       │
│   Instance 1    │    │   Instance 2    │    │   Instance N    │
│                 │    │                 │    │                 │
│ Token Pool A    │    │ Token Pool B    │    │ Token Pool N    │
└─────────┬───────┘    └─────────┬───────┘    └─────────┬───────┘
          │                      │                      │
          └──────────────────────┼──────────────────────┘
                                 │
                    ┌─────────────▼─────────────┐
                    │     Message Queue         │
                    │    (Redis/RabbitMQ)       │
                    └─────────────┬─────────────┘
                                  │
                    ┌─────────────▼─────────────┐
                    │   Database Cluster        │
                    │  (Sharded PostgreSQL)     │
                    └───────────────────────────┘
```

#### Key Changes Required

**1. Multi-Token Management**
```python
class TokenManager:
    def __init__(self, tokens: List[str]):
        self.tokens = tokens
        self.token_index = 0
        self.rate_limits = {}
    
    async def get_available_token(self) -> str:
        """Get token with available rate limit."""
        for _ in range(len(self.tokens)):
            token = self.tokens[self.token_index]
            self.token_index = (self.token_index + 1) % len(self.tokens)
            
            if not self.is_rate_limited(token):
                return token
        
        # All tokens rate limited, wait for earliest reset
        await self.wait_for_earliest_reset()
        return await self.get_available_token()
```

**2. Distributed Work Coordination**
```python
class DistributedCrawlCoordinator:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.work_queue = "github_crawl_queue"
    
    async def claim_work_batch(self, worker_id: str) -> Optional[CrawlBatch]:
        """Atomically claim a batch of work."""
        batch = await self.redis.lpop(self.work_queue)
        if batch:
            await self.redis.hset(
                "active_batches", 
                worker_id, 
                json.dumps({
                    "batch": batch,
                    "claimed_at": datetime.utcnow().isoformat(),
                    "worker_id": worker_id
                })
            )
        return CrawlBatch.from_json(batch) if batch else None
```

**3. Database Sharding Strategy**
```python
class ShardedRepositoryRepository:
    def __init__(self, shard_pools: Dict[int, DatabasePool]):
        self.shard_pools = shard_pools
        self.shard_count = len(shard_pools)
    
    def get_shard_id(self, repo_id: int) -> int:
        """Determine shard based on repository ID."""
        return repo_id % self.shard_count
    
    async def save(self, repository: Repository) -> Repository:
        shard_id = self.get_shard_id(repository.repo_id)
        pool = self.shard_pools[shard_id]
        # Use shard-specific repository implementation
        return await self._save_to_shard(pool, repository)
```

### 2. GitHub API Rate Limit Management

#### Token Pool Strategy
- **Requirement**: ~1,000 GitHub tokens to achieve required throughput
- **Cost**: $4/month per token = $4,000/month for tokens
- **Organization**: Use GitHub Apps or multiple organizations
- **Rate Limit**: 5,000 requests/hour/token × 1,000 tokens = 5M requests/hour

#### Optimized GraphQL Queries
```graphql
query GetRepositoriesBatch($cursor: String, $first: Int!) {
  search(
    query: "stars:>0 sort:updated", 
    type: REPOSITORY, 
    first: $first, 
    after: $cursor
  ) {
    repositoryCount
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      ... on Repository {
        databaseId
        nameWithOwner
        owner { login, __typename }
        stargazerCount
        forkCount
        watchers { totalCount }
        primaryLanguage { name }
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
}
```

### 3. Database Architecture

#### Sharding Strategy
```sql
-- Shard by repository ID modulo
-- Shard 0: repo_id % 100 = 0-9
-- Shard 1: repo_id % 100 = 10-19
-- ...
-- Shard 9: repo_id % 100 = 90-99

-- Each shard contains:
CREATE TABLE repositories_shard_0 (
    LIKE repositories INCLUDING ALL
);

-- Partition by date for time-series data
CREATE TABLE crawl_jobs_2024_01 PARTITION OF crawl_jobs
FOR VALUES FROM ('2024-01-01') TO ('2024-02-01');
```

#### Database Sizing Estimates
- **500M repositories × 1KB/record = 500GB**
- **Indexes: ~200GB additional**
- **Total per shard (10 shards): ~70GB**
- **Recommended**: PostgreSQL cluster with 10 primary shards + replicas

#### Read Replicas Strategy
```
Primary Shard 0 ─── Read Replica 0A ─── Read Replica 0B
Primary Shard 1 ─── Read Replica 1A ─── Read Replica 1B
...
Primary Shard 9 ─── Read Replica 9A ─── Read Replica 9B
```

### 4. Infrastructure Requirements

#### Compute Resources
```yaml
# Kubernetes deployment example
apiVersion: apps/v1
kind: Deployment
metadata:
  name: github-crawler
spec:
  replicas: 100  # Scale based on token availability
  template:
    spec:
      containers:
      - name: crawler
        image: github-crawler:latest
        resources:
          requests:
            memory: "512Mi"
            cpu: "500m"
          limits:
            memory: "1Gi"
            cpu: "1000m"
        env:
        - name: WORKER_ID
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - name: GITHUB_TOKENS
          valueFrom:
            secretRef:
              name: github-tokens
```

#### Storage Requirements
- **Database Storage**: 1TB per shard (10 shards = 10TB total)
- **Backup Storage**: 10TB × 3 (daily, weekly, monthly) = 30TB
- **Log Storage**: ~100GB/day × 30 days = 3TB
- **Export Storage**: ~50GB for full exports

#### Network Requirements
- **Bandwidth**: ~100 Mbps sustained for API calls
- **Latency**: Sub-100ms to GitHub API (CDN/edge locations)
- **Internal**: High-bandwidth between crawler instances and database

### 5. Performance Optimizations

#### Batch Processing Improvements
```python
class OptimizedBatchProcessor:
    def __init__(self, batch_size: int = 10000):
        self.batch_size = batch_size
        self.connection_pool = AsyncConnectionPool(min_size=50, max_size=100)
    
    async def process_mega_batch(self, repositories: List[Repository]):
        """Process very large batches with parallel writes."""
        tasks = []
        for i in range(0, len(repositories), self.batch_size):
            batch = repositories[i:i + self.batch_size]
            task = asyncio.create_task(self._process_batch(batch))
            tasks.append(task)
        
        await asyncio.gather(*tasks, return_exceptions=True)
```

#### Caching Strategy
```python
class RepositoryCache:
    def __init__(self, redis_client):
        self.redis = redis_client
        self.cache_ttl = 3600  # 1 hour
    
    async def get_repository(self, repo_id: int) -> Optional[Repository]:
        """Get repository from cache first."""
        cached = await self.redis.get(f"repo:{repo_id}")
        if cached:
            return Repository.from_json(cached)
        return None
    
    async def cache_repository(self, repository: Repository):
        """Cache repository data."""
        await self.redis.setex(
            f"repo:{repository.repo_id}",
            self.cache_ttl,
            repository.to_json()
        )
```

### 6. Monitoring and Observability

#### Metrics to Track
```python
class CrawlerMetrics:
    def __init__(self):
        self.repos_processed = Counter('repos_processed_total')
        self.api_requests = Counter('github_api_requests_total')
        self.rate_limit_hits = Counter('rate_limit_hits_total')
        self.processing_duration = Histogram('repo_processing_duration_seconds')
        self.database_operations = Counter('database_operations_total')
        self.error_count = Counter('errors_total')
```

#### Alerting Thresholds
- Repository processing rate < 1000/minute
- Error rate > 5%
- Rate limit exhaustion on > 10% of tokens
- Database connection pool exhaustion
- Shard availability < 90%

### 7. Cost Analysis

#### Infrastructure Costs (Monthly)
- **Compute**: 100 instances × $100/month = $10,000
- **Database**: 10 shards × $500/month = $5,000  
- **Storage**: 40TB × $50/TB = $2,000
- **Network**: $1,000
- **GitHub Tokens**: 1,000 tokens × $4 = $4,000
- **Monitoring/Logging**: $500
- **Total**: ~$22,500/month

#### Cost Optimization Strategies
1. **Spot Instances**: Use AWS Spot for 50-70% cost reduction
2. **Token Sharing**: Share tokens across multiple projects
3. **Tiered Storage**: Move old data to cheaper storage tiers
4. **Reserved Instances**: Commit to 1-3 year terms for discounts

### 8. Deployment Strategy

#### Phased Rollout
1. **Phase 1**: Scale to 1M repositories (10x current)
2. **Phase 2**: Scale to 10M repositories (100x current) 
3. **Phase 3**: Scale to 100M repositories (1000x current)
4. **Phase 4**: Scale to 500M repositories (5000x current)

#### Risk Mitigation
- **Circuit Breakers**: Prevent cascade failures
- **Blue-Green Deployments**: Zero-downtime updates
- **Canary Releases**: Gradual rollout of changes
- **Rollback Plans**: Quick recovery from issues

### 9. Alternative Architectures

#### Stream Processing Approach
```python
# Apache Kafka + Kafka Streams
class KafkaStreamProcessor:
    async def process_repo_stream(self):
        """Process repositories as streaming data."""
        async for message in self.kafka_consumer:
            repository = Repository.from_json(message.value)
            await self.process_repository(repository)
            await self.kafka_producer.send("processed_repos", repository.to_json())
```

#### Serverless Approach
```yaml
# AWS Lambda with SQS
Resources:
  CrawlerFunction:
    Type: AWS::Lambda::Function
    Properties:
      Runtime: python3.11
      Handler: crawler.lambda_handler
      ReservedConcurrency: 1000  # Process 1000 repos concurrently
      
  RepoQueue:
    Type: AWS::SQS::Queue
    Properties:
      VisibilityTimeoutSeconds: 300
      MessageRetentionPeriod: 1209600  # 14 days
```

## Summary

Scaling from 100K to 500M repositories requires fundamental architectural changes:

1. **Distributed Processing**: 100+ crawler instances with token pools
2. **Database Sharding**: 10+ PostgreSQL shards with read replicas  
3. **Rate Limit Management**: 1000+ GitHub tokens with intelligent routing
4. **Infrastructure**: Cloud-native with auto-scaling capabilities
5. **Monitoring**: Comprehensive observability and alerting
6. **Cost Management**: ~$270K/year operational costs

The key to success is gradual scaling with thorough testing at each phase, robust error handling, and comprehensive monitoring to identify bottlenecks early.
