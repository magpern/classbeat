"""The confinement the Swedebeat mix analysis child puts on itself before it opens the file
(ADR 0016 section 3 step 2, threat model T109).

The child runs as the worker's own user, so without this a decoder exploit could read what the
worker reads (the mounted secrets) and reach the network past the worker's in-process allowlist.
Linux only (the image); stdlib and ``ctypes`` only, and nothing of ``classbeat`` (the child loads
this file by path, so it never imports the package).

- **System calls** (``deny_syscalls``, required: the child fails when it cannot install it). A
  seccomp filter, under ``no_new_privs``, that answers ``EPERM`` to ``socket`` (so no network of
  any family, DNS included), ``io_uring_*`` (sockets without ``socket``), ``ptrace``,
  ``process_vm_readv``/``writev`` and ``pidfd_getfd`` (another process's memory or descriptors:
  the worker's), ``setsid`` and ``setpgid`` (leaving the process group the parent kills), ``bpf``,
  ``perf_event_open`` and ``userfaultfd``; any other architecture's call (32-bit or x32) kills the
  process.
- **Files** (``restrict_files``, Landlock, used when the kernel has it). Read and execute beneath
  the given read-only paths (the interpreter, its libraries, ``/proc`` and the like), everything
  beneath the read-write ones (the job's private directory), nothing else: not ``/run/secrets``,
  not ``/app``. Landlock also forbids the child to trace or read a process outside its domain (so
  the worker's ``/proc/<pid>/mem``), and from ABI 4 any TCP bind or connect, from ABI 6 any signal
  to a process outside it. A kernel without Landlock returns ``False``; a failure once it is there
  raises.
- **The parent** (``make_undumpable``): the worker makes itself non-dumpable before it starts a
  child, so a process of the same user that is not root cannot attach to it or read its memory
  through ``/proc`` even without Landlock.

Elsewhere (a developer's Windows or macOS machine) every function does nothing and says so.
"""

import ctypes
import errno
import os
import platform
import stat
import sys
from collections.abc import Sequence
from typing import Final


class SandboxError(Exception):
    """The confinement could not be put in place."""


LINUX: Final = sys.platform.startswith("linux")

_PR_SET_DUMPABLE: Final = 4
_PR_GET_DUMPABLE: Final = 3
_PR_SET_SECCOMP: Final = 22
_PR_SET_NO_NEW_PRIVS: Final = 38
_SECCOMP_MODE_FILTER: Final = 2

_BPF_LD_W_ABS: Final = 0x20
_BPF_JEQ_K: Final = 0x15
_BPF_JGE_K: Final = 0x35
_BPF_RET_K: Final = 0x06
_RET_KILL_PROCESS: Final = 0x80000000
_RET_ALLOW: Final = 0x7FFF0000
_RET_EPERM: Final = 0x00050000 | errno.EPERM
_X32_BIT: Final = 0x40000000

DENIED: Final = (
    "socket",
    "io_uring_setup",
    "io_uring_enter",
    "io_uring_register",
    "ptrace",
    "process_vm_readv",
    "process_vm_writev",
    "pidfd_getfd",
    "setsid",
    "setpgid",
    "bpf",
    "perf_event_open",
    "userfaultfd",
)
# (audit architecture, system call numbers) per machine
_ARCHES: Final[dict[str, tuple[int, dict[str, int]]]] = {
    "x86_64": (
        0xC000003E,
        {
            "socket": 41,
            "ptrace": 101,
            "setpgid": 109,
            "setsid": 112,
            "perf_event_open": 298,
            "process_vm_readv": 310,
            "process_vm_writev": 311,
            "bpf": 321,
            "userfaultfd": 323,
            "io_uring_setup": 425,
            "io_uring_enter": 426,
            "io_uring_register": 427,
            "pidfd_getfd": 438,
        },
    ),
    "aarch64": (
        0xC00000B7,
        {
            "ptrace": 117,
            "setpgid": 154,
            "setsid": 157,
            "socket": 198,
            "perf_event_open": 241,
            "process_vm_readv": 270,
            "process_vm_writev": 271,
            "bpf": 280,
            "userfaultfd": 282,
            "io_uring_setup": 425,
            "io_uring_enter": 426,
            "io_uring_register": 427,
            "pidfd_getfd": 438,
        },
    ),
}

_SYS_LANDLOCK_CREATE_RULESET: Final = 444  # the same number on every architecture
_SYS_LANDLOCK_ADD_RULE: Final = 445
_SYS_LANDLOCK_RESTRICT_SELF: Final = 446
_LANDLOCK_CREATE_RULESET_VERSION: Final = 1
_LANDLOCK_RULE_PATH_BENEATH: Final = 1
_FS_EXECUTE: Final = 1 << 0
_FS_WRITE_FILE: Final = 1 << 1
_FS_READ_FILE: Final = 1 << 2
_FS_READ_DIR: Final = 1 << 3
_FS_REFER: Final = 1 << 13
_FS_TRUNCATE: Final = 1 << 14
_FS_IOCTL_DEV: Final = 1 << 15
_FS_FILE_ONLY: Final = _FS_EXECUTE | _FS_WRITE_FILE | _FS_READ_FILE | _FS_TRUNCATE | _FS_IOCTL_DEV
_READ_ONLY: Final = _FS_EXECUTE | _FS_READ_FILE | _FS_READ_DIR
_NET_BIND_CONNECT_TCP: Final = 0b11
_SCOPE_ABSTRACT_UNIX_SIGNAL: Final = 0b11


class _SockFilter(ctypes.Structure):
    _fields_ = (
        ("code", ctypes.c_uint16),
        ("jt", ctypes.c_uint8),
        ("jf", ctypes.c_uint8),
        ("k", ctypes.c_uint32),
    )


class _SockFprog(ctypes.Structure):
    _fields_ = (("len", ctypes.c_ushort), ("filter", ctypes.POINTER(_SockFilter)))


class _RulesetAttr(ctypes.Structure):
    _fields_ = (
        ("handled_access_fs", ctypes.c_uint64),
        ("handled_access_net", ctypes.c_uint64),
        ("scoped", ctypes.c_uint64),
    )


