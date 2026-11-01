"""Resident memory of a process, for the Swedebeat mix analysis (ADR 0016 section 3): the worker
watches its child's RSS and kills it over the limit; the child reports its own peak.

Linux (the image) reads ``/proc/<pid>/statm`` and ``getrusage``; Windows (a developer's machine)
asks ``GetProcessMemoryInfo``. Anywhere else the answer is ``None`` (no limit can be watched; the
child's CPU limit and the decode cap still hold). Never raises.
"""

import ctypes
import os
import sys

_PAGE = 4096 if sys.platform == "win32" else os.sysconf("SC_PAGE_SIZE")


def process_rss(pid: int) -> int | None:
    """The bytes of resident memory of process ``pid`` now, or None."""
    try:
        if sys.platform.startswith("linux"):
            with open(f"/proc/{pid}/statm", encoding="ascii") as statm:
                return int(statm.read().split()[1]) * _PAGE
        if sys.platform == "win32":
            return _windows_counters(pid, peak=False)
    except (OSError, ValueError, IndexError):
        return None
    return None


def own_peak_rss() -> int | None:
    """The peak resident memory of this process so far, in bytes, or None."""
    try:
        if sys.platform == "win32":
            return _windows_counters(os.getpid(), peak=True)
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak * 1024 if sys.platform != "darwin" else peak
    except (OSError, ValueError, ImportError):
        return None


if sys.platform == "win32":
    from ctypes import wintypes

    class _Counters(ctypes.Structure):
        _fields_ = (
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        )

    def _windows_counters(pid: int, *, peak: bool) -> int | None:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        psapi.GetProcessMemoryInfo.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(_Counters),
            wintypes.DWORD,
        )
        handle = kernel.OpenProcess(0x1000 | 0x0010, False, pid)  # QUERY_LIMITED | VM_READ
        if not handle:
            handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)  # QUERY_INFORMATION | VM_READ
        if not handle:
            return None
        try:
            counters = _Counters()
            counters.cb = ctypes.sizeof(_Counters)
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return None
            return int(counters.PeakWorkingSetSize if peak else counters.WorkingSetSize)
        finally:
            kernel.CloseHandle(handle)

else:

    def _windows_counters(pid: int, *, peak: bool) -> int | None:  # pragma: no cover
        return None
