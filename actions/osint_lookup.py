# actions/osint_lookup.py
"""
OSINT lookup module — PUBLIC DATA ONLY.
Legitimate open-source intelligence for IP / domain / network info.
No unauthorized access; only uses freely available public APIs.
"""
import urllib.request
import urllib.error
import json
import sys
from pathlib import Path

# Make it easy for the agent to find config if needed
_BASE = Path(__file__).resolve().parent.parent


def _fetch_json(url: str, timeout: int = 8) -> dict:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mark-L-OSINT/1.0 (public-lookup)"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def osint_lookup(
    parameters: dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    """
    Public OSINT lookup.
    parameters:
        target : str  (IP, domain, or search query)
        mode   : str  "ip" | "domain" | "search" (default "ip")
    Returns formatted public info string.
    """
    params = parameters or {}
    target = (params.get("target") or "").strip()
    mode = (params.get("mode") or "ip").lower().strip()

    if player:
        try:
            player.write_log(f"[OSINT:{mode}] target={target}")
        except Exception:
            pass

    if not target:
        return "[OSINT] Provide a target (IP / domain / query) and mode (ip / domain / search)."

    # ── IP lookup (free public API) ───────────────────────────────────────────
    if mode == "ip":
        # Basic validation: if it doesn't look like an IPv4/IPv6, try domain instead
        import ipaddress
        try:
            ipaddress.ip_address(target)
        except ValueError:
            # Not a pure IP — fall back to domain lookup
            mode = "domain"

    if mode == "ip":
        url = f"http://ip-api.com/json/{target}?fields=66846719"
        try:
            data = _fetch_json(url, timeout=6)
            if data.get("status") != "success":
                return f"[OSINT] IP lookup failed for {target}: {data.get('message', 'unknown error')}"
            lines = [
                f"OSINT — IP Lookup: {target}",
                f"  Country     : {data.get('country', 'N/A')} ({data.get('countryCode', 'N/A')})",
                f"  Region      : {data.get('regionName', 'N/A')} ({data.get('region', 'N/A')})",
                f"  City        : {data.get('city', 'N/A')}",
                f"  ZIP         : {data.get('zip', 'N/A')}",
                f"  Lat/Lon     : {data.get('lat', 'N/A')} / {data.get('lon', 'N/A')}",
                f"  ISP         : {data.get('isp', 'N/A')}",
                f"  Org         : {data.get('org', 'N/A')}",
                f"  AS          : {data.get('as', 'N/A')}",
                f"  Mobile / Proxy / Hosting: {data.get('mobile', 'N/A')} / {data.get('proxy', 'N/A')} / {data.get('hosting', 'N/A')}",
                "  Source: ip-api.com (public, free, no auth needed)",
            ]
            return "\n".join(lines)
        except urllib.error.URLError as e:
            return f"[OSINT] IP lookup network error for {target}: {e}"
        except Exception as e:
            return f"[OSINT] IP lookup error for {target}: {e}"

    # ── Domain lookup (RDAP — open standard, no API key) ───────────────────────
    if mode == "domain":
        # Sanitize: remove protocol / path
        clean = target.replace("https://", "").replace("http://", "").split("/")[0].split(":")[0]
        url = f"https://rdap.org/domain/{clean}"
        try:
            data = _fetch_json(url, timeout=8)
            lines = [
                f"OSINT — Domain Lookup: {clean}",
                f"  Handle      : {data.get('handle', 'N/A')}",
                f"  Status      : {', '.join(data.get('status', [])) or 'N/A'}",
                f"  Events      : {', '.join(data.get('events', [])) or 'N/A'}",
                "  Entities (registrar/tech/admin) — see raw RDAP if needed.",
                "  Source: rdap.org (open RDAP, public domain info)",
            ]
            # Try to pull registrar/creation/expiry from events
            events = data.get("events", [])
            for ev in events:
                ev_action = ev.get("eventAction", "")
                ev_date = ev.get("eventDate", "")
                if ev_action and ev_date:
                    lines.insert(-2, f"  {ev_action} : {ev_date}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return f"[OSINT] Domain {clean} not found in RDAP (may be unregistered or TLD unsupported)."
            return f"[OSINT] Domain lookup HTTP error for {clean}: {e}"
        except Exception as e:
            return f"[OSINT] Domain lookup error for {clean}: {e}"

    # ── Search / general OSINT query ───────────────────────────────────────────
    # ── Direct DDG search (no Gemini — avoids 429 quota) ───────────────────────
    def _ddg_direct(query: str, max_results: int = 6) -> list[str]:
        try:
            from duckduckgo_search import DDGS
            results = []
            with DDGS() as ddgs:
                for r in ddgs.text(query, max_results=max_results):
                    results.append(f"• {r.get('title','')} — {r.get('href','')}")
            return results
        except Exception:
            try:
                from ddgs import DDGS
                results = []
                with DDGS() as ddgs:
                    for r in ddgs.text(query, max_results=max_results):
                        results.append(f"• {r.get('title','')} — {r.get('href','')}")
                return results
            except Exception:
                return []

    if mode == "search":
        results = _ddg_direct(target, max_results=5)
        if results:
            return f"OSINT — Search: {target}\n" + "\n".join(results[:5])
        return f"[OSINT] No DDG results for '{target}'. Check connection or try different query."

    def _osint_search_fallback(q, t, label, resp, pl, sm):
        results = _ddg_direct(q, max_results=5)
        if results:
            return f"OSINT — {label}: {t}\n" + "\n".join(results[:5])
        return f"[OSINT] {label} search found nothing for '{t}'. Try different query."

    # ── Phone / Email / Person (public web search only) ────────────────────────
    if mode == "phone":
        query = f"phone number info {target} country operator"
        return _osint_search_fallback(query, target, "Phone", response, player, session_memory)

    if mode == "email":
        query = f"email {target} public info breach check"
        return _osint_search_fallback(query, target, "Email", response, player, session_memory)

    if mode in ("person", "name", "person_search"):
        query = f"{target} public info biography"
        return _osint_search_fallback(query, target, "Person", response, player, session_memory)

    return f"[OSINT] Unknown mode '{mode}'. Use: ip | domain | search | phone | email | person | name."
