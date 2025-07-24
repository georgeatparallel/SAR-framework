#!/bin/bash
# Find an available port and save it to a file
PORT=$(python -c 'import socket; s=socket.socket(); s.bind(("", 0)); print(s.getsockname()[1]); s.close()')
echo $PORT > .port

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
