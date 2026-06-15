#!/bin/bash
cd "$(dirname "$0")"
export PYTHONPATH=.
echo "Starting Glomz on port 5000 with Gunicorn..."
pkill -9 -f "gunicorn.*5000" 2>/dev/null || true
sleep 2
gunicorn --bind 127.0.0.1:5000 --workers 2 --threads 4 --daemon --log-level info --access-logfile gunicorn_access.log --error-logfile gunicorn_error.log app:app
echo "Glomz started. Checking health..."
sleep 3
curl -s http://127.0.0.1:5000/api/health || echo "Health check failed"
echo "Done."