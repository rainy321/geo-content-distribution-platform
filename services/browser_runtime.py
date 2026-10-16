"""Fall back only when a browser cannot launch, before any page operation."""
from pathlib import Path


async def launch_context(playwright, profile_dir, options, fallback_executable=None):
    try:
        return await playwright.chromium.launch_persistent_context(str(profile_dir), **options)
    except Exception:
        if not fallback_executable or fallback_executable == options.get('executable_path'):
            raise
        # Never open a different browser against another engine's cookies or
        # schema. A fallback may require a fresh normal login by the user.
        alternate = Path(profile_dir).with_name(Path(profile_dir).name + '-chrome')
        alternate.mkdir(parents=True, exist_ok=True)
        return await playwright.chromium.launch_persistent_context(str(alternate), **{**options, 'executable_path': fallback_executable})
