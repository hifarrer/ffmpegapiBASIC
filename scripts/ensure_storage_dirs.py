#!/usr/bin/env python3
"""
Create UPLOAD_FOLDER and OUTPUT_FOLDER so endpoints don't fail (e.g. on Railway).
Uses same logic as main app: env UPLOAD_FOLDER/OUTPUT_FOLDER, or /tmp/... in production, or local dirs.
Run from repo root: python scripts/ensure_storage_dirs.py
"""
import os
import sys

def main():
    is_production = bool(os.environ.get("REPLIT_DEPLOYMENT") or os.environ.get("RAILWAY_ENVIRONMENT"))
    upload = os.environ.get("UPLOAD_FOLDER") or ("/tmp/uploads" if is_production else "uploads")
    output = os.environ.get("OUTPUT_FOLDER") or ("/tmp/outputs" if is_production else "outputs")
    for path in (upload, output):
        os.makedirs(path, exist_ok=True)
        print(f"OK: {path}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
