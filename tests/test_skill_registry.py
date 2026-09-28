import unittest

from skills.registry import SkillRegistry


class SkillRegistryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = SkillRegistry()

    def test_builtin_skills_load(self):
        names = {skill.name for skill in self.registry.list_skills()}

        self.assertIn("conversation", names)
        self.assertIn("coding", names)
        self.assertIn("research", names)
        self.assertIn("system", names)
        self.assertIn("knowledge", names)

    def test_coding_selection(self):
        matches = self.registry.select(
            "debug this Python function",
            task_type="CODING",
        )

        self.assertTrue(matches)
        self.assertEqual(matches[0].name, "coding")

    def test_research_selection(self):
        matches = self.registry.select(
            "research the latest API changes",
            task_type="RESEARCH",
        )

        self.assertTrue(matches)
        self.assertEqual(matches[0].name, "research")

    def test_system_selection(self):
        matches = self.registry.select(
            "launch Notepad",
            task_type="SYSTEM_ACTION",
        )

        self.assertTrue(matches)
        self.assertEqual(matches[0].name, "system")

    def test_context_generation(self):
        context = self.registry.build_context(
            "debug this Python script",
            task_type="CODING",
        )

        self.assertIn("SKILL: coding", context)
        self.assertIn("inspect", context.lower())
        self.assertIn("verify", context.lower())


if __name__ == "__main__":
    unittest.main()