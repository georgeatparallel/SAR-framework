#!/bin/bash
#
# This script performs a factory reinstall of the Python virtual environment.
# It now uses `nix-shell .idx/dev.nix` to explicitly load the correct
# environment configuration for the IDX platform.

echo "--- Starting IDX Environment Factory Reset ---"
echo "This will build the environment from '.idx/dev.nix' and then set up Python."

# We explicitly point nix-shell to the correct configuration file for the IDX environment.
# The --run flag executes the provided string of commands inside that new shell.
# `set -e` ensures that the script will exit immediately if any command fails.
nix-shell .idx/dev.nix --run "
  set -e

  echo
  echo '[0/5] Identifying Operating System...'
  if [ -f /etc/os-release ]; then
    cat /etc/os-release
  else
    echo 'Cannot find /etc/os-release. OS could not be identified.'
  fi
  echo '      -> Done.'

  echo
  echo '[1/5] Removing existing virtual environment...'
  rm -rf .venv
  echo '      -> Done.'

  echo
  echo '[2/5] Creating a new Python virtual environment...'
  python3 -m venv .venv
  echo '      -> Done.'

  echo
  echo '[3/5] Activating environment and installing dependencies from requirements.txt...'
  # We must source the venv activate script within this sub-shell
  source .venv/bin/activate
  pip install -r requirements.txt
  echo '      -> Done.'

  echo
  echo '[4/5] Installing Playwright browser binaries...'
  # The venv is already active, so we can call playwright directly.
  playwright install
  echo '      -> Done.'

  echo
  echo '[5/5] Verifying Playwright browser dependencies...'
  ldd ~/.cache/ms-playwright/chromium-1181/chrome-linux/chrome | grep 'not found' || echo 'All browser dependencies appear to be met.'
  echo '      -> Done.'


  echo
  echo '--- Environment Factory Reset Complete! ---'
  echo 'The virtual environment has been recreated inside the correct Nix environment.'
"

# Check the exit code of the nix-shell command
if [ $? -eq 0 ]; then
  echo "Script completed successfully."
else
  echo "An error occurred during the Nix environment setup. Please review the logs." >&2
fi
