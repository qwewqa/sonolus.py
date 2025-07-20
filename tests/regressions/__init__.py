import sys
from pathlib import Path

from sonolus.script.project import Project

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "test_projects"))

import pydori.project

pydori_project: Project = pydori.project.project
