"""User-owned VCBS Home management (~/.vcbs).

Supports long-lived personal state outside the VCBS repository:
- personal development profile / preferences
- reusable user capabilities and component definitions
- reusable verified Experience
"""

from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
import yaml

from composer.core import ROOT, load_yaml, validate


def _utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def get_vcbs_home(custom_path=None) -> Path:
    """Resolve the user-owned VCBS Home directory.

    Order of precedence:
    1. Explicit custom_path argument
    2. VCBS_HOME environment variable
    3. Platform-resolved ~/.vcbs
    """
    if custom_path:
        return Path(custom_path).resolve()
    env_home = os.environ.get("VCBS_HOME")
    if env_home:
        return Path(env_home).resolve()
    return (Path.home() / ".vcbs").resolve()


def init_vcbs_home(home_path=None, seed_root=ROOT) -> Path:
    """Initialize standard VCBS Home layout and seed profile if missing."""
    home = get_vcbs_home(home_path)
    home.mkdir(parents=True, exist_ok=True)
    (home / "capabilities").mkdir(parents=True, exist_ok=True)
    (home / "components").mkdir(parents=True, exist_ok=True)
    (home / "experiences" / "verified").mkdir(parents=True, exist_ok=True)
    (home / "experiences" / "candidate").mkdir(parents=True, exist_ok=True)
    (home / "experiences" / "rejected").mkdir(parents=True, exist_ok=True)

    profile_file = home / "profile.yaml"
    alt_profile = home / "profiles" / "personal.yaml"
    if not profile_file.is_file() and not alt_profile.is_file():
        seed_profile = Path(seed_root) / "profiles" / "personal.yaml"
        if seed_profile.is_file():
            shutil.copy2(seed_profile, profile_file)
        else:
            default_profile = {
                "id": "personal",
                "name": "Personal development profile",
                "description": "Explicitly stated preferences for AI-assisted development.",
                "preferences": {
                    "testing_approach": "targeted",
                    "additional": {
                        "prefer_simple_solutions": "true",
                        "avoid_unnecessary_abstraction": "true",
                    },
                },
                "default_capability_ids": [],
                "excluded_capability_ids": [],
                "reusable_experience_ids": [],
                "created_at": _utc(),
                "updated_at": _utc(),
            }
            profile_file.write_text(yaml.safe_dump(default_profile, sort_keys=False), encoding="utf-8")
    return home


def load_profile(home_path=None, root=ROOT) -> dict:
    """Load and validate personal profile from VCBS Home, with fallback to repository seed."""
    home = get_vcbs_home(home_path)
    candidate_paths = [
        home / "profile.yaml",
        home / "profile.json",
        home / "profiles" / "personal.yaml",
    ]
    for path in candidate_paths:
        if path.is_file():
            data = load_yaml(path)
            validate(data, "profile.schema.json", root)
            return data

    seed_path = Path(root) / "profiles" / "personal.yaml"
    if seed_path.is_file():
        data = load_yaml(seed_path)
        validate(data, "profile.schema.json", root)
        return data

    raise FileNotFoundError(f"Personal profile not found in {home} or {seed_path}")


def save_profile(profile_data: dict, home_path=None, root=ROOT) -> Path:
    """Save and validate personal profile into VCBS Home."""
    home = get_vcbs_home(home_path)
    home.mkdir(parents=True, exist_ok=True)
    validate(profile_data, "profile.schema.json", root)
    target = home / "profile.yaml"
    target.write_text(yaml.safe_dump(profile_data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return target


def load_user_capabilities(home_path=None) -> list[dict]:
    """Load user-defined capabilities from VCBS Home capabilities directory."""
    home = get_vcbs_home(home_path)
    cap_dir = home / "capabilities"
    if not cap_dir.is_dir():
        return []

    records = []
    # If a catalog.yaml exists in home capabilities, check it
    catalog_path = cap_dir / "catalog.yaml"
    listed_paths = []
    if catalog_path.is_file():
        try:
            catalog = load_yaml(catalog_path)
            for entry in catalog.get("capabilities", []):
                listed_paths.append(cap_dir / entry)
        except Exception:
            pass

    # Find all yaml files in capabilities dir
    all_yamls = sorted(cap_dir.rglob("*.yaml"))
    candidate_files = listed_paths if listed_paths else [p for p in all_yamls if p.name != "catalog.yaml"]

    for path in candidate_files:
        if not path.is_file():
            continue
        try:
            data = load_yaml(path)
            if isinstance(data, dict) and "id" in data and "purpose" in data:
                data["origin"] = "user_home"
                data["source_path"] = str(path.resolve())
                records.append(data)
        except Exception:
            continue
    return records


def get_experience_dir(status: str, home_path=None) -> Path:
    """Return the directory for a given experience status ('verified', 'candidate', 'rejected')."""
    home = get_vcbs_home(home_path)
    target = home / "experiences" / status
    target.mkdir(parents=True, exist_ok=True)
    return target


def load_verified_experiences(home_path=None, root=ROOT) -> list[dict]:
    """Load verified reusable Experience records from VCBS Home and built-in repo."""
    experiences = []
    seen_ids = set()

    # 1. VCBS Home verified experiences take precedence
    home = get_vcbs_home(home_path)
    home_verified = home / "experiences" / "verified"
    if home_verified.is_dir():
        for path in sorted(home_verified.glob("*.yaml")):
            try:
                data = load_yaml(path)
                if isinstance(data, dict) and data.get("id") and data.get("status") == "verified":
                    data["origin"] = "user_home"
                    experiences.append(data)
                    seen_ids.add(data["id"])
            except Exception:
                continue

    # 2. Built-in repository verified experiences
    repo_verified = Path(root) / "experiences" / "verified"
    if repo_verified.is_dir():
        for path in sorted(repo_verified.glob("*.yaml")):
            try:
                data = load_yaml(path)
                if (isinstance(data, dict) and data.get("id")
                        and data.get("status") == "verified" and data["id"] not in seen_ids):
                    data["origin"] = "built_in"
                    experiences.append(data)
                    seen_ids.add(data["id"])
            except Exception:
                continue

    return experiences
