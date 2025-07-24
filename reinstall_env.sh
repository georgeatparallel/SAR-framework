#!/bin/bash
#
# This script performs a factory reinstall of the Python virtual environment.
# This version corrects the nix-shell command to properly evaluate the
# .idx/dev.nix configuration file by passing the required 'pkgs' argument.

echo "--- Starting IDX Environment Factory Reset ---"
echo "This will build the environment from '.idx/dev.nix' and then set up Python."

# We use --arg to explicitly pass the nixpkgs set as the 'pkgs' argument
# to the function inside our .idx/dev.nix file. This resolves the error.
nix-shell .idx/dev.nix --arg pkgs "import <nixpkgs> {}" --run "
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
