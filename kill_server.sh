#!/bin/bash
if [ -f .server.pid ]; then
    PID=$(cat .server.pid)
    echo "Stopping server with PID $PID"
    kill $PID
    rm .server.pid
else
    echo "No server PID file found. Is the server running?"
fi
