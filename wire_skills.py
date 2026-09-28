from pathlib import Path
import shutil


def patch_file(path_str: str, transform):
    path = Path(path_str)

    if not path.exists():
        raise FileNotFoundError(path)

    original = path.read_text(encoding="utf-8")

    backup = path.with_suffix(path.suffix + ".skill-backup")
    if not backup.exists():
        shutil.copy2(path, backup)

    updated = transform(original)

    if updated == original:
        print(f"[UNCHANGED] {path}")
        return

    path.write_text(updated, encoding="utf-8")
    print(f"[PATCHED]   {path}")


def patch_planner(text: str) -> str:
    # 1. Import registry
    if "from skills.registry import SkillRegistry" not in text:
        anchor = "import re\n"
        if anchor not in text:
            raise RuntimeError("Planner import anchor not found.")
        text = text.replace(
            anchor,
            anchor + "\nfrom skills.registry import SkillRegistry\n",
            1,
        )

    # 2. Initialize registry
    if "self.skill_registry = SkillRegistry()" not in text:
        anchor = "        self.task_router = task_router\n"
        if anchor not in text:
            raise RuntimeError("Planner __init__ anchor not found.")

        text = text.replace(
            anchor,
            anchor + "        self.skill_registry = SkillRegistry()\n",
            1,
        )

    # 3. Add skills to Plan
    if "self.skills = []" not in text:
        anchor = "        self.context = {}\n"
        if anchor not in text:
            raise RuntimeError("Plan context anchor not found.")

        text = text.replace(
            anchor,
            anchor + "        self.skills = []\n",
            1,
        )

    # 4. Select skills immediately after routing
    marker = "        route = self.task_router.route(task)\n"

    if "skill_context = self.skill_registry.build_context(" not in text:
        if marker not in text:
            raise RuntimeError("Planner route anchor not found.")

        addition = """        skill_context = self.skill_registry.build_context(
            objective,
            task_type=task.task_type,
            limit=3,
        )

        selected_skills = self.skill_registry.select(
            objective,
            task_type=task.task_type,
            limit=3,
        )

        selected_skill_names = [skill.name for skill in selected_skills]
"""

        text = text.replace(
            marker,
            marker + addition,
            1,
        )

    # 5. Put selected skills into the generated Plan
    marker = "            plan = Plan(objective)\n"

    if "plan.skills = selected_skill_names" not in text:
        if marker not in text:
            raise RuntimeError("Planner Plan construction anchor not found.")

        text = text.replace(
            marker,
            marker + "            plan.skills = selected_skill_names\n",
            1,
        )

    # 6. Give the LLM the procedural skill context
    marker = "OBJECTIVE: {objective}\n\n"
    if "{skill_context}" not in text:
        if marker not in text:
            raise RuntimeError("Planner prompt anchor not found.")

        text = text.replace(
            marker,
            marker
            + "RELEVANT JARVIS SKILLS:\n{skill_context}\n\n",
            1,
        )

    # 7. Explain that skills are reusable procedures
    marker = 'Be thorough. Include verification steps. Mark risky steps for approval."""'

    if "Treat the provided skills as operating procedures" not in text:
        if marker not in text:
            raise RuntimeError("Planner prompt ending anchor not found.")

        replacement = (
            'Be thorough. Include verification steps. Mark risky steps for approval.\n'
            '\n'
            'Treat the provided skills as operating procedures. '
            'Use them when relevant, but do not invent tools or permissions."""'
        )

        text = text.replace(
            marker,
            replacement,
            1,
        )

    return text


def patch_executor(text: str) -> str:
    # 1. Import registry
    if "from skills.registry import SkillRegistry" not in text:
        anchor = '"""Task Executor\n'
        if anchor not in text:
            raise RuntimeError("Executor module anchor not found.")

        # Put the import after the module docstring/import area instead.
        marker = "\n\nclass Executor:"
        if marker not in text:
            raise RuntimeError("Executor class anchor not found.")

        text = text.replace(
            marker,
            "\n\nfrom skills.registry import SkillRegistry"
            + marker,
            1,
        )

    # 2. Initialize registry
    if "self.skill_registry = SkillRegistry()" not in text:
        anchor = "        self.toolchain = Toolchain(workspace_dir)\n"
        if anchor not in text:
            raise RuntimeError("Executor initialization anchor not found.")

        text = text.replace(
            anchor,
            anchor + "        self.skill_registry = SkillRegistry()\n",
            1,
        )

    # 3. Inject selected skill procedures into executor system prompt
    if "skill_context = self.skill_registry.build_context(" not in text:
        marker = '        system = f"You are an expert at {step.step_type}. Be thorough and precise."\n'

        if marker not in text:
            raise RuntimeError("Executor system prompt anchor not found.")

        addition = """        skill_context = self.skill_registry.build_context(
            step.description,
            task_type=None,
            limit=3,
        )

        if skill_context:
            system += "\\n\\n" + skill_context

"""

        text = text.replace(
            marker,
            marker + addition,
            1,
        )

    return text


patch_file("engine/planner.py", patch_planner)
patch_file("engine/executor.py", patch_executor)

print()
print("Skill integration patch completed.")
print("Backups created as *.skill-backup")