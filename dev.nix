# dev.nix
# This file defines the development environment for the project,
# including the Python version and necessary system libraries for Playwright.
{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  # The buildInputs attribute lists all the packages available in the shell.
  buildInputs = [
    # Base Python environment
    pkgs.python3

    # System-level dependencies required by Playwright's headless browsers.
    # These are necessary for Chromium, Firefox, and WebKit to run.
    pkgs.at-spi2-atk
    pkgs.cairo
    pkgs.cups
    pkgs.dbus
    pkgs.expat
    pkgs.gdk-pixbuf
    pkgs.glib
    pkgs.gtk3
    pkgs.libX11
    pkgs.libXScrnSaver
    pkgs.libXcomposite
    pkgs.libXcursor
    pkgs.libXdamage
    pkgs.libXext
    pkgs.libXfixes
    pkgs.libXi
    pkgs.libXrandr
    pkgs.libXrender
    pkgs.libXtst
    pkgs.libdrm
    pkgs.libepoxy
    pkgs.libglvnd
    pkgs.libxkbcommon
    pkgs.mesa
    pkgs.nss
    pkgs.pango
    pkgs.pipewire
    pkgs.udev
  ];
}
