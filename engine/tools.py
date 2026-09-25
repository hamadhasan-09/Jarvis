import os
import subprocess
import tempfile
import re

class Toolchain:
    def __init__(self, workspace_dir="."):
        self.workspace_dir = os.path.abspath(workspace_dir)

    def run_bash(self, command, timeout=30):
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=self.workspace_dir,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            out = result.stdout.strip()
            err = result.stderr.strip()
            if result.returncode == 0:
                return f"Success.\nStdout:\n{out}"
            else:
                return f"Failed with code {result.returncode}.\nStdout:\n{out}\nStderr:\n{err}"
        except subprocess.TimeoutExpired:
            return "Command timed out."
        except Exception as e:
            return f"Error executing command: {e}"

    def run_python(self, code, timeout=30):
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as f:
            f.write(code)
            temp_path = f.name
        
        try:
            result = subprocess.run(
                ["python", temp_path],
                cwd=self.workspace_dir,
                capture_output=True,
                text=True,
                timeout=timeout
            )
            out = result.stdout.strip()
            err = result.stderr.strip()
            if result.returncode == 0:
                return f"Success.\nStdout:\n{out}"
            else:
                return f"Failed with code {result.returncode}.\nStdout:\n{out}\nStderr:\n{err}"
        except subprocess.TimeoutExpired:
            return "Execution timed out."
        except Exception as e:
            return f"Error executing python: {e}"
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    def evaluate_tools(self, llm_response):
        """Parse bash/python blocks from the LLM response and execute them."""
        results = []
        
        # Parse Bash
        bash_blocks = re.findall(r'```bash\n(.*?)\n```', llm_response, re.DOTALL)
        for cmd in bash_blocks:
            res = self.run_bash(cmd)
            results.append(f"Bash Command: {cmd}\nResult: {res}")
            
        # Parse Python
        python_blocks = re.findall(r'```python\n(.*?)\n```', llm_response, re.DOTALL)
        for code in python_blocks:
            res = self.run_python(code)
            results.append(f"Python Script Executed.\nResult: {res}")
            
        return results
