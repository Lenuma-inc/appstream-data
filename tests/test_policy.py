import unittest

from generate import apply_policy, load_policy, repository_shadows, ROOT


class RepositoryPolicyTests(unittest.TestCase):
    def test_priority_selects_one_repository_independent_of_config_order(self):
        config = {"chaotic-aur": {"priority": -10}, "lunaos-repo": {"priority": 20}}
        shadows = repository_shadows(config, {"chaotic-aur": {"cassette", "other"}, "lunaos-repo": {"cassette"}})
        self.assertEqual(shadows["chaotic-aur"], {"cassette": "lunaos-repo"})
        self.assertEqual(shadows["lunaos-repo"], {})

    def test_overlapping_package_is_filtered_even_when_explicitly_kept(self):
        policy = load_policy(ROOT / "policy.json")
        policy["keep_packages"] = ["cassette"]
        selected = {name: {"fields": {"NAME": [name]}} for name in ("cassette", "other")}
        result, excluded = apply_policy(selected, {"cassette", "other"}, policy, {"cassette": "lunaos-repo"})
        self.assertEqual(set(result), {"other"})
        self.assertEqual(excluded["cassette"], "preferred repository: lunaos-repo")

    def test_discover_configuration_is_hidden_without_excluding_main_app(self):
        policy = load_policy(ROOT / "policy.json")
        self.assertIn("discover_overlay_configure.desktop", policy["exclude_components"])
        self.assertNotIn("discover_overlay.desktop", policy["exclude_components"])

    def test_zenmap_root_launcher_is_hidden_without_excluding_zenmap(self):
        policy = load_policy(ROOT / "policy.json")
        self.assertIn("zenmap-root.desktop", policy["exclude_components"])
        self.assertNotIn("zenmap.desktop", policy["exclude_components"])

    def test_nwg_readme_viewer_is_hidden_without_excluding_nwg_shell(self):
        policy = load_policy(ROOT / "policy.json")
        self.assertIn("nwg-readme.desktop", policy["exclude_components"])
        self.assertNotIn("nwg-shell", policy["exclude_packages"])


if __name__ == "__main__":
    unittest.main()
