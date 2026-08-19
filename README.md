# BIOSArchive

An archive of BIOS and firmware images collected for preservation and future use.

The goal is simple: keep firmware for hardware that may be difficult to find, recover, or download in the future.

The archive currently consists of images personally collected and verified by the maintainer.

## Structure

```text id="m4xq5j"
BIOSArchive/
├── components/
├── tools/
│   └── catalog-browser/    # GUI catalog viewer (PyQt6)
├── vendors/
│   └── Vendor/
│       └── Device-Model/
│           ├── dump-01.bin
│           └── metadata.yml
└── README.md
```

Each device has its own directory. Firmware images are kept together with metadata describing their origin, contents, and known state.

## Image naming

Images use a simple local identifier:

```text id="u8bqfw"
dump-01.bin
dump-02.bin
dump-03.bin
```

The directory identifies the hardware. `metadata.yml` identifies the image.

This keeps filenames short while allowing multiple images of the same device to coexist.

## Metadata

Metadata may include:

* exact hardware model
* board revision
* CPU / SoC
* firmware version
* image type
* image size
* dump method
* source
* NVRAM state
* testing status
* SHA-256 checksum
* notes

The metadata format is intentionally flexible. Not every image will have the same amount of available information.

## Tools

### Catalog Browser

A PyQt6 GUI tool for browsing the archive visually.

```
tools/catalog-browser/main.py
```

**Requirements:** Python 3.10+, PyQt6, PyYAML.

**Run:**

```bash
python3 tools/catalog-browser/main.py
```

The browser reads all `metadata.yml` files recursively and builds an interactive catalog. Two view modes are available:

- **By Vendor** — groups devices under their board vendor, then by CPU family
- **By CPU Family** — groups by CPU architecture first, showing all boards sharing the same SoC or CPU family together regardless of vendor

A filter bar lets you narrow the tree by CPU vendor, CPU family, CPU model, and verification status. Filters cascade: selecting a CPU family limits the model list to that family only. A text search field additionally filters by name within the current view.

Clicking any node in the tree shows its full metadata in the detail panel on the right, including SHA-256 checksums (with a one-click copy button), source information, NVRAM state, and verification notes.

## Current archive

The images currently in this archive were personally obtained and, where stated, tested on the corresponding hardware.

No image is assumed to be working merely because it exists in the archive. The metadata should make its known state clear.

## Disclaimer

Firmware images may contain device-specific data such as serial numbers, MAC addresses, UUIDs, configuration data, or other platform-specific information.

Use archived firmware at your own risk.

---

**Preserve the firmware before it disappears.**
