#!/usr/bin/env python3
"""
CI-specific GitHub crawler for GitHub Actions workflow
Designed to crawl the specified number of repositories and handle CI environment
"""

import asyncio
import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def ci_crawl_repositories():
    """Crawl repositories for CI environment"""
    
    print("🚀 GitHub Repository Star Crawler - CI Mode")
    print("=" * 60)
    
    # Get target count from environment
    target_count = int(os.getenv('TARGET_REPOSITORIES', '100000'))
    github_token = os.getenv('GITHUB_TOKEN')
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:password@localhost:5432/github_crawler')
    
    print(f"🎯 Target repositories: {target_count:,}")
    print(f"🔑 GitHub token: {github_token[:10] if github_token else 'NOT SET'}...")
    print(f"🗄️ Database: {database_url.split('@')[1] if '@' in database_url else database_url}")
    
    if not github_token:
        print("❌ GITHUB_TOKEN environment variable is required")
        sys.exit(1)
    
    try:
        # Import system components
        from src.infrastructure.database import (
            DatabasePool, 
            PostgreSQLRepositoryRepository,
            PostgreSQLCrawlJobRepository,
            PostgreSQLRateLimitRepository
        )
        from src.infrastructure.github_api import GitHubGraphQLClient
        from src.application.use_cases import CrawlRepositoriesUseCase
        
        print("\n📋 Step 1: Initialize Database Connection")
        db_pool = DatabasePool(database_url)
        await db_pool.initialize()
        print("✅ Database pool initialized")
        
        # Create repositories
        repo_repo = PostgreSQLRepositoryRepository(db_pool)
        job_repo = PostgreSQLCrawlJobRepository(db_pool)
        rate_limit_repo = PostgreSQLRateLimitRepository(db_pool)
        print("✅ Repository interfaces created")
        
        print("\n📋 Step 2: Initialize GitHub API Client")
        async with GitHubGraphQLClient(github_token) as github_client:
            print("✅ GitHub GraphQL client initialized")
            
            # Check rate limits before starting
            if github_client.rate_limit_info:
                remaining = github_client.rate_limit_info.remaining
                reset_at = github_client.rate_limit_info.reset_at
                print(f"📊 Rate limit: {remaining}/5000 remaining, resets at {reset_at}")
            
            print(f"\n📋 Step 3: Execute Repository Crawl ({target_count:,} repositories)")
            
            # Create and execute crawl use case
            use_case = CrawlRepositoriesUseCase(
                repo_repo, job_repo, rate_limit_repo, github_client
            )
            
            # Execute crawl with maximum batch size for speed
            batch_size = min(20000, target_count // 2)  # Ultra-large batches for maximum speed
            if batch_size < 5000:
                batch_size = 5000
            
            print(f"🔧 Using batch size: {batch_size}")
            
            job = await use_case.execute(
                target_count=target_count,
                batch_size=batch_size,
                resume_from_last=False  # Always start fresh in CI
            )
            
            print("\n✅ Repository crawl completed successfully!")
            print(f"📊 Crawl Job Results:")
            print(f"   • Job ID: {job.job_id}")
            print(f"   • Records processed: {job.records_processed:,}")
            print(f"   • Records created: {job.records_created:,}")
            print(f"   • Records updated: {job.records_updated:,}")
            print(f"   • Duration: {(job.completed_at - job.started_at).total_seconds():.1f} seconds")
            
            # Verify data in database
            print(f"\n📋 Step 4: Verify Crawled Data")
            total_repos = await repo_repo.count()
            print(f"✅ Total repositories in database: {total_repos:,}")
            
            # Get sample of top repositories
            sample_repos = await repo_repo.get_all_paginated(limit=10)
            if sample_repos:
                sorted_repos = sorted(sample_repos, key=lambda x: x.stars_count, reverse=True)
                print("🌟 Top repositories by stars:")
                for i, repo in enumerate(sorted_repos[:5], 1):
                    lang = repo.language or "Unknown"
                    print(f"   {i}. {repo.full_name}: {repo.stars_count:,} stars ({lang})")
            
            # Final rate limit check
            if github_client.rate_limit_info:
                remaining = github_client.rate_limit_info.remaining
                used = 5000 - remaining
                print(f"\n📊 Final API Usage: {used}/5000 requests used ({remaining} remaining)")
        
        await db_pool.close()
        
        print(f"\n🎉 CI Crawl completed successfully!")
        print(f"🎯 Target: {target_count:,} repositories")
        print(f"📊 Achieved: {job.records_processed:,} repositories processed")
        
        return True
        
    except Exception as e:
        print(f"\n❌ CI crawl failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

async def main():
    """Main CI crawler entry point"""
    await ci_crawl_repositories()

if __name__ == "__main__":
    asyncio.run(main())
