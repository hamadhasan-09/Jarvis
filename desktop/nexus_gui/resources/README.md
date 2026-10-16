# Nexus application icons

The authoritative assets are in the repository's `icons/` directory:

- `nexus.ico`: Windows icon with transparent 16, 24, 32, 48, 64, 128, and 256 pixel representations. Embedded in the executable by `nexus.rc.in` and in Qt resources for application/window icons.
- `nexus-icon-512.png` and `nexus-icon-1024.png`: transparent, high-resolution Qt icon representations embedded alongside the ICO.
- `nexus-icon-256.png`: original standalone PNG, retained unchanged; its size is already represented in the ICO.
- `nexus.icns`: original macOS asset, retained unchanged and unused by the Windows build.

CMake resolves the icon paths when configuring the Windows resource file. Both the Windows shell icon and Qt icon images are embedded in `Nexus.exe`; deploying the executable does not require copying loose icon assets. No conversion or resampling of the supplied assets is performed during the build.
