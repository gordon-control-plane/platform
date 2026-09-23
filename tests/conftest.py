import platform
import resource


def pytest_sessionstart(session):
    """
    Guarantee that tests can never cause a runaway memory explosion.
    Sets a hard memory limit for the pytest process.
    """
    if platform.system() != "Windows":
        try:
            # Set soft limit to 2GB (2 * 1024 * 1024 * 1024 bytes)
            mem_limit_bytes = 2 * 1024 * 1024 * 1024
            soft, hard = resource.getrlimit(resource.RLIMIT_AS)

            # Apply limit if it's currently unlimited or higher than our cap
            if soft == resource.RLIM_INFINITY or soft > mem_limit_bytes:
                resource.setrlimit(resource.RLIMIT_AS, (mem_limit_bytes, hard))
        except (ValueError, OSError) as e:
            print(f"Warning: Could not set strict memory limit: {e}")
