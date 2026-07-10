#!/bin/sh
exec gunicorn app:app --bind "0.0.0.0:${PORT:-5050}" --workers 2 --timeout 120
