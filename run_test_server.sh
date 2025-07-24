#!/bin/bash
# Generate a random port number between 1024 and 49151
PORT=$((RANDOM % 48128 + 1024))

echo "Starting server on port $PORT"

# Activate virtual environment
source .venv/bin/activate

# Set Flask environment variables
export FLASK_APP=main
export FLASK_DEBUG=1

# Start server in the background and save its PID to .server.pid
python -m flask run -p $PORT &
echo $! > .server.pid

echo "Server started with PID $(cat .server.pid) on port $PORT."
echo "Use './kill_server.sh' to stop it."
