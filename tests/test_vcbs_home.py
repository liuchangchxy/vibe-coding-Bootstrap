"""Tests for user-owned VCBS Home (~/.vcbs)."""

import os
from pathlib import Path
import tempfile
import unittest
import yaml

from bootstrap.home import (
    get_vcbs_home,
    init_vcbs_home,
    load_profile,
    save_profile,
    load_user_capabilities,
    load_verified_experiences,
    get_experience_dir,
)
from composer.core import ROOT


class VCBSHomeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.home_path = Path(self.temp_dir.name) / ".vcbs"
        self.old_env = os.environ.get("VCBS_HOME")
        os.environ["VCBS_HOME"] = str(self.home_path)

    def tearDown(self):
        if self.old_env is None:
            os.environ.pop("VCBS_HOME", None)
        else:
            os.environ["VCBS_HOME"] = self.old_env
        self.temp_dir.cleanup()

    def test_get_vcbs_home_resolution(self):
        # Uses VCBS_HOME env
        self.assertEqual(self.home_path.resolve(), get_vcbs_home().resolve())
        # Custom path overrides env
        custom = Path(self.temp_dir.name) / "custom_vcbs"
        self.assertEqual(custom.resolve(), get_vcbs_home(custom).resolve())

    def test_init_vcbs_home_creates_layout_and_seeds_profile(self):
        self.assertFalse(self.home_path.exists())
        initialized = init_vcbs_home(self.home_path, seed_root=ROOT)
        self.assertEqual(self.home_path.resolve(), initialized.resolve())
        self.assertTrue((self.home_path / "profile.yaml").is_file())
        self.assertTrue((self.home_path / "capabilities").is_dir())
        self.assertTrue((self.home_path / "components").is_dir())
        self.assertTrue((self.home_path / "experiences/verified").is_dir())
        self.assertTrue((self.home_path / "experiences/candidate").is_dir())
        self.assertTrue((self.home_path / "experiences/rejected").is_dir())

        # Seed profile should be valid
        profile = load_profile(self.home_path, root=ROOT)
        self.assertEqual("personal", profile["id"])
        self.assertIn("preferences", profile)

    def test_profile_fallback_to_repo_when_home_has_no_profile(self):
        # Home directory exists but has no profile
        self.home_path.mkdir(parents=True)
        profile = load_profile(self.home_path, root=ROOT)
        self.assertEqual("personal", profile["id"])

    def test_save_and_load_profile_in_home(self):
        init_vcbs_home(self.home_path, seed_root=ROOT)
        profile = load_profile(self.home_path, root=ROOT)
        profile["preferences"]["additional"]["custom_preference"] = "always_verified"
        save_profile(profile, self.home_path, root=ROOT)

        reloaded = load_profile(self.home_path, root=ROOT)
        self.assertEqual("always_verified", reloaded["preferences"]["additional"]["custom_preference"])
        # Verify repository seed was untouched
        repo_profile = yaml.safe_load((ROOT / "profiles/personal.yaml").read_text(encoding="utf-8"))
        self.assertNotIn("custom_preference", repo_profile.get("preferences", {}).get("additional", {}))

    def test_load_user_capabilities_from_home(self):
        init_vcbs_home(self.home_path, seed_root=ROOT)
        user_cap = {
            "id": "user-custom-tool",
            "version": "1",
            "name": "User Custom Tool",
            "kind": "tool",
            "purpose": "Provide user-specific CLI tooling.",
            "applicability": {
                "use_when": ["Specific user tooling is required."],
                "not_for": ["General projects."],
            },
            "selection_hints": {"signals": ["custom-tool"], "constraints": []},
            "components": [
                {
                    "id": "custom-script",
                    "kind": "template",
                    "role": "User custom helper script",
                    "assembly": {
                        "id": "custom-script",
                        "name": "Custom script",
                        "type": "template",
                        "source": "components/custom-script.sh",
                        "provider": "user-home",
                        "version": "1",
                        "scope": "project",
                        "delivery": "copy",
                        "destination": ".vibe-coding-bootstrap/components/custom-script.sh",
                        "optional": True,
                    },
                }
            ],
        }
        cap_file = self.home_path / "capabilities" / "user-custom-tool.yaml"
        cap_file.write_text(yaml.safe_dump(user_cap), encoding="utf-8")

        caps = load_user_capabilities(self.home_path)
        self.assertEqual(1, len(caps))
        self.assertEqual("user-custom-tool", caps[0]["id"])
        self.assertEqual("user_home", caps[0]["origin"])

    def test_experience_directory_resolution_and_verified_experiences(self):
        init_vcbs_home(self.home_path, seed_root=ROOT)
        verified_dir = get_experience_dir("verified", self.home_path)
        self.assertEqual((self.home_path / "experiences/verified").resolve(), verified_dir.resolve())

        # Put a verified experience in home
        exp_record = {
            "id": "exp-2026-user-test-01",
            "format_version": 1,
            "title": "User Verified Experience",
            "summary": "Reusable knowledge from home.",
            "status": "verified",
            "reusable": True,
            "project_context": {
                "technologies": ["Python"],
                "project_type": "software project",
            },
            "applicable_capabilities": ["testing"],
            "verification": {"verified_by": "user", "verified_at": "2026-10-26T00:00:00Z"},
        }
        (verified_dir / "exp-2026-user-test-01.yaml").write_text(
            yaml.safe_dump(exp_record), encoding="utf-8"
        )

        loaded = load_verified_experiences(self.home_path, root=ROOT)
        self.assertTrue(any(e["id"] == "exp-2026-user-test-01" for e in loaded))


if __name__ == "__main__":
    unittest.main()
