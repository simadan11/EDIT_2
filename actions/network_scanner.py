# actions/network_scanner.py
"""
AUTHORIZED NETWORK SCANNER — OWN NETWORK ONLY.
Scans nearby WiFi access points and Bluetooth Low Energy (BLE) devices
that emit signals (radio, beacon, SSID).  Intended ONLY for:
  • Auditing your own home/office WiFi and BLE peripherals
  • Inventory of devices YOU own or have explicit permission to scan
DO NOT use to track people, neighbors, or devices without authorization.
This tool relies on standard Linux/Windows system utilities.
"""
import subprocess
import shlex
import sys
import os
from pathlib import Path

# Small helper to try common command paths
_COMMANDS = {
    "nmcli": ["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL,SECURITY,MODE", "dev", "wifi", "list"],
    "bluetoothctl": ["bluetoothctl", "scan", "on"],
    "iwlist": ["iwlist", "scanning"],
}


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except FileNotFoundError:
        return 127, "", f"Command not found: {cmd[0]}"
    except subprocess.TimeoutExpired:
        return -1, "", "Timeout"
    except Exception as e:
        return -1, "", str(e)


def network_scanner(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Nearby device / signal scanner (authorized use only).
    parameters:
        type : str  "wifi" | "ble" | "all" (default "all")
    Returns list of discovered networks / BLE devices with disclaimer.
    """
    params = parameters or {}
    scan_type = (params.get("type") or "all").lower().strip()

    if player:
        try:
            player.write_log(f"[NetworkScan] type={scan_type}")
        except Exception:
            pass

    lines = [
        "=" * 60,
        "NETWORK SCANNER — AUTHORIZED USE ONLY",
        "Only for auditing YOUR OWN networks/devices.",
        "Do NOT use to track others or invade privacy.",
        "=" * 60,
    ]

    # ── WiFi scanning ─────────────────────────────────────────────────────────
    if scan_type in ("wifi", "all"):
        lines.append("\n[WIFI] Nearby access points / emitting SSIDs:")
        # Try nmcli first (works on most Linux with NetworkManager)
        ret, out, err = _run(_COMMANDS["nmcli"], timeout=8)
        if ret == 0 and out:
            # Parse simple table-like output from nmcli -t
            rows = []
            for raw_line in out.splitlines():
                raw_line = raw_line.strip()
                if not raw_line or raw_line.startswith("BSSID="):
                    continue
                # nmcli -t gives colon-separated fields sometimes; handle both
                # We'll just show the raw clean line if it contains signal/SSID
                if "SSID" in raw_line or "BSSID" in raw_line or (len(raw_line) > 10 and ";" not in raw_line and ":" in raw_line):
                    # Try to pretty-print by splitting on known delimiters
                    # Actually nmcli -t uses colon separator for fields?
                    # It often uses colon when -t is used, but let's just show clean lines
                    rows.append("  • " + raw_line.replace(":", " | ").replace("\\", " "))
            # If we got nothing structured, just show non-empty lines
            if not rows:
                for line in out.splitlines():
                    line = line.strip()
                    if line and not line.startswith("BSSID="):
                        rows.append("  • " + line.replace(":", " | "))
            if rows:
                lines.extend(rows[:15])  # cap so answer isn't huge
            else:
                lines.append("  (nmcli returned data but format unclear — shown raw)")
                lines.extend(["  • " + l for l in out.splitlines()[:10] if l.strip()])
        else:
            # Try iwlist (needs root / proper group often)
            ret2, out2, err2 = _run(_COMMANDS["iwlist"], timeout=8)
            if ret2 == 0 and out2:
                # iwlist scanning output is long; extract Cell lines
                cells = []
                for line in out2.splitlines():
                    stripped = line.strip()
                    if stripped.startswith("Cell ") or stripped.startswith("ESSID:") or stripped.startswith("Address: ") or stripped.startswith("Signal level=") or stripped.startswith("Encryption key:"):
                        cells.append("  • " + stripped)
                if cells:
                    lines.extend(cells[:20])
                else:
                    lines.append("  iwlist found networks (raw output suppressed for brevity).")
            else:
                lines.append(f"  [WIFI] nmcli unavailable ({err or 'not installed'}). iwlist also unavailable ({err2 or 'not installed/needs root'}).")
                lines.append("  To scan WiFi you need: NetworkManager (nmcli) or root + iwlist.")
        lines.append("  Note: WiFi scanning only reads beacons from nearby APs (public info).")

    # ── BLE scanning ───────────────────────────────────────────────────────────
    if scan_type in ("ble", "all"):
        lines.append("\n[BLE] Nearby Bluetooth Low Energy devices / beacons:")
        # Try bluetoothctl scan on (needs bluetooth service running, often user-level)
        ret, out, err = _run(["bluetoothctl", "scan", "on"], timeout=6)
        # bluetoothctl scan on just starts scanning; we then ask for devices
        if ret == 0 or ret == 1:  # sometimes exits 1 after starting
            # Give it a moment then ask for devices
            import time
            time.sleep(0.8)
            ret2, out2, err2 = _run(["bluetoothctl", "devices"], timeout=4)
            if ret2 == 0 and out2:
                for line in out2.splitlines():
                    line = line.strip()
                    if line.startswith("Device "):
                        lines.append("  • BLE/BT " + line.replace("Device ", "").replace(" ", " | "))
                if not any("BLE/BT" in l for l in lines[-10:] if l.startswith("  •")):
                    lines.append("  (bluetoothctl scan active — no paired devices shown; run 'scan on' longer for discoveries)")
            else:
                # Try bleak if installed (Python library)
                try:
                    from bleak import BleakScanner
                    lines.append("  Trying bleak (Python BLE library)...")
                    import asyncio
                    async def _bleak_scan():
                        devices = await BleakScanner.discover(timeout=3.0)
                        return devices
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    found = loop.run_until_complete(_bleak_scan())
                    loop.close()
                    if found:
                        for d in found[:15]:
                            lines.append(f"  • {d.name or 'Unknown'} | {d.address} | RSSI={d.rssi if hasattr(d,'rssi') else 'N/A'}")
                    else:
                        lines.append("  (bleak: no BLE devices discovered in 3s)")
                except Exception as e:
                    lines.append(f"  [BLE] bluetoothctl/bleak unavailable or needs setup: {e}")
                    lines.append("  To scan BLE: ensure bluetooth service is running; install bleak (pip install bleak) for Python scanning.")
        else:
            # Try bleak directly even if bluetoothctl missing
            try:
                from bleak import BleakScanner
                lines.append("  Trying bleak (direct Python BLE scan)...")
                import asyncio
                async def _bleak_scan():
                    devices = await BleakScanner.discover(timeout=3.0)
                    return devices
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                found = loop.run_until_complete(_bleak_scan())
                loop.close()
                if found:
                    for d in found[:15]:
                        lines.append(f"  • {d.name or 'Unknown'} | {d.address} | RSSI={d.rssi if hasattr(d,'rssi') else 'N/A'}")
                else:
                    lines.append("  (bleak: no BLE devices discovered in 3s)")
            except Exception as e:
                lines.append(f"  [BLE] No BLE scanner available (bluetoothctl/bleak): {e}")
        lines.append("  Note: BLE beacons expose only device UUID/name/RSSI — no private content.")

    # ── General / All ─────────────────────────────────────────────────────────
    if scan_type == "all":
        pass  # already handled both blocks above
    elif scan_type not in ("wifi", "ble", "all"):
        lines.append(f"\n[ERROR] Unknown scan type '{scan_type}'. Use: wifi | ble | all")
        return "\n".join(lines)

    # Disclaimer footer
    lines.append("\n" + "=" * 60)
    lines.append("DISCLAIMER")
    lines.append("This tool scans PUBLIC radio emissions (SSIDs / BLE beacons) near you.")
    lines.append("Use ONLY on networks/devices you OWN or have EXPLICIT PERMISSION to audit.")
    lines.append("Unauthorized tracking of people or devices via RF is illegal/private.")
    lines.append("=" * 60)

    if player:
        try:
            player.write_log("[NetworkScan] scan complete — disclaimer shown.")
        except Exception:
            pass

    return "\n".join(lines)
