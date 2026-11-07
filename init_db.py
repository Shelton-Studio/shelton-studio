#!/usr/bin/env python3
"""Initialize Shelton database"""

from app.database.db import init_db
from app.config import settings

print("🤖 Initializing Shelton database...")
print(f"📍 Database: {settings.DATABASE_URL}")

try:
    init_db()
    print("✅ Database initialized successfully!")
    print("\n📂 Created tables:")
    print("   - jobs")
    print("   - content")
    print("   - processing_logs")
    print("\n✓ Ready to start processing!")
except Exception as e:
    print(f"❌ Error: {e}")
    exit(1)
