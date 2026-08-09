# actions/install_on_phone.py
"""
Install apps on a connected Android phone (authorized use only).
Requires either:
  1) ADB over WiFi (phone USB-debug + adb tcpip 5555 + adb connect IP:5555)
  2) Or opening an APK / store URL in the phone's browser via remote dashboard
"""
import subprocess
import os


def install_on_phone(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    app_source = (params.get("app_source") or params.get("url") or params.get("apk_path") or "").strip()
    method = (params.get("method") or "adb").lower().strip()  # adb | browser

    if player:
        try:
            player.write_log(f"[InstallPhone] method={method} source={app_source[:60]}")
        except Exception:
            pass

    lines = [
        "PHONE INSTALLED / INSTALL HELPER — AUTHORIZED USE ONLY",
        "Only install apps YOU own or have rights to install.",
        "Requires phone to be connected and authorized (ADB / browser access).",
    ]

    if not app_source:
        lines.append("\nProvide app_source (URL / local APK path) or use browser method to open a link.")
        return "\n".join(lines)

    if method == "adb":
        # Try ADB over WiFi / USB
        try:
            # Check if adb is available
            ret, out, err = subprocess.getstatusoutput("adb devices")
            if ret != 0 or "daemon" in err.lower() or not out.strip():
                lines.append("\n[ADB] adb not available or no device connected.")
                lines.append("  Setup: enable USB debugging → adb tcpip 5555 → adb connect PHONE_IP:5555")
                lines.append("  Then call again with method=adb and apk_path=...")
            else:
                # Try install
                cmd = f"adb install \"{app_source}\""
                ret2, out2 = subprocess.getstatusoutput(cmd)
                if ret2 == 0:
                    lines.append(f"\n[ADB] Installed successfully: {app_source}")
                    lines.append(out2[:200])
                else:
                    lines.append(f"\n[ADB] Install failed ({ret2}): {out2[:200]} {err[:200] if 'err' in locals() else ''}")
        except Exception as e:
            lines.append(f"\n[ADB] Exception: {e}")

    elif method == "browser":
        # Open URL on remote phone browser (if dashboard / session supports it)
        # For now, return instruction; can be extended to send URL via remote session
        lines.append(f"\n[BROWSER] Open this URL on connected phone: {app_source}")
        lines.append("  If using JARVIS remote dashboard, open link directly on phone.")

    else:
        lines.append(f"\nUnknown method '{method}'. Use: adb | browser")

    lines.append("\nDISCLAIMER: Only install apps you have permission to install. Unauthorized installation violates device policies.")
    return "\n".join(lines)
