import subprocess
import pytest


@pytest.fixture(scope="session", autouse=True)
def update_helm_deps():
    subprocess.run(
        ["helm", "dependency", "update", "charts/agent-platform"], check=True
    )
