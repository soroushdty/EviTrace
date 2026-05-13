"""Temporary script to verify path computations for task 7.1."""
from pathlib import Path

# Pipeline test files
pipeline_test = Path(r"c:\Users\sorou\github_repos\my_repos\EviTrace\tests\src\pipeline\test_pipeline_validator_loc.py").resolve()
print(f"Pipeline test file: {pipeline_test}")
print(f"  parents[0] = {pipeline_test.parents[0]}")
print(f"  parents[1] = {pipeline_test.parents[1]}")
print(f"  parents[2] = {pipeline_test.parents[2]}")
print(f"  parents[3] = {pipeline_test.parents[3]}")
resolved = pipeline_test.parents[3] / "src" / "pipeline" / "validator.py"
print(f"  Resolved path: {resolved}")
print(f"  Exists: {resolved.exists()}")
print()

# Agents test files
agents_test = Path(r"c:\Users\sorou\github_repos\my_repos\EviTrace\tests\src\agents\openai\test_prompts_builders.py").resolve()
print(f"Agents test file: {agents_test}")
print(f"  parents[0] = {agents_test.parents[0]}")
print(f"  parents[1] = {agents_test.parents[1]}")
print(f"  parents[2] = {agents_test.parents[2]}")
print(f"  parents[3] = {agents_test.parents[3]}")
print(f"  parents[4] = {agents_test.parents[4]}")
resolved_agents = agents_test.parents[4] / "src" / "agents" / "__init__.py"
print(f"  Resolved path: {resolved_agents}")
print(f"  Exists: {resolved_agents.exists()}")
resolved_prompts = agents_test.parents[4] / "src" / "agents" / "openai" / "prompts.py"
print(f"  Prompts path: {resolved_prompts}")
print(f"  Exists: {resolved_prompts.exists()}")
