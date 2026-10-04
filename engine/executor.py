"""
Task Executor
Runs individual steps with the right model and tools
"""


class Executor:
    """Executes task steps with real tools."""

    def __init__(self, provider_manager, gateway, workspace_dir=".", broker=None):
        self.provider_manager = provider_manager
        self.gateway = gateway
        from engine.tools import Toolchain
        from security_v2.broker import SecurityBroker
        from security_v2.key_manager import SecureKeyManager

        self.broker = broker or SecurityBroker(gateway=gateway, key_manager=SecureKeyManager())
        self.toolchain = Toolchain(workspace_dir, approval_gateway=gateway, broker=self.broker)

    def execute_step(self, step, route, context):
        """Execute one step, iterating if tools are used."""
        if step.requires_human_approval:
            approved = self.gateway.request_approval(
                "execute_step",
                {"step": step.name, "risk": step.risk_level},
                f"Step: {step.name}\n{step.description}\nRisk: {step.risk_level}"
            )
            if not approved:
                raise PermissionError(f"Step '{step.name}' denied")

        system = f"You are an expert at {step.step_type}. Be thorough and precise."
        if step.tools_needed:
            system += (
                "\nYou may propose a bounded internal command or pure-code block, but every proposal "
                "is validated by the sandbox and may be denied. Never assume unrestricted shell, "
                "network, filesystem, interpreter, or process access. Wrap a command in a ```bash "
                "block or a pure-code proposal in a ```python block. The system will return a "
                "controlled result; then provide the final answer."
            )

        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": self._build_prompt(step, context)}
        ]

        max_iterations = 5
        for i in range(max_iterations):
            self.gateway.log(f"  [Executor] Generating response (iteration {i+1})...")
            response = self.provider_manager.generate(
                route,
                messages,
                temperature=0.4,
                max_tokens=2000
            )
            reply = response.text
            messages.append({"role": "assistant", "content": reply})

            tool_results = self.toolchain.evaluate_tools(reply)
            if not tool_results:
                return reply
            
            # Tools were executed
            feedback = "\n\n".join(tool_results)
            self.gateway.log(f"  [Executor] Tools executed. Returning output to model...")
            messages.append({"role": "user", "content": f"Tool execution results:\n{feedback}"})

        raise RuntimeError("Tool loop exhausted without a final response")

    def _build_prompt(self, step, context):
        """Build execution prompt with context."""
        
        sops = ""
        try:
            import os, json
            skills_dir = os.path.join(os.path.dirname(__file__), "skills")
            if os.path.exists(skills_dir):
                obj_lower = step.description.lower() + " " + step.name.lower()
                for f in os.listdir(skills_dir):
                    if f.endswith(".json"):
                        with open(os.path.join(skills_dir, f), 'r', encoding='utf-8') as sf:
                            skill = json.load(sf)
                            if any(kw in obj_lower for kw in skill.get("trigger_keywords", [])):
                                sops += f"\nSOP for {skill['name']}:\n{skill['sop']}\n"
        except Exception:
            pass

        prompt = f"TASK: {step.name}\nDESCRIPTION: {step.description}\n{sops}"

        if hasattr(step, 'input_keys') and step.input_keys:
            for key in step.input_keys:
                if key in context:
                    prompt += f"\nINPUT ({key}):\n{str(context[key])[:2000]}\n"

        if step.verification_criteria:
            prompt += "\nYOUR OUTPUT MUST:\n"
            for c in step.verification_criteria:
                prompt += f"  ✓ {c}\n"

        prompt += "\nProvide your complete output. Remember, if you need to execute code, use ```bash or ```python blocks."
        return prompt
