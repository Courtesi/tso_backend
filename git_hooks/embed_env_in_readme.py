#!/usr/bin/env python3
"""
Auto-embeds .env.example content into README.md between markers.
Runs automatically via pre-commit hook.
"""

import re
import sys
from pathlib import Path


def embed_env_in_readme():
    """Replace content between markers with current .env.example content."""

    # Define file paths (relative to script location)
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    env_file = project_root / ".env.example"
    readme_file = project_root / "README.md"

    # Validate files exist
    if not env_file.exists():
        print(f"Error: {env_file} not found", file=sys.stderr)
        return 1

    if not readme_file.exists():
        print(f"Error: {readme_file} not found", file=sys.stderr)
        return 1

    # Read .env.example content
    env_content = env_file.read_text(encoding="utf-8")

    # Read README content
    readme_content = readme_file.read_text(encoding="utf-8")

    # Define markers
    start_marker = "<!-- ENV_EXAMPLE_START -->"
    end_marker = "<!-- ENV_EXAMPLE_END -->"

    # Check if markers exist
    if start_marker not in readme_content or end_marker not in readme_content:
        print(f"Warning: Markers not found in {readme_file}", file=sys.stderr)
        print(f"Add {start_marker} and {end_marker} to README.md", file=sys.stderr)
        return 1

    # Build replacement content
    replacement = f"{start_marker}\n```env\n{env_content.rstrip()}\n```\n{end_marker}"

    # Replace content between markers (DOTALL flag allows . to match newlines)
    pattern = rf"{re.escape(start_marker)}.*?{re.escape(end_marker)}"
    new_readme = re.sub(pattern, replacement, readme_content, flags=re.DOTALL)

    # Check if content changed
    if new_readme == readme_content:
        print("README.md already up to date")
        return 0

    # Write updated README
    readme_file.write_text(new_readme, encoding="utf-8")
    print(f"Updated {readme_file} with {env_file} content")

    return 0


if __name__ == "__main__":
    sys.exit(embed_env_in_readme())
