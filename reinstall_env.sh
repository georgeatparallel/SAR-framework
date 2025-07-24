#!/bin/bash
#
# This script performs a factory reinstall of the Python virtual environment.
# It ensures a clean state by removing the old environment, reinstalling
# all dependencies, and setting up Playwright.

echo "--- Starting Environment Factory Reset ---"

# Check if the virtual environment directory exists
if [ -d ".venv" ]; then
    echo "[1/4] Removing existing virtual environment..."
    rm -rf .venv
    echo "      -> Done."
else
    echo "[1/4] No existing virtual environment found. Skipping removal."
fi

# Create a new virtual environment
echo "[2/4] Creating a new Python virtual environment..."
python3 -m venv .venv
echo "      -> Done."

# Activate the new environment and install dependencies
echo "[3/4] Activating the environment and installing dependencies from requirements.txt..."
source .venv/bin/activate && pip install -r requirements.txt
echo "      -> Done."

# Install Playwright's browser binaries
echo "[4/4] Installing Playwright browser binaries..."
source .venv/bin/activate && playwright install
echo "      -> Done."

echo "
--- Environment Factory Reset Complete! ---"
echo "The virtual environment has been recreated and all dependencies are installed."