class _PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = (("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32))


def _libc() -> ctypes.CDLL:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.prctl.argtypes = (
        ctypes.c_int,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
        ctypes.c_ulong,
    )
    libc.prctl.restype = ctypes.c_int
    libc.syscall.restype = ctypes.c_long
    return libc


def _prctl(libc: ctypes.CDLL, option: int, arg: int) -> None:
    if libc.prctl(option, arg, 0, 0, 0) != 0:
        raise SandboxError(f"prctl {option}: {os.strerror(ctypes.get_errno())}")


def filter_program(machine: str) -> list[tuple[int, int, int, int]]:
    """The classic BPF program of ``deny_syscalls`` for ``machine`` (``platform.machine()``), as
    ``(code, jt, jf, k)``; ``SandboxError`` for a machine it has no numbers for."""
    if machine not in _ARCHES:
        raise SandboxError(f"no system call numbers for {machine}")
    audit_arch, numbers = _ARCHES[machine]
    program = [
        (_BPF_LD_W_ABS, 0, 0, 4),  # seccomp_data.arch
        (_BPF_JEQ_K, 1, 0, audit_arch),
        (_BPF_RET_K, 0, 0, _RET_KILL_PROCESS),
        (_BPF_LD_W_ABS, 0, 0, 0),  # seccomp_data.nr
    ]
    if machine == "x86_64":
        program += [(_BPF_JGE_K, 0, 1, _X32_BIT), (_BPF_RET_K, 0, 0, _RET_KILL_PROCESS)]
    for name in DENIED:
        program += [(_BPF_JEQ_K, 0, 1, numbers[name]), (_BPF_RET_K, 0, 0, _RET_EPERM)]
    program.append((_BPF_RET_K, 0, 0, _RET_ALLOW))
    return program


def deny_syscalls() -> bool:
    """Install the system call filter on this thread and every thread and program it starts after;
    ``True`` once installed, ``False`` off Linux. ``SandboxError`` when it cannot be installed."""
    if not LINUX:
        return False
    program = filter_program(platform.machine())
    filters = (_SockFilter * len(program))(*(_SockFilter(*ins) for ins in program))
    fprog = _SockFprog(len(program), filters)
    libc = _libc()
    _prctl(libc, _PR_SET_NO_NEW_PRIVS, 1)
    if libc.prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER, ctypes.addressof(fprog), 0, 0) != 0:
        raise SandboxError(f"seccomp: {os.strerror(ctypes.get_errno())}")
    return True


def landlock_abi() -> int:
    """The kernel's Landlock ABI version, 0 when it has none (or a filter refuses the call)."""
    if not LINUX:
        return 0
    libc = _libc()
    version = libc.syscall(
        ctypes.c_long(_SYS_LANDLOCK_CREATE_RULESET),
        None,
        ctypes.c_size_t(0),
        ctypes.c_uint32(_LANDLOCK_CREATE_RULESET_VERSION),
    )
    return max(int(version), 0)


def _handled_fs(abi: int) -> int:
    rights = (1 << 13) - 1  # ABI 1: execute through make_sym
    if abi >= 2:
        rights |= _FS_REFER
    if abi >= 3:
        rights |= _FS_TRUNCATE
    if abi >= 5:
        rights |= _FS_IOCTL_DEV
    return rights


def restrict_files(read_only: Sequence[str], read_write: Sequence[str]) -> bool:
    """Confine this thread (and what it starts after) to ``read_only`` (read, execute) and
    ``read_write`` (everything) beneath each path; a path that does not exist is skipped. ``True``
    once enforced, ``False`` when the kernel has no Landlock (or off Linux). ``SandboxError`` for
    any failure once Landlock is there. Needs ``no_new_privs`` (``deny_syscalls`` sets it)."""
    abi = landlock_abi()
    if abi < 1:
        return False
    libc = _libc()
    handled = _handled_fs(abi)
    attr = _RulesetAttr(handled, _NET_BIND_CONNECT_TCP if abi >= 4 else 0)
    size = 8
    if abi >= 4:
        size = 16
    if abi >= 6:
        attr.scoped = _SCOPE_ABSTRACT_UNIX_SIGNAL
        size = 24
    ruleset = int(
        libc.syscall(
            ctypes.c_long(_SYS_LANDLOCK_CREATE_RULESET),
            ctypes.byref(attr),
            ctypes.c_size_t(size),
            ctypes.c_uint32(0),
        )
    )
    if ruleset < 0:
        raise SandboxError(f"landlock ruleset: {os.strerror(ctypes.get_errno())}")
    try:
        for paths, access in ((read_only, _READ_ONLY), (read_write, handled)):
            for path in paths:
                _add_rule(libc, ruleset, path, access & handled)
        _prctl(libc, _PR_SET_NO_NEW_PRIVS, 1)
        restricted = libc.syscall(
            ctypes.c_long(_SYS_LANDLOCK_RESTRICT_SELF), ctypes.c_int(ruleset), ctypes.c_uint32(0)
        )
        if restricted != 0:
            raise SandboxError(f"landlock restrict: {os.strerror(ctypes.get_errno())}")
    finally:
        os.close(ruleset)
    return True


def _add_rule(libc: ctypes.CDLL, ruleset: int, path: str, access: int) -> None:
    try:
        fd = os.open(path, os.O_PATH | os.O_CLOEXEC)  # pyright: ignore[reportAttributeAccessIssue]
    except FileNotFoundError:
        return
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            access &= _FS_FILE_ONLY  # a file takes file rights only
        rule = _PathBeneath(access, fd)
        added = libc.syscall(
            ctypes.c_long(_SYS_LANDLOCK_ADD_RULE),
            ctypes.c_int(ruleset),
            ctypes.c_int(_LANDLOCK_RULE_PATH_BENEATH),
            ctypes.byref(rule),
            ctypes.c_uint32(0),
        )
        if added != 0:
            raise SandboxError(f"landlock rule: {os.strerror(ctypes.get_errno())}")
    finally:
        os.close(fd)


def make_undumpable() -> bool:
    """Make this process non-dumpable (no core file; only root may attach to it or read its memory
    through ``/proc``). ``True`` when it is, ``False`` off Linux."""
    if not LINUX:
        return False
    libc = _libc()
    if libc.prctl(_PR_GET_DUMPABLE, 0, 0, 0, 0) != 0:
        _prctl(libc, _PR_SET_DUMPABLE, 0)
    return True
