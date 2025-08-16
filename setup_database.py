#!/usr/bin/env python3
"""
Setup PostgreSQL database and run migrations
"""

import asyncio
import asyncpg
import os
from pathlib import Path
try:
    from dotenv import load_dotenv
    load_dotenv()
except:
    # Continue without .env file - use environment variables directly
    pass

async def create_database():
    """Create the github_crawler database if it doesn't exist"""
    
    print("🗄️  Setting up PostgreSQL database...")
    
    # Connect to postgres default database first
    try:
        print("🔌 Connecting to PostgreSQL server...")
        conn = await asyncpg.connect(
             host='localhost',
             port=5432,
             user='postgres',
             password='$$1234$$',  # Your PostgreSQL password
             database='postgres'  # Connect to default postgres database
         )
        
        print("✅ Connected to PostgreSQL server")
        
        # Check if database exists
        db_exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = 'github_crawler'"
        )
        
        if db_exists:
            print("✅ Database 'github_crawler' already exists")
        else:
            print("📋 Creating database 'github_crawler'...")
            await conn.execute("CREATE DATABASE github_crawler")
            print("✅ Database 'github_crawler' created successfully")
        
        await conn.close()
        return True
        
    except asyncpg.InvalidPasswordError:
        print("❌ Authentication failed. Check your PostgreSQL password.")
        print("💡 Common passwords: 'password', 'postgres', or what you set during installation")
        return False
    except Exception as e:
        print(f"❌ Failed to connect to PostgreSQL: {e}")
        print("💡 Make sure PostgreSQL is running: Get-Service postgresql-*")
        return False

async def run_migrations():
    """Run database migrations to create tables"""
    
    print("\n📋 Running database migrations...")
    
    # Use the connection string from .env or default
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:$$1234$$@localhost:5432/github_crawler')
    
    try:
        print("🔌 Connecting to github_crawler database...")
        conn = await asyncpg.connect(database_url)
        
        print("✅ Connected to github_crawler database")
        
        # Read and execute migration file
        migration_file = Path("migrations/001_initial_schema.sql")
        
        if not migration_file.exists():
            print(f"❌ Migration file not found: {migration_file}")
            return False
        
        print("📄 Reading migration file...")
        with open(migration_file, 'r', encoding='utf-8') as f:
            migration_sql = f.read()
        
        print("⚡ Executing migrations...")
        await conn.execute(migration_sql)
        
        print("✅ Migrations executed successfully!")
        
        # Verify tables were created
        tables = await conn.fetch("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            ORDER BY table_name
        """)
        
        print(f"📊 Created {len(tables)} tables:")
        for table in tables:
            print(f"   • {table['table_name']}")
        
        await conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        return False

async def test_connection():
    """Test the final database connection"""
    
    print("\n🧪 Testing database connection...")
    
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:$$1234$$@localhost:5432/github_crawler')
    
    try:
        conn = await asyncpg.connect(database_url)
        
        # Test basic operations
        version = await conn.fetchval('SELECT version()')
        repo_count = await conn.fetchval('SELECT COUNT(*) FROM repositories')
        
        print("✅ Database connection test passed!")
        print(f"🔧 PostgreSQL version: {version.split(',')[0]}")
        print(f"📊 Current repositories: {repo_count}")
        
        await conn.close()
        return True
        
    except Exception as e:
        print(f"❌ Connection test failed: {e}")
        return False

async def main():
    """Main setup function"""
    
    print("🚀 PostgreSQL Database Setup for GitHub Crawler")
    print("=" * 60)
    
    # Step 1: Create database
    db_created = await create_database()
    if not db_created:
        print("\n❌ Database creation failed. Please check your PostgreSQL setup.")
        return
    
    # Step 2: Run migrations
    migrations_ok = await run_migrations()
    if not migrations_ok:
        print("\n❌ Migrations failed.")
        return
    
    # Step 3: Test connection
    test_ok = await test_connection()
    if not test_ok:
        print("\n❌ Connection test failed.")
        return
    
    print("\n🎉 Database setup completed successfully!")
    print("\n📋 Next steps:")
    print("1. python test_github_crawler.py  # Test GitHub API")
    print("2. python -c \"from src.main import *; # Test full system")
    print("3. Start crawling repositories!")
    
    # Show connection string
    database_url = os.getenv('DATABASE_URL', 'postgresql://postgres:$$1234$$@localhost:5432/github_crawler')
    print(f"\n🔗 Your database URL: {database_url}")

if __name__ == "__main__":
    asyncio.run(main())
