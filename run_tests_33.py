import subprocess, sys
result = subprocess.run(
    [sys.executable, "-m", "pytest",
     "tests/steering/test_steering_drift_extraction_routing_alignment_bug_condition.py",
     "tests/steering/test_steering_drift_extraction_routing_alignment_preservation.py",
     "-v"],
    capture_output=True, text=True,
    cwd=r"c:\Users\sorou\github_repos\my_repos\EviTrace"
)
print(result.stdout)
print(result.stderr)
sys.exit(result.returncode)
