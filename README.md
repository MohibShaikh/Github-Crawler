# GitHub Repository Star Crawler

A high-performance GitHub repository metadata crawler using GraphQL API with Avian PostgreSQL storage. **Optimized for efficient crawling** with clean architecture principles.

> **⚡ Performance Optimized:** Crawls 100,000 repositories in ~70-80 minutes using optimized batch processing and smart rate limiting.

## 🚀 Features

- **⚡ Fast Crawling**: Optimized for high-speed performance with batch processing
- **🏗️ Clean Architecture**: Domain-driven design with anti-corruption layers
- **🗄️ Avian PostgreSQL Storage**: Managed database with automatic scaling and sharding
- **🔄 Rate Limit Management**: Smart GitHub API rate limiting with retry logic
- **📁 Flexible Export**: CSV and JSON export formats
- **🤖 CI/CD Ready**: Complete GitHub Actions workflow
- **📊 Extensible Schema**: Ready for issues, PRs, comments, CI checks

## 🏗️ Architecture

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│  GitHub API     │    │   Application   │    │   Database      │
│  (GraphQL)      │◄──►│   Layer         │◄──►│   (Avian PostgreSQL)│
│                 │    │                 │    │                 │
│ • Rate Limiting │    │ • Use Cases     │    │ • Repositories  │
│ • Retry Logic   │    │ • Orchestration │    │ • Issues        │
│ • Anti-Corruption    │ • Error Handling│    │ • Pull Requests │
└─────────────────┘    └─────────────────┘    │ • Comments      │
                                              │ • CI Checks     │
                                              └─────────────────┘
```

### Layer Structure

- **Domain Layer**: Core business entities and repository interfaces
- **Application Layer**: Use cases and business logic orchestration  
- **Infrastructure Layer**: External service integrations (GitHub API, PostgreSQL)
- **Interface Layer**: CLI commands and API endpoints

## 📋 Requirements

- Python 3.11+
- Avian PostgreSQL (managed database)
- GitHub Personal Access Token
- 4GB+ RAM (for large crawls)
- Stable internet connection

## ⚡ TL;DR - Fast Start

```bash
# 1. Setup
pip install -r requirements.txt
echo "GITHUB_TOKEN=your_token_here" > .env
echo "DATABASE_URL=postgresql://postgres:password@localhost:5432/github_crawler" >> .env

# 2. Initialize database
python setup_database.py

# 3. Crawl with optimized performance (FAST!)
python -m src.main crawl-stars --batch-size 3000 --target-count 10000

# 4. Export results
python -m src.main export-data repos.csv --format csv
```

## 🚀 Quick Start

### 1. Clone and Setup

```bash
git clone <repository-url>
cd github-star-crawler
pip install -r requirements.txt
```

### 2. Environment Configuration

Create a `.env` file with:
```bash
# Required
GITHUB_TOKEN=pat or default github token
DATABASE_URL=postgresql://avnadmin:PASSWORD@HOST:PORT/DB?sslmode=require

# Optional (performance tuning)
DEFAULT_BATCH_SIZE=3000
TARGET_REPOSITORIES=100000
```

Required environment variables:
- `GITHUB_TOKEN`: Your GitHub personal access token ([Create one here](https://github.com/settings/tokens))
- `DATABASE_URL`: PostgreSQL connection string

### 3. Database Setup

#### Avian PostgreSQL (Recommended for Production)
```bash
# Set your Avian database URL
export DATABASE_URL="postgresql://avnadmin:PASSWORD@HOST:PORT/DB?sslmode=require"

# Initialize schema
python setup_database.py
```

#### Local PostgreSQL (Development Only)
```bash
# Setup local PostgreSQL schema
python -m src.main setup-postgres

# Verify setup
python -m src.main health-check
```

### 4. Start Crawling

```bash
# Fast crawling with optimized batch size (RECOMMENDED)
python -m src.main crawl-stars --batch-size 3000

# Crawl specific number (100K repos)
python -m src.main crawl-stars --target-count 100000 --batch-size 3000

# Resume from last crawl
python -m src.main crawl-stars --resume
```

### 5. Export Data

```bash
# Export to CSV
python -m src.main export-data repositories.csv --format csv

# Export to JSON  
python -m src.main export-data repositories.json --format json
```

## 🐳 GitHub Actions CI/CD

The project includes a complete CI/CD pipeline that:

1. **Sets up Avian PostgreSQL service container**
2. **Installs dependencies and sets up environment**
3. **Creates database schema**
4. **Crawls repository data** 
5. **Exports results as artifacts**
6. **Performs health checks**

### Running the Pipeline

The pipeline runs automatically on:
- Push to `main` or `develop` branches
- Pull requests to `main`
- Manual workflow dispatch

### Manual Trigger

```bash
# Trigger via GitHub UI or API
curl -X POST \
  -H "Authorization: token YOUR_TOKEN" \
  -H "Accept: application/vnd.github.v3+json" \
  https://api.github.com/repos/OWNER/REPO/actions/workflows/crawl-stars.yml/dispatches \
  -d '{"ref":"main","inputs":{"target_count":"100000"}}'
```

## 📊 Performance Characteristics

### Performance Characteristics (100K repositories)

| Metric | **Current Performance** | **Notes** |
|--------|------------------------|-----------|
| **Processing Time** | ~70-80 minutes | Actual measured performance |
| **Batch Size** | 3,000 repos | Optimized for efficiency |
| **Throughput** | ~1,250-1,430 repos/min | Based on actual runtime |
| **Database Operations** | Optimized batches | Efficient upsert operations |

### Current Scale (100K repositories)
- **API Requests**: ~1,000 GraphQL queries
- **Database Size**: ~100MB (Avian PostgreSQL)
- **Memory Usage**: ~50MB  
- **Rate Limit Usage**: <1% of hourly limit

### Rate Limiting
- **GitHub API Limit**: 5,000 requests/hour per token
- **Intelligent Backoff**: Exponential retry with jitter
- **Secondary Rate Limit**: Automatic detection and handling
- **Token Rotation**: Ready for multi-token scenarios

## 🔧 Configuration

### Command Line Options

```bash
# Setup database
python -m src.main setup-postgres [--migration-dir migrations]

# Crawl repositories  
python -m src.main crawl-stars [--target-count 100000] [--batch-size 3000] [--resume/--no-resume]

# Export data
python -m src.main export-data OUTPUT_FILE [--format csv|json]

# Health check
python -m src.main health-check

# Complete pipeline
python -m src.main run-pipeline [--target-count 100000]
```

### Environment Variables

```bash
# GitHub API
GITHUB_TOKEN=your_token_here

# Database  
DATABASE_URL=postgresql://user:pass@host:port/db
DATABASE_MIN_POOL_SIZE=10
DATABASE_MAX_POOL_SIZE=20

# Crawler Settings
DEFAULT_BATCH_SIZE=3000
DEFAULT_TARGET_COUNT=100000
MAX_CONCURRENT_REQUESTS=5

# Rate Limiting
RESPECT_RATE_LIMITS=true
RATE_LIMIT_BUFFER=10

# Logging
LOG_LEVEL=INFO
LOG_FORMAT=json
```

## 📈 Scaling to 500M Repositories

The system is designed to scale efficiently. See [SCALING_ANALYSIS.md](SCALING_ANALYSIS.md) for detailed analysis of scaling to 500 million repositories, including:

- **Distributed Architecture**: Multi-instance deployment with token pools
- **Managed Database**: Avian PostgreSQL with automatic sharding and scaling
- **Infrastructure Requirements**: Compute, storage, and network needs
- **Cost Analysis**: Estimated operational costs and optimization strategies

Key scaling changes required:
- 100+ crawler instances
- 1,000+ GitHub tokens  
- Avian PostgreSQL cluster (auto-scaling)
- Message queue coordination
- ~$214K-274K/year operational cost

## 🗄️ Schema Evolution

The database schema is designed for extensibility. See [SCHEMA_EVOLUTION.md](SCHEMA_EVOLUTION.md) for comprehensive schema evolution strategy covering:

- **Incremental Updates**: Efficient delta processing
- **New Metadata Types**: Issues, PRs, comments, reviews, CI checks
- **Performance Optimization**: Partitioning, indexing, materialized views
- **Zero-Downtime Migrations**: Safe schema evolution practices

### Current Schema

```sql
repositories          # Core repository data
├── Basic info       # name, description, language
├── Metrics         # stars, forks, watchers  
├── Metadata        # private, fork, archived flags
└── Timestamps      # created, updated, crawled

issues               # GitHub issues (future)
pull_requests        # GitHub pull requests (future)  
comments            # Issue/PR comments (future)
ci_checks           # CI check results (future)
crawl_jobs          # Job tracking and resume capability
rate_limit_status   # API rate limit monitoring
```

## 🔍 Monitoring and Observability

### Health Checks

```bash
python -m src.main health-check
```

Monitors:
- Database connectivity and performance
- Active crawl jobs status
- Rate limit status
- System resource usage

### Logging

Structured JSON logging with:
- Request/response tracking
- Performance metrics
- Error details with context
- Rate limit information

### Metrics

Key metrics tracked:
- Repositories processed per minute
- API requests and rate limit usage
- Database operation performance
- Error rates and types

## 🏛️ Clean Architecture Implementation

### Domain Layer (`src/domain/`)
- **Entities**: Core business objects (Repository, Issue, etc.)
- **Repository Interfaces**: Data access contracts
- **Value Objects**: Immutable data structures

### Application Layer (`src/application/`)
- **Use Cases**: Business logic orchestration
- **Services**: Cross-cutting concerns

### Infrastructure Layer (`src/infrastructure/`)
- **Database**: Avian PostgreSQL implementations
- **GitHub API**: GraphQL client with rate limiting
- **Anti-Corruption Layer**: External API translation

### Benefits
- **Testability**: Clear dependencies and interfaces
- **Maintainability**: Separated concerns
- **Flexibility**: Easy to swap implementations
- **Scalability**: Clean extension points

## 🧪 Testing

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=src

# Run specific test categories
pytest tests/unit/
pytest tests/integration/
pytest tests/performance/
```

## 📝 Contributing

1. Fork the repository
2. Create feature branch (`git checkout -b feature/amazing-feature`)
3. Commit changes (`git commit -m 'Add amazing feature'`)
4. Push to branch (`git push origin feature/amazing-feature`)
5. Open Pull Request

### Development Guidelines

- Follow clean architecture principles
- Add comprehensive tests
- Update documentation
- Follow semantic versioning
- Use structured logging

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🤝 Support

- **Issues**: [GitHub Issues](../../issues)
- **Discussions**: [GitHub Discussions](../../discussions)
- **Documentation**: See `/docs` directory for detailed guides

## 🔗 Related Projects

- [GitHub GraphQL API Documentation](https://docs.github.com/en/graphql)
- [Avian PostgreSQL Documentation](https://avian.com/docs)
- [asyncpg](https://github.com/MagicStack/asyncpg) - PostgreSQL adapter
- [aiohttp](https://github.com/aio-libs/aiohttp) - HTTP client library

---

**Built with ❤️ for efficient GitHub data collection**
