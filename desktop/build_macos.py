"""Build a dedicated TradingAgents app using a locally installed Electron shell."""

import argparse
import hashlib
import json
import plistlib
import shutil
import struct
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def archive_files(files):
    header = {"files": {}}
    payloads = []
    offset = 0
    for name, raw in sorted(files.items()):
        node = header
        parts = name.split("/")
        for part in parts[:-1]:
            node = node["files"].setdefault(part, {"files": {}})
        block = 4194304
        node["files"][parts[-1]] = {
            "size": len(raw),
            "offset": str(offset),
            "integrity": {
                "algorithm": "SHA256",
                "hash": hashlib.sha256(raw).hexdigest(),
                "blockSize": block,
                "blocks": [
                    hashlib.sha256(raw[i : i + block]).hexdigest()
                    for i in range(0, len(raw), block)
                ],
            },
        }
        payloads.append(raw)
        offset += len(raw)
    raw_header = json.dumps(header, separators=(",", ":")).encode()
    payload = struct.pack("<I", len(raw_header)) + raw_header
    payload += b"\0" * (-len(payload) % 4)
    packed = struct.pack("<I", len(payload)) + payload
    return struct.pack("<II", 4, len(packed)) + packed + b"".join(payloads), hashlib.sha256(
        raw_header
    ).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shell", type=Path, default=Path("/Applications/AgentsRoom.app"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--icon", type=Path, default=ROOT / "desktop/assets/trading-agents.icns")
    args = parser.parse_args()
    if args.output.exists():
        raise SystemExit("Output already exists; choose a fresh staging directory.")
    if args.output.resolve().is_relative_to(args.shell.resolve()):
        raise SystemExit("The original app must not be modified.")
    shutil.copytree(args.shell, args.output, symlinks=True)
    resources = args.output / "Contents/Resources"
    notices = {
        p.name: p.read_bytes()
        for p in resources.iterdir()
        if p.is_file()
        and any(word in p.name.lower() for word in ("license", "notices", "copyright"))
    }
    shutil.rmtree(resources)
    resources.mkdir()
    for name, raw in notices.items():
        (resources / name).write_bytes(raw)
    include = [
        "main.cjs",
        "preload.cjs",
        "index.html",
        "app.css",
        "app.js",
        "paper-market.js",
        "vendor/lightweight-charts/lightweight-charts.js",
        "vendor/lightweight-charts/LICENSE",
        "vendor/lightweight-charts/NOTICE",
        "vendor/lightweight-charts/provenance.json",
        "assets/logo.png",
    ]
    files = {name: (ROOT / "desktop" / name).read_bytes() for name in include}
    files["package.json"] = json.dumps(
        {
            "name": "trading-agents",
            "productName": "Trading Agents",
            "version": "0.7.1",
            "main": "main.cjs",
        }
    ).encode()
    packed, digest = archive_files(files)
    (resources / "app.asar").write_bytes(packed)
    shutil.copy2(args.icon, resources / "trading-agents.icns")
    if __package__:
        from desktop.install_engine import install
    else:
        from install_engine import install

    (resources / "trading-desktop.json").write_text(json.dumps(install(ROOT)))
    plist_path = args.output / "Contents/Info.plist"
    plist = plistlib.loads(plist_path.read_bytes())
    old = plist["CFBundleExecutable"]
    executable = args.output / "Contents/MacOS"
    (executable / old).rename(executable / "Trading Agents")
    plist.update(
        CFBundleName="Trading Agents",
        CFBundleDisplayName="Trading Agents",
        CFBundleExecutable="Trading Agents",
        CFBundleIdentifier="dev.tradingagents.desktop",
        CFBundleIconFile="trading-agents.icns",
        CFBundleShortVersionString="0.7.1",
        CFBundleVersion="0.7.1",
        LSApplicationCategoryType="public.app-category.finance",
    )
    plist.pop("CFBundleURLTypes", None)
    plist.pop("CFBundleDocumentTypes", None)
    plist.pop("AgentsRoomCachePatch", None)
    plist["ElectronAsarIntegrity"] = {"Resources/app.asar": {"algorithm": "SHA256", "hash": digest}}
    plist_path.write_bytes(plistlib.dumps(plist))
    for helper in (args.output / "Contents/Frameworks").glob("AgentsRoom Helper*.app"):
        info = helper / "Contents/Info.plist"
        value = plistlib.loads(info.read_bytes())
        old = value["CFBundleExecutable"]
        new = old.replace("AgentsRoom", "Trading Agents", 1)
        (helper / "Contents/MacOS" / old).rename(helper / "Contents/MacOS" / new)
        value.update(CFBundleExecutable=new, CFBundleName=new)
        value["CFBundleIdentifier"] = value.get("CFBundleIdentifier", "").replace(
            "com.agentsroom.desktop", "dev.tradingagents.desktop"
        )
        info.write_bytes(plistlib.dumps(value))
        helper.rename(helper.with_name(helper.name.replace("AgentsRoom", "Trading Agents", 1)))
    subprocess.run(["xattr", "-cr", str(args.output)], check=True)
    subprocess.run(
        [
            "codesign",
            "--force",
            "--deep",
            "--sign",
            "-",
            "--preserve-metadata=entitlements,flags,runtime",
            str(args.output),
        ],
        check=True,
    )
    subprocess.run(["codesign", "--verify", "--deep", "--strict", str(args.output)], check=True)
    print(json.dumps({"app": str(args.output), "asar_files": len(files), "repo": str(ROOT)}))


if __name__ == "__main__":
    main()
