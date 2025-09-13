#!/usr/bin/env bash
set -e
PORT="${PORT:-8080}"
exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
