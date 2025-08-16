"""Main application entry point for GitHub star crawler."""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

import structlog
import typer
from dotenv import load_dotenv

from .infrastructure.database import (
    DatabasePool,
    PostgreSQLRepositoryRepository,
    PostgreSQLCrawlJobRepository,
    PostgreSQLRateLimitRepository,
    PostgreSQLDatabaseRepository
)
from .infrastructure.github_api import GitHubGraphQLClient
from .application.use_cases import (
    CrawlRepositoriesUseCase,
    ExportDataUseCase,
    SetupDatabaseUseCase,
    HealthCheckUseCase
)

# Load environment variables (skip .env file to avoid encoding issues)
load_dotenv()


# Configure structured logging
structlog.configure(
    processors=[
        structlog.stdlib.filter_by_level,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.JSONRenderer()
    ],
    context_class=dict,
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.stdlib.BoundLogger,
    cache_logger_on_first_use=True,
)

logger = structlog.get_logger()

# CLI application
app = typer.Typer(
    name="github-star-crawler",
    help="GitHub repository star crawler with PostgreSQL storage",
    no_args_is_help=True
)


def get_database_url() -> str:
    """Get database URL from environment."""
    return os.getenv(
        "DATABASE_URL",
        "postgresql://postgres:password@localhost:5432/github_crawler"
    )


