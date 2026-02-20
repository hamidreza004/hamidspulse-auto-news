#!/usr/bin/env python3
"""Migration script to add profile_photo_path column to source_channels table"""

import sqlite3
import os

def migrate():
    db_path = "./data/news.db"
    
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # Check if column already exists
        cursor.execute("PRAGMA table_info(source_channels)")
        columns = [col[1] for col in cursor.fetchall()]
        
        if 'profile_photo_path' in columns:
            print("Column 'profile_photo_path' already exists. Migration not needed.")
            return
        
        # Add the column
        print("Adding 'profile_photo_path' column to source_channels table...")
        cursor.execute("ALTER TABLE source_channels ADD COLUMN profile_photo_path TEXT")
        conn.commit()
        print("✓ Migration complete!")
        
    except Exception as e:
        print(f"Error during migration: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    migrate()
