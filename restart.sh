#!/bin/bash
# Restart the Polymarket Scanner server
cd "$(dirname "$0")"
echo "Stopping existing server..."
lsof -ti :8899 | xargs kill -9 2>/dev/null
sleep 1
echo "Starting server..."
arch -arm64 python3 server.py