def get_github_token() -> str:
    """Get GitHub token from environment."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        typer.echo("Error: GITHUB_TOKEN environment variable is required", err=True)
        raise typer.Exit(1)
    return token


async def create_database_pool() -> DatabasePool:
    """Create and initialize database pool."""
    database_url = get_database_url()
    pool = DatabasePool(database_url)
    await pool.initialize()
    return pool


@app.command()
def setup_postgres(
    migration_dir: str = typer.Option(
        "migrations",
        "--migration-dir",
        "-m",
        help="Directory containing migration files"
    )
):
    """Setup PostgreSQL database schema."""
    async def _setup():
        logger.info("Setting up PostgreSQL database")
        
        pool = await create_database_pool()
        try:
            database_repo = PostgreSQLDatabaseRepository(pool)
            use_case = SetupDatabaseUseCase(database_repo)
            
            # Find migration files
            migration_path = Path(migration_dir)
            if not migration_path.exists():
                typer.echo(f"Error: Migration directory {migration_dir} not found", err=True)
                raise typer.Exit(1)
            
            migration_files = sorted(migration_path.glob("*.sql"))
            if not migration_files:
                typer.echo(f"Error: No SQL files found in {migration_dir}", err=True)
                raise typer.Exit(1)
            
            await use_case.execute([str(f) for f in migration_files])
            
            typer.echo("✅ Database setup completed successfully")
            
        finally:
            await pool.close()
    
    asyncio.run(_setup())


@app.command()
def crawl_stars(
    target_count: int = typer.Option(
        100000,
        "--target-count",
        "-c",
        help="Number of repositories to crawl"
    ),
    batch_size: int = typer.Option(
        2000,
        "--batch-size",
        "-b",
        help="Batch size for database operations"
    ),
    resume: bool = typer.Option(
        True,
        "--resume/--no-resume",
        help="Resume from last crawl position"
    )
):
    """Crawl GitHub repository stars."""
    async def _crawl():
        logger.info("Starting GitHub repository crawl",
                   target_count=target_count,
                   batch_size=batch_size,
                   resume=resume)
        
        github_token = get_github_token()
        pool = await create_database_pool()
        
        try:
            # Initialize repositories
            repo_repo = PostgreSQLRepositoryRepository(pool)
            job_repo = PostgreSQLCrawlJobRepository(pool)
            rate_limit_repo = PostgreSQLRateLimitRepository(pool)
            
            # Initialize GitHub client
            async with GitHubGraphQLClient(github_token) as github_client:
                # Execute crawl use case
                use_case = CrawlRepositoriesUseCase(
                    repo_repo, job_repo, rate_limit_repo, github_client
                )
                
                job = await use_case.execute(
                    target_count=target_count,
                    batch_size=batch_size,
                    resume_from_last=resume
                )
                
                typer.echo("✅ Repository crawl completed successfully")
                typer.echo(f"Job ID: {job.job_id}")
                typer.echo(f"Records processed: {job.records_processed}")
                typer.echo(f"Records created: {job.records_created}")
                typer.echo(f"Records updated: {job.records_updated}")
                
        except Exception as e:
            logger.error("Crawl failed", error=str(e))
            typer.echo(f"❌ Crawl failed: {e}", err=True)
            raise typer.Exit(1)
            
        finally:
            await pool.close()
    
    asyncio.run(_crawl())


@app.command()
def export_data(
    output_file: str = typer.Argument(..., help="Output file path"),
    format: str = typer.Option(
        "csv",
        "--format",
        "-f",
        help="Export format (csv or json)"
    )
):
    """Export crawled data to file."""
    async def _export():
        logger.info("Exporting data", output_file=output_file, format=format)
        
        pool = await create_database_pool()
        
        try:
            database_repo = PostgreSQLDatabaseRepository(pool)
            use_case = ExportDataUseCase(database_repo)
            
            if format.lower() == "csv":
                stats = await use_case.export_repositories_csv(output_file)
            elif format.lower() == "json":
                stats = await use_case.export_repositories_json(output_file)
            else:
                typer.echo(f"Error: Unsupported format '{format}'. Use 'csv' or 'json'", err=True)
                raise typer.Exit(1)
            
            typer.echo("✅ Data export completed successfully")
            typer.echo(f"Output file: {output_file}")
            typer.echo(f"Repository count: {stats.get('repositories_count', 0)}")
            
        except Exception as e:
            logger.error("Export failed", error=str(e))
            typer.echo(f"❌ Export failed: {e}", err=True)
            raise typer.Exit(1)
            
        finally:
            await pool.close()
    
    asyncio.run(_export())


@app.command()
def health_check():
    """Perform system health check."""
    async def _health():
        pool = await create_database_pool()
        
        try:
            database_repo = PostgreSQLDatabaseRepository(pool)
            job_repo = PostgreSQLCrawlJobRepository(pool)
            rate_limit_repo = PostgreSQLRateLimitRepository(pool)
            
            use_case = HealthCheckUseCase(database_repo, job_repo, rate_limit_repo)
            health_status = await use_case.get_system_health()
            
            # Print health status
            status_emoji = "✅" if health_status["status"] == "healthy" else "⚠️"
            typer.echo(f"{status_emoji} System Status: {health_status['status'].upper()}")
            typer.echo(f"Timestamp: {health_status['timestamp']}")
            typer.echo()
            
            for component, status in health_status["components"].items():
                comp_emoji = "✅" if status["status"] == "healthy" else "❌"
                typer.echo(f"{comp_emoji} {component.title()}: {status['status']}")
                
                if status["status"] == "healthy" and "stats" in status:
                    for key, value in status["stats"].items():
                        typer.echo(f"  {key}: {value}")
                elif status["status"] == "unhealthy":
                    typer.echo(f"  Error: {status.get('error', 'Unknown error')}")
                
                typer.echo()
            
        except Exception as e:
            logger.error("Health check failed", error=str(e))
            typer.echo(f"❌ Health check failed: {e}", err=True)
            raise typer.Exit(1)
            
        finally:
            await pool.close()
    
    asyncio.run(_health())


@app.command()
def run_pipeline(
    target_count: int = typer.Option(
        100000,
        "--target-count",
        "-c",
        help="Number of repositories to crawl"
    )
):
    """Run complete CI pipeline (setup + crawl + export)."""
    async def _pipeline():
        logger.info("Running complete pipeline", target_count=target_count)
        
        github_token = get_github_token()
        pool = await create_database_pool()
        
        try:
            # 1. Setup database
            typer.echo("🔧 Setting up database...")
            database_repo = PostgreSQLDatabaseRepository(pool)
            setup_use_case = SetupDatabaseUseCase(database_repo)
            
            migration_files = sorted(Path("migrations").glob("*.sql"))
            await setup_use_case.execute([str(f) for f in migration_files])
            typer.echo("✅ Database setup completed")
            
            # 2. Crawl repositories
            typer.echo(f"🕷️ Crawling {target_count} repositories...")
            repo_repo = PostgreSQLRepositoryRepository(pool)
            job_repo = PostgreSQLCrawlJobRepository(pool)
            rate_limit_repo = PostgreSQLRateLimitRepository(pool)
            
            async with GitHubGraphQLClient(github_token) as github_client:
                crawl_use_case = CrawlRepositoriesUseCase(
                    repo_repo, job_repo, rate_limit_repo, github_client
                )
                
                job = await crawl_use_case.execute(target_count=target_count)
                typer.echo(f"✅ Crawl completed: {job.records_processed} repositories")
            
            # 3. Export data
            typer.echo("📤 Exporting data...")
            export_use_case = ExportDataUseCase(database_repo)
            
            await export_use_case.export_repositories_csv("repositories.csv")
            await export_use_case.export_repositories_json("repositories.json")
            
            summary = await export_use_case.get_crawl_summary()
            typer.echo("✅ Export completed")
            
            # 4. Final summary
            typer.echo("\n🎉 Pipeline completed successfully!")
            typer.echo(f"Repositories crawled: {summary['database_stats'].get('repositories_count', 0)}")
            typer.echo(f"Database size: {summary['database_stats'].get('database_size', 'Unknown')}")
            typer.echo(f"Export files: repositories.csv, repositories.json")
            
        except Exception as e:
            logger.error("Pipeline failed", error=str(e))
            typer.echo(f"❌ Pipeline failed: {e}", err=True)
            raise typer.Exit(1)
            
        finally:
            await pool.close()
    
    asyncio.run(_pipeline())


if __name__ == "__main__":
    app()
