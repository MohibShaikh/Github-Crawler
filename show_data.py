#!/usr/bin/env python3
"""Show crawled repository data."""

import asyncio
import asyncpg
import os
from dotenv import load_dotenv

async def show_repositories():
    """Display crawled repositories."""
    
    # Load environment
    load_dotenv()
    
    # Connect to database
    database_url = os.getenv('DATABASE_URL')
    if not database_url:
        print("❌ DATABASE_URL not set")
        return
    
    try:
        conn = await asyncpg.connect(database_url)
        
        # Get repositories
        rows = await conn.fetch("""
            SELECT 
                name,
                full_name,
                stars,
                language,
                description,
                created_at,
                updated_at
            FROM repositories 
            ORDER BY stars DESC
        """)
        
        print("📊 Crawled Repositories")
        print("=" * 80)
        print(f"{'Repository':<30} {'Full Name':<25} {'Stars':<8} {'Language':<12} {'Created'}")
        print("-" * 80)
        
        for row in rows:
            name = row['name'][:28] + ".." if len(row['name']) > 30 else row['name']
            full_name = row['full_name'][:23] + ".." if len(row['full_name']) > 25 else row['full_name']
            language = row['language'][:10] + ".." if row['language'] and len(row['language']) > 12 else (row['language'] or "N/A")
            created = row['created_at'].strftime('%Y-%m-%d') if row['created_at'] else "N/A"
            
            print(f"{name:<30} {full_name:<25} {row['stars']:<8} {language:<12} {created}")
        
        print(f"\n✅ Total repositories: {len(rows)}")
        
        # Get crawl job info
        job_rows = await conn.fetch("""
            SELECT 
                id,
                target_count,
                records_processed,
                records_created,
                records_updated,
                started_at,
                completed_at
            FROM crawl_jobs 
            ORDER BY started_at DESC 
            LIMIT 3
        """)
        
        print(f"\n📋 Recent Crawl Jobs")
        print("=" * 80)
        for job in job_rows:
            duration = job['completed_at'] - job['started_at'] if job['completed_at'] else "Running"
            print(f"Job {job['id']}: {job['records_processed']}/{job['target_count']} repos "
                  f"({job['records_created']} created, {job['records_updated']} updated) "
                  f"Duration: {duration}")
        
        await conn.close()
        
    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    asyncio.run(show_repositories())
