"""
DenialGPT — Pre-Flight Deploy Check
=====================================

Run this before registering on Prompt Opinion to confirm all 6 prerequisites
are satisfied.

Usage (from project root):
    python scripts/deploy_check.py

Each check prints PASS or FAIL with actionable details.
Final line: READY or NOT READY (with specific fix instructions per failure).
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import socket
import subprocess
import sys
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure project root is on sys.path so tool imports work when called from
# the scripts/ subdirectory.
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Change working directory to project root so relative paths (prompts/, etc.)
# resolve correctly.
os.chdir(PROJECT_ROOT)

# ---------------------------------------------------------------------------
# Terminal colors (degrade gracefully on Windows without ANSI support)
# ---------------------------------------------------------------------------

_USE_COLOR = sys.stdout.isatty() and sys.platform != "win32"

def _green(s: str) -> str:
    return f"\033[32m{s}\033[0m" if _USE_COLOR else s

def _red(s: str) -> str:
    return f"\033[31m{s}\033[0m" if _USE_COLOR else s

def _yellow(s: str) -> str:
    return f"\033[33m{s}\033[0m" if _USE_COLOR else s

def _bold(s: str) -> str:
    return f"\033[1m{s}\033[0m" if _USE_COLOR else s


PASS = _green("PASS")
FAIL = _red("FAIL")
WARN = _yellow("WARN")


# ---------------------------------------------------------------------------
# Result accumulation
# ---------------------------------------------------------------------------

_results: list[tuple[str, bool, list[str]]] = []  # (name, passed, fix_lines)


def _report(name: str, passed: bool, details: str = "", fixes: list[str] | None = None):
    status = PASS if passed else FAIL
    line = f"  [{status}] {name}"
    if details:
        line += f"\n         {details}"
    print(line)
    _results.append((name, passed, fixes or []))


# ---------------------------------------------------------------------------
# Check 1 — Required environment variables
# ---------------------------------------------------------------------------

def check_env_vars():
    print(_bold("\nCheck 1 — Environment variables"))
    required = ["ANTHROPIC_API_KEY", "FHIR_BASE_URL", "DEV_ACCESS_TOKEN"]
    missing = [v for v in required if not os.getenv(v)]
    if missing:
        _report(
            "Environment variables",
            False,
            f"Missing: {', '.join(missing)}",
            fixes=[
                f"  Set the following in your .env file or shell:",
                *[f"    export {v}=<your_value>" for v in missing],
            ],
        )
    else:
        vals = {v: (os.getenv(v) or "")[:8] + "..." for v in required}
        _report(
            "Environment variables",
            True,
            "All 3 present: " + ", ".join(f"{k}={v}" for k, v in vals.items()),
        )


# ---------------------------------------------------------------------------
# Check 2 — Tool imports
# ---------------------------------------------------------------------------

def check_tool_imports():
    print(_bold("\nCheck 2 — Tool imports"))
    imports = [
        ("tools.analyze_denial", "run_analyze_denial"),
        ("tools.fetch_evidence", "run_fetch_clinical_evidence"),
        ("tools.gap_analysis", "run_gap_analysis"),
    ]
    all_ok = True
    for module, symbol in imports:
        try:
            mod = __import__(module, fromlist=[symbol])
            getattr(mod, symbol)
            print(f"    {_green('ok')}  {module}.{symbol}")
        except ImportError as e:
            print(f"    {_red('ERR')} {module}.{symbol} — ImportError: {e}")
            all_ok = False
        except AttributeError as e:
            print(f"    {_red('ERR')} {module}.{symbol} — AttributeError: {e}")
            all_ok = False

    _report(
        "Tool imports",
        all_ok,
        fixes=[
            "  ImportError usually means a missing package.",
            "  Run: pip install -r requirements.txt --break-system-packages",
        ] if not all_ok else [],
    )


# ---------------------------------------------------------------------------
# Helper: find a free TCP port
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# ---------------------------------------------------------------------------
# Check 3 — FastAPI /health endpoint
# ---------------------------------------------------------------------------

def check_fastapi_health():
    print(_bold("\nCheck 3 — FastAPI /health"))
    import httpx

    port = _free_port()
    proc = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "main:app",
             "--host", "127.0.0.1", "--port", str(port),
             "--log-level", "error"],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        # Wait up to 15s for the server to be ready
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            try:
                r = httpx.get(f"http://127.0.0.1:{port}/health", timeout=2)
                if r.status_code == 200:
                    data = r.json()
                    tools = data.get("tools_registered", [])
                    if data.get("status") == "ok" and len(tools) == 3:
                        _report(
                            "FastAPI /health",
                            True,
                            f"status=ok, tools={tools}",
                        )
                    else:
                        _report(
                            "FastAPI /health",
                            False,
                            f"Unexpected response: {data}",
                            fixes=["  /health response malformed — check main.py"],
                        )
                    return
            except (httpx.ConnectError, httpx.TimeoutException):
                time.sleep(0.5)

        _report(
            "FastAPI /health",
            False,
            f"Server did not start within 15s on port {port}",
            fixes=[
                "  Start the server manually to see the error:",
                "    uvicorn main:app --host 0.0.0.0 --port 8000",
            ],
        )

    except Exception as e:
        _report(
            "FastAPI /health",
            False,
            f"Exception: {e}",
            fixes=["  Ensure uvicorn is installed: pip install uvicorn[standard]"],
        )
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


# ---------------------------------------------------------------------------
# Check 4 — Agent card validity
# ---------------------------------------------------------------------------

def check_agent_card():
    print(_bold("\nCheck 4 — Agent card (.well-known/agent.json)"))
    card_path = PROJECT_ROOT / ".well-known" / "agent.json"
    if not card_path.exists():
        _report(
            "Agent card",
            False,
            f"{card_path} not found",
            fixes=[
                "  Create .well-known/agent.json with name, url, and tools fields.",
            ],
        )
        return

    try:
        data = json.loads(card_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        _report(
            "Agent card",
            False,
            f"Invalid JSON: {e}",
            fixes=["  Fix the JSON syntax in .well-known/agent.json"],
        )
        return

    missing_fields = [f for f in ("name", "tools", "url") if f not in data]
    if missing_fields:
        _report(
            "Agent card",
            False,
            f"Missing fields: {missing_fields}",
            fixes=[f"  Add these fields to .well-known/agent.json: {missing_fields}"],
        )
        return

    tools = data.get("tools", [])
    if len(tools) != 3:
        _report(
            "Agent card",
            False,
            f"Expected 3 tools, found {len(tools)}",
            fixes=["  Add all 3 tool definitions to .well-known/agent.json"],
        )
        return

    url = data.get("url", "")
    if "YOUR_RAILWAY_URL" in url:
        # Warn but don't fail — user may not have deployed yet
        print(
            f"    {WARN} url still contains placeholder 'YOUR_RAILWAY_URL'.\n"
            "         Update with your Railway URL before registering on Prompt Opinion."
        )

    _report(
        "Agent card",
        True,
        f"name={data['name']!r}, {len(tools)} tools, url={url!r}",
    )


# ---------------------------------------------------------------------------
# Check 5 — FHIR connectivity
# ---------------------------------------------------------------------------

def check_fhir_connectivity():
    print(_bold("\nCheck 5 — FHIR connectivity"))
    import httpx

    base_url = (os.getenv("FHIR_BASE_URL") or "").rstrip("/")
    token = os.getenv("DEV_ACCESS_TOKEN", "")

    if not base_url:
        _report(
            "FHIR connectivity",
            False,
            "FHIR_BASE_URL is not set",
            fixes=["  Set FHIR_BASE_URL=<your_prompt_opinion_fhir_url> in .env"],
        )
        return

    metadata_url = f"{base_url}/metadata"
    try:
        r = httpx.get(
            metadata_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
            follow_redirects=True,
        )
        if r.status_code != 200:
            _report(
                "FHIR connectivity",
                False,
                f"HTTP {r.status_code} from {metadata_url}",
                fixes=[
                    f"  Expected 200, got {r.status_code}.",
                    "  Check FHIR_BASE_URL and DEV_ACCESS_TOKEN are correct.",
                ],
            )
            return

        data = r.json()
        if data.get("resourceType") != "CapabilityStatement":
            _report(
                "FHIR connectivity",
                False,
                f"Expected resourceType=CapabilityStatement, got {data.get('resourceType')}",
                fixes=["  FHIR server returned unexpected response — verify FHIR_BASE_URL"],
            )
        else:
            _report(
                "FHIR connectivity",
                True,
                f"CapabilityStatement received from {base_url}",
            )
    except httpx.TimeoutException:
        _report(
            "FHIR connectivity",
            False,
            f"Timeout connecting to {metadata_url}",
            fixes=["  Check network access and that FHIR_BASE_URL is reachable from this machine."],
        )
    except Exception as e:
        _report(
            "FHIR connectivity",
            False,
            f"Exception: {e}",
            fixes=["  Verify FHIR_BASE_URL is a valid HTTPS URL."],
        )


# ---------------------------------------------------------------------------
# Check 6 — Anthropic API reachable
# ---------------------------------------------------------------------------

def check_anthropic_api():
    print(_bold("\nCheck 6 — Anthropic API reachability"))
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        _report(
            "Anthropic API",
            False,
            "ANTHROPIC_API_KEY not set",
            fixes=["  Set ANTHROPIC_API_KEY in .env or environment."],
        )
        return

    try:
        import anthropic

        async def _ping():
            client = anthropic.AsyncAnthropic(api_key=api_key)
            response = await client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=5,
                messages=[{"role": "user", "content": "ping"}],
            )
            return response

        response = asyncio.run(_ping())
        _report(
            "Anthropic API",
            True,
            f"Response received (stop_reason={response.stop_reason!r})",
        )
    except Exception as e:
        _report(
            "Anthropic API",
            False,
            f"Exception: {e}",
            fixes=[
                "  Verify ANTHROPIC_API_KEY is valid.",
                "  Check network: curl https://api.anthropic.com",
            ],
        )


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

def print_summary():
    passed = [r for r in _results if r[1]]
    failed = [r for r in _results if not r[1]]

    print("\n" + "=" * 60)
    print(_bold(f"  {len(passed)}/6 checks passed"))

    if not failed:
        print(_green(_bold("\n  ✓ READY — safe to deploy and register on Prompt Opinion\n")))
    else:
        print(_red(_bold(f"\n  ✗ NOT READY — {len(failed)} check(s) failed\n")))
        print("  Fix the following before deploying:\n")
        for name, _, fixes in _results:
            if fixes:
                print(f"  ── {name}:")
                for fix in fixes:
                    print(fix)
                print()

    print("=" * 60 + "\n")
    return len(failed) == 0


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(_bold("\nDenialGPT Pre-Flight Deploy Check"))
    print("=" * 60)

    check_env_vars()
    check_tool_imports()
    check_fastapi_health()
    check_agent_card()
    check_fhir_connectivity()
    check_anthropic_api()

    ready = print_summary()
    sys.exit(0 if ready else 1)
