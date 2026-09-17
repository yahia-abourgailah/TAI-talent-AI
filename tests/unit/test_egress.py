"""CR-01, OBJ-03: nothing leaves except to the places we have named (docs/security/EGRESS.md).

Every module that imports something able to open a network connection is listed here, with where
it connects and what it carries. A new module that does, or a listed module that picks up another
network library, fails this test until someone adds it here and to EGRESS.md, in a reviewed change.

The database is not listed: it is inside the deployment, and every module reaches it through
SQLAlchemy, which this test does not count as leaving.
"""

import ast
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"

# Import roots that can open a connection to another machine.
NETWORK_LIBRARIES = frozenset(
    {
        "aiohttp",
        "boto3",
        "botocore",
        "ftplib",
        "http.client",
        "httpx",
        "openai",
        "anthropic",
        "paramiko",
        "redis",
        "requests",
        "smtplib",
        "socket",
        "urllib.request",
        "urllib3",
        "websocket",
        "websockets",
        "jwt",  # PyJWKClient fetches signing keys; see auth/oidc.py
    }
)

OBJECT_STORAGE = "object storage (original CVs, inside our network)"
OCR = "the OCR host (CVs, on our own host)"
CRM = "the CRM webhook (ids and codes only)"
REDIS = "Redis (queue and limits, no candidate data)"
IDENTITY = "the company identity provider (staff sign-in, no candidate)"
ALERTS = "the alert chat webhook (counts and job kinds only)"
OWN_API = "our own API, from an operator's tool"
NOWHERE = "nothing: signs development tokens locally"

# module (relative to src) -> {library: destination}
ALLOWED: dict[str, dict[str, str]] = {
    "importer/blobs.py": {"boto3": OBJECT_STORAGE, "botocore": OBJECT_STORAGE},
    "infra/probes.py": {"boto3": OBJECT_STORAGE, "botocore": OBJECT_STORAGE, "redis": REDIS},
    "infra/dev_storage.py": {"botocore": OBJECT_STORAGE},
    "intake/ocr_http.py": {"httpx": OCR},
    "integrations/webhooks.py": {"httpx": CRM},
    "auth/oidc.py": {"urllib.request": IDENTITY, "jwt": IDENTITY},
    "auth/dev_identity.py": {"jwt": NOWHERE},
    "ops/watch.py": {"httpx": ALERTS},
    "pipeline/load_openings.py": {"urllib.request": OWN_API},
}

# Packages in pyproject that can reach the network, and why each is there.
ALLOWED_DEPENDENCIES = {
    "boto3": OBJECT_STORAGE,
    "httpx": f"{OCR}; {CRM}; {ALERTS}",
    "pyjwt": IDENTITY,
    "redis": REDIS,
}
NETWORK_PACKAGES = {
    "aiohttp",
    "boto3",
    "httpx",
    "openai",
    "anthropic",
    "pyjwt",
    "redis",
    "requests",
    "urllib3",
    "websockets",
    "paramiko",
    "langchain",
}


def _network_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module] + [f"{node.module}.{alias.name}" for alias in node.names]
        for name in names:
            for library in NETWORK_LIBRARIES:
                if name == library or name.startswith(library + "."):
                    found.add(library)
    return found


def _modules() -> dict[str, set[str]]:
    return {
        path.relative_to(SRC).as_posix(): imports
        for path in sorted(SRC.rglob("*.py"))
        if (imports := _network_imports(path))
    }


def test_only_the_named_modules_can_reach_the_network():
    found = _modules()
    unexpected = {
        module: sorted(libraries - set(ALLOWED.get(module, {})))
        for module, libraries in found.items()
        if libraries - set(ALLOWED.get(module, {}))
    }
    assert not unexpected, (
        "New outbound network code. Say where it connects and what it carries, in this test's "
        f"ALLOWED and in docs/security/EGRESS.md, or remove it (CR-01): {unexpected}"
    )


def test_the_allowlist_has_no_stale_entries():
    found = _modules()
    stale = {
        module: sorted(set(libraries) - found.get(module, set()))
        for module, libraries in ALLOWED.items()
        if set(libraries) - found.get(module, set())
    }
    assert not stale, f"Listed but no longer imported; remove from ALLOWED and EGRESS.md: {stale}"


def test_no_new_network_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = project["project"]["dependencies"]
    names = {
        dependency.split(";")[0]
        .split("[")[0]
        .split("=")[0]
        .split(">")[0]
        .split("<")[0]
        .split("~")[0]
        .split("!")[0]
        .strip()
        .lower()
        for dependency in declared
    }
    unexpected = sorted((names & NETWORK_PACKAGES) - set(ALLOWED_DEPENDENCIES))
    assert not unexpected, (
        f"A dependency that can reach the network, not in EGRESS.md: {unexpected}"
    )


def test_the_written_list_names_every_destination():
    written = (ROOT / "docs" / "security" / "EGRESS.md").read_text(encoding="utf-8")
    for name in (
        "Object storage",
        "OCR host",
        "CRM webhook",
        "Redis",
        "identity provider",
        "alert chat webhook",
    ):
        assert name in written, name
    for module in ALLOWED:
        assert f"src/{module}" in written, module


def test_the_check_sees_a_new_call(tmp_path):
    sneaky = tmp_path / "sneaky.py"
    sneaky.write_text("import requests\nfrom urllib.request import urlopen\n", encoding="utf-8")
    assert _network_imports(sneaky) == {"requests", "urllib.request"}
