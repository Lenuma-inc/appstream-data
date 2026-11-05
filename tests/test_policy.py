import unittest
import xml.etree.ElementTree as ET

from generate import (apply_policy, load_policy, repository_shadows,
                      resolve_duplicate_components, ROOT)


def package_component(cid, package, name="Example"):
    root = ET.Element("component")
    ET.SubElement(root, "id").text = cid
    ET.SubElement(root, "pkgname").text = package
    ET.SubElement(root, "name").text = name
    return root


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

    def test_replaced_packages_are_excluded_but_wps_editions_remain(self):
        policy = load_policy(ROOT / "policy.json")
        self.assertIn("caffeine", policy["exclude_packages"])
        self.assertNotIn("caffeine-ng", policy["exclude_packages"])
        self.assertIn("gtkwave-gtk3-tcl", policy["exclude_packages"])
        self.assertNotIn("gtkwave", policy["exclude_packages"])
        self.assertNotIn("wps-office", policy["exclude_packages"])
        self.assertNotIn("wps-office-cn", policy["exclude_packages"])
        self.assertEqual(policy["disambiguate_duplicate_packages"],
                         {"wps-office-cn": {"id_suffix": "cn", "name_suffix": " (CN)"}})

    def test_duplicate_component_ids_can_keep_both_editions(self):
        components = [
            package_component("wps-office-wps.desktop", "wps-office", "WPS Office"),
            package_component("wps-office-wps.desktop", "wps-office-cn", "WPS Office"),
            package_component("org.example.App", "example", "Example"),
        ]

        removed = resolve_duplicate_components(
            components, disambiguate={"wps-office-cn": {"id_suffix": "cn", "name_suffix": " (CN)"}})

        self.assertEqual(removed, [])
        self.assertEqual(components[0].findtext("id"), "wps-office-wps.desktop")
        self.assertEqual(components[0].findtext("name"), "WPS Office")
        self.assertEqual(components[1].findtext("id"), "wps-office-wps.desktop.cn")
        self.assertEqual(components[1].findtext("name"), "WPS Office (CN)")

    def test_duplicate_component_ids_keep_the_preferred_package(self):
        components = [
            package_component("wps-office-wps.desktop", "wps-office"),
            package_component("wps-office-wps.desktop", "wps-office-cn"),
            package_component("org.example.App", "example"),
        ]

        removed = resolve_duplicate_components(components, ["wps-office-cn"])

        self.assertEqual([component.findtext("pkgname") for component in removed],
                         ["wps-office"])
        self.assertNotIn(components[1], removed)

    def test_duplicate_component_ids_without_preference_are_all_removed(self):
        components = [
            package_component("wps-office-wps.desktop", "wps-office"),
            package_component("wps-office-wps.desktop", "wps-office-cn"),
        ]

        removed = resolve_duplicate_components(components)

        self.assertEqual([component.findtext("pkgname") for component in removed],
                         ["wps-office", "wps-office-cn"])


if __name__ == "__main__":
    unittest.main()
