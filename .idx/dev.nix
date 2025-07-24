# .idx/dev.nix
# This is the official configuration file for the Firebase IDX environment.
# The IDX platform reads this file on startup to provision the workspace
# with the specified system-level Nix packages.

{ pkgs, ... }: {
  # The list of packages to make available in the environment's PATH.
  packages = [
    # 1. Base Python environment
    pkgs.python3

    # 2. System-level dependencies required by Playwright's browsers.
    pkgs.at-spi2-atk
    pkgs.cairo
    pkgs.cups
    pkgs.dbus
    pkgs.expat
    pkgs.gdk-pixbuf
    pkgs.glib
    pkgs.gtk3
    pkgs.libdrm
    pkgs.libepoxy
    pkgs.libglvnd
    pkgs.libxkbcommon
    pkgs.mesa
    pkgs.nss
    pkgs.pango
    pkgs.pipewire
    pkgs.udev

    # 3. Corrected X.Org (X11) libraries, which are under the 'xorg' attribute set.
    pkgs.xorg.libX11
    pkgs.xorg.libXScrnSaver
    pkgs.xorg.libXcomposite
    pkgs.xorg.libXcursor
    pkgs.xorg.libXdamage
    pkgs.xorg.libXext
    pkgs.xorg.libXfixes
    pkgs.xorg.libXi
    pkgs.xorg.libXrandr
    pkgs.xorg.libXrender
    pkgs.xorg.libXtst
  ];
}
