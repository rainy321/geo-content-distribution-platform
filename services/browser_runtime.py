"""Keep a successful launch fallback on its own remembered browser profile.

Only browser startup may fall back. Once Chrome was selected for this profile,
later failures or changed configuration require inspection rather than silently
opening another browser's empty session. Markers contain no executable path.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile


SELECTION_FILE = '.geo-browser-selection.json'
CHROME_SUFFIX = '-chrome'
_MARKER_ERROR = '浏览器会话选择记录无法确认，已暂停；请核对本机会话记录，原浏览器数据保留。'
_CONFIG_ERROR = '浏览器配置已变化，已暂停；请核对并恢复原浏览器配置，原会话保留。'


class BrowserSessionSelectionError(ValueError):
    """Only fixed, safe session-selection instructions may reach the desktop UI."""


def _executable_identity(executable):
    try:
        path = os.path.normcase(os.path.abspath(os.fspath(executable)))
        return hashlib.sha256(os.fsencode(path)).hexdigest()
    except (TypeError, ValueError, OSError):
        raise BrowserSessionSelectionError(_CONFIG_ERROR) from None


def _read_selection(marker):
    try:
        info = marker.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise BrowserSessionSelectionError(_MARKER_ERROR) from None
    try:
        if (not stat.S_ISREG(info.st_mode) or info.st_size > 1024
                or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)):
            raise ValueError()
        with marker.open('rb') as stream:
            payload = stream.read(1025)
        if len(payload) > 1024:
            raise ValueError()
        value = json.loads(payload)
        if (not isinstance(value, dict)
                or set(value) != {'schema_version', 'engine', 'profile_suffix', 'executable_sha256'}
                or type(value['schema_version']) is not int or value['schema_version'] != 1
                or value['engine'] != 'chrome' or value['profile_suffix'] != CHROME_SUFFIX
                or not isinstance(value['executable_sha256'], str)
                or not re.fullmatch('[0-9a-f]{64}', value['executable_sha256'])):
            raise ValueError()
        return value
    except (OSError, ValueError, UnicodeError):
        raise BrowserSessionSelectionError(_MARKER_ERROR) from None


def _chrome_profile(profile, *, remembered=False):
    try:
        alternate = profile.with_name(profile.name + CHROME_SUFFIX)
        if not remembered:
            alternate.mkdir(parents=True, exist_ok=True)
        info = alternate.lstat()
        if (not stat.S_ISDIR(info.st_mode) or alternate.is_symlink()
                or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0)
                or (profile.exists() and os.path.samefile(profile, alternate))):
            raise ValueError()
    except (OSError, ValueError):
        raise BrowserSessionSelectionError('上次使用的独立 Chrome 会话目录无法确认，已暂停；请核对本机浏览器数据。') from None
    return alternate


def _remember_chrome(profile, executable):
    value = {'schema_version': 1, 'engine': 'chrome', 'profile_suffix': CHROME_SUFFIX,
             'executable_sha256': _executable_identity(executable)}
    marker = profile / SELECTION_FILE
    profile.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix='.geo-browser-selection-', suffix='.tmp', dir=profile)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write((json.dumps(value, sort_keys=True) + '\n').encode('utf-8'))
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Atomically publish the complete file without replacing an unknown
            # record that may have appeared while the browser was starting.
            os.link(temporary, marker)
        except FileExistsError:
            if _read_selection(marker) != value:
                raise BrowserSessionSelectionError(_MARKER_ERROR) from None
    finally:
        temporary.unlink(missing_ok=True)


async def launch_context(playwright, profile_dir, options, fallback_executable=None):
    # Interactive desktop pages must follow the real window, including smaller
    # screens and later resizing. Keep deliberate device emulation unchanged.
    if (options.get('headless') is False and not options.get('is_mobile')
            and not options.get('has_touch') and options.get('device_scale_factor') is None):
        options = dict(options)
        options.pop('viewport', None)
        options.pop('screen', None)
        options['no_viewport'] = True
        args = list(options.get('args') or [])
        if not any(arg.split('=', 1)[0] in ('--start-maximized', '--window-size', '--start-fullscreen', '--kiosk')
                   for arg in args):
            args.append('--start-maximized')
        options['args'] = args
    profile = Path(profile_dir)
    selected = _read_selection(profile / SELECTION_FILE)
    if selected:
        if (not fallback_executable or fallback_executable == options.get('executable_path')
                or selected['executable_sha256'] != _executable_identity(fallback_executable)):
            raise BrowserSessionSelectionError(_CONFIG_ERROR)
        alternate = _chrome_profile(profile, remembered=True)
        # Do not silently return to a new Edge session if this launch fails.
        return await playwright.chromium.launch_persistent_context(str(alternate), **{**options, 'executable_path': fallback_executable})
    try:
        return await playwright.chromium.launch_persistent_context(str(profile_dir), **options)
    except Exception:
        if not fallback_executable or fallback_executable == options.get('executable_path'):
            raise
        # Never open a different browser against another engine's cookies or
        # schema. A fallback may require a fresh normal login by the user.
        alternate = _chrome_profile(profile)
        context = await playwright.chromium.launch_persistent_context(str(alternate), **{**options, 'executable_path': fallback_executable})
        try:
            _remember_chrome(profile, fallback_executable)
        except Exception:
            try:
                await context.close()
            except Exception:
                pass
            raise BrowserSessionSelectionError('浏览器会话选择未保存，已暂停操作；请核对浏览器窗口和本机写入权限，原数据保留。') from None
        return context
