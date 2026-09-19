#!/usr/bin/env python3
"""
Migration script from SQLite to PostgreSQL.
Safely migrates all data while preserving relationships and constraints.
"""

import os
import sys
from pathlib import Path
from typing import Any

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent / "apps" / "api" / "src"))

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from robopark_api.models import Base
from robopark_api.config import get_settings


def migrate_sqlite_to_postgres(
    sqlite_url: str,
    postgres_url: str,
    batch_size: int = 1000,
    dry_run: bool = False
) -> None:
    """
    Migrate data from SQLite to PostgreSQL.
    
    Args:
        sqlite_url: SQLite database URL
        postgres_url: PostgreSQL database URL
        batch_size: Number of rows to migrate at once
        dry_run: If True, don't actually write to PostgreSQL
    """
    print("=" * 80)
    print("Robopark SQLite → PostgreSQL Migration")
    print("=" * 80)
    print()
    
    # Connect to both databases
    print("Connecting to databases...")
    sqlite_engine = create_engine(sqlite_url, future=True)
    postgres_engine = create_engine(postgres_url, future=True)
    
    SQLiteSession = sessionmaker(bind=sqlite_engine)
    PostgresSession = sessionmaker(bind=postgres_engine)
    
    sqlite_session = SQLiteSession()
    postgres_session = PostgresSession()
    
    try:
        # Create schema in PostgreSQL
        print("Creating PostgreSQL schema...")
        if not dry_run:
            Base.metadata.create_all(postgres_engine)
        print("✓ Schema created")
        print()
        
        # Get all tables in dependency order
        tables = [
            table for table in Base.metadata.sorted_tables
        ]
        
        total_rows = 0
        
        # Migrate each table
        for table in tables:
            table_name = table.name
            print(f"Migrating table: {table_name}")
            
            # Count rows in SQLite
            count_query = text(f"SELECT COUNT(*) FROM {table_name}")
            row_count = sqlite_session.execute(count_query).scalar()
            
            if row_count == 0:
                print(f"  ⊘ Empty table, skipping")
                print()
                continue
            
            print(f"  Found {row_count:,} rows")
            
            # Fetch all rows from SQLite
            select_query = text(f"SELECT * FROM {table_name}")
            rows = sqlite_session.execute(select_query).fetchall()
            
            if dry_run:
                print(f"  [DRY RUN] Would migrate {len(rows):,} rows")
            else:
                # Insert into PostgreSQL in batches
                migrated = 0
                for i in range(0, len(rows), batch_size):
                    batch = rows[i:i + batch_size]
                    
                    # Build INSERT statement
                    columns = list(rows[0]._mapping.keys())
                    placeholders = ", ".join([f":{col}" for col in columns])
                    insert_query = text(
                        f"INSERT INTO {table_name} ({', '.join(columns)}) "
                        f"VALUES ({placeholders})"
                    )
                    
                    # Execute batch
                    for row in batch:
                        postgres_session.execute(
                            insert_query,
                            dict(row._mapping)
                        )
                        migrated += 1
                    
                    postgres_session.commit()
                    print(f"  ✓ Migrated {migrated:,}/{row_count:,} rows", end="\r")
                
                print(f"  ✓ Migrated {migrated:,} rows")
            
            total_rows += row_count
            print()
        
        # Update sequences for auto-increment columns
        if not dry_run:
            print("Updating sequences...")
            for table in tables:
                # Find primary key column
                pk_columns = [col for col in table.columns if col.primary_key]
                if not pk_columns:
                    continue
                
                pk_col = pk_columns[0]
                if not pk_col.autoincrement:
                    continue
                
                # Get max value
                max_query = text(f"SELECT MAX({pk_col.name}) FROM {table.name}")
                max_val = postgres_session.execute(max_query).scalar()
                
                if max_val is not None:
                    # Update sequence
                    seq_name = f"{table.name}_{pk_col.name}_seq"
                    postgres_session.execute(
                        text(f"SELECT setval('{seq_name}', {max_val})")
                    )
                    print(f"  ✓ Updated sequence {seq_name} to {max_val}")
            
            postgres_session.commit()
            print()
        
        # Verify migration
        print("Verifying migration...")
        all_match = True
        for table in tables:
            sqlite_count = sqlite_session.execute(
                text(f"SELECT COUNT(*) FROM {table.name}")
            ).scalar()
            
            if not dry_run:
                postgres_count = postgres_session.execute(
                    text(f"SELECT COUNT(*) FROM {table.name}")
                ).scalar()
                
                if sqlite_count != postgres_count:
                    print(f"  ✗ {table.name}: SQLite={sqlite_count}, PostgreSQL={postgres_count}")
                    all_match = False
                else:
                    print(f"  ✓ {table.name}: {sqlite_count:,} rows")
            else:
                print(f"  [DRY RUN] {table.name}: {sqlite_count:,} rows")
        
        print()
        
        if dry_run:
            print("=" * 80)
            print("DRY RUN COMPLETE")
            print(f"Would migrate {total_rows:,} total rows")
            print("Run without --dry-run to perform actual migration")
            print("=" * 80)
        elif all_match:
            print("=" * 80)
            print("✓ MIGRATION SUCCESSFUL")
            print(f"Migrated {total_rows:,} total rows")
            print("=" * 80)
        else:
            print("=" * 80)
            print("✗ MIGRATION FAILED")
            print("Row counts don't match!")
            print("=" * 80)
            sys.exit(1)
    
    except Exception as e:
        print()
        print("=" * 80)
        print("✗ MIGRATION FAILED")
        print(f"Error: {e}")
        print("=" * 80)
        if not dry_run:
            postgres_session.rollback()
        raise
    
    finally:
        sqlite_session.close()
        postgres_session.close()


def main():
    import argparse
    
    parser = argparse.ArgumentParser(description="Migrate Robopark from SQLite to PostgreSQL")
    parser.add_argument(
        "--sqlite-url",
        default="sqlite:///data/robopark.db",
        help="SQLite database URL (default: sqlite:///data/robopark.db)"
    )
    parser.add_argument(
        "--postgres-url",
        help="PostgreSQL database URL (default: from POSTGRES_URL env var)"
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Number of rows to migrate at once (default: 1000)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Don't actually write to PostgreSQL, just show what would be done"
    )
    
    args = parser.parse_args()
    
    # Get PostgreSQL URL
    postgres_url = args.postgres_url or os.getenv("POSTGRES_URL")
    if not postgres_url:
        print("Error: PostgreSQL URL not provided")
        print("Set POSTGRES_URL environment variable or use --postgres-url")
        sys.exit(1)
    
    # Confirm migration
    if not args.dry_run:
        print("WARNING: This will migrate data to PostgreSQL")
        print(f"Source: {args.sqlite_url}")
        print(f"Target: {postgres_url}")
        print()
        response = input("Continue? (yes/no): ")
        if response.lower() != "yes":
            print("Migration cancelled")
            sys.exit(0)
        print()
    
    # Run migration
    migrate_sqlite_to_postgres(
        sqlite_url=args.sqlite_url,
        postgres_url=postgres_url,
        batch_size=args.batch_size,
        dry_run=args.dry_run
    )


if __name__ == "__main__":
    main()
