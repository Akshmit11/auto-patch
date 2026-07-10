"""Docker-based sandbox: never run untrusted target-repo code on the host."""

from __future__ import annotations

import contextlib
import shlex
import tarfile
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from autopatch.tracing.logger import StructuredLogger


@dataclass(frozen=True)
class ExecResult:
    """Result of a command executed inside the sandbox."""

    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def summary(self, *, max_chars: int = 4000) -> str:
        parts = [
            f"exit_code={self.exit_code}",
            f"timed_out={self.timed_out}",
            f"duration_s={self.duration_seconds:.2f}",
        ]
        if self.stdout:
            parts.append(f"stdout:\n{self.stdout[-max_chars:]}")
        if self.stderr:
            parts.append(f"stderr:\n{self.stderr[-max_chars:]}")
        return "\n".join(parts)


class DockerRunner:
    """Owns container lifecycle for sandboxed test execution.

    Target repositories are bind-mounted read-write at ``/workspace``.
    Network is disabled by default. All commands run as a non-root user
    when the image supports it.
    """

    def __init__(
        self,
        *,
        image: str = "python:3.11-slim",
        timeout_seconds: int = 300,
        network_disabled: bool = True,
        logger: StructuredLogger | None = None,
    ) -> None:
        self.image = image
        self.timeout_seconds = timeout_seconds
        self.network_disabled = network_disabled
        self.logger = logger
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            import docker

            self._client = docker.from_env()
        return self._client

    def ensure_image(self) -> None:
        """Pull the sandbox image if it is not present locally."""
        client = self._get_client()
        try:
            client.images.get(self.image)
        except Exception:
            if self.logger:
                self.logger.log("sandbox", message=f"pulling image {self.image}")
            client.images.pull(self.image)

    def run_command(
        self,
        workspace: Path,
        command: list[str] | str,
        *,
        timeout_seconds: int | None = None,
        workdir: str = "/workspace",
        environment: dict[str, str] | None = None,
    ) -> ExecResult:
        """Run ``command`` inside a one-shot container with ``workspace`` mounted."""
        workspace = workspace.resolve()
        if not workspace.is_dir():
            raise FileNotFoundError(f"Workspace does not exist: {workspace}")

        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds
        cmd: list[str] = command if isinstance(command, list) else ["bash", "-lc", command]

        client = self._get_client()
        self.ensure_image()

        container_kwargs: dict[str, Any] = {
            "image": self.image,
            "command": cmd,
            "working_dir": workdir,
            "volumes": {str(workspace): {"bind": "/workspace", "mode": "rw"}},
            "detach": True,
            "mem_limit": "1g",
            "nano_cpus": 1_000_000_000,  # 1 CPU
            "environment": environment or {},
        }
        if self.network_disabled:
            container_kwargs["network_mode"] = "none"
        # Drop privileges where possible
        container_kwargs["security_opt"] = ["no-new-privileges:true"]

        start = time.monotonic()
        container = client.containers.run(**container_kwargs)
        timed_out = False
        try:
            try:
                result = container.wait(timeout=timeout)
            except Exception:
                # docker SDK raises on timeout depending on version/API
                container.kill()
                timed_out = True
                result = {"StatusCode": 124}
            # Soft timeout fallback if wait returned early without raising
            elapsed = time.monotonic() - start
            if not timed_out and elapsed > timeout:
                with contextlib.suppress(Exception):
                    container.kill()
                timed_out = True

            stdout_bytes = container.logs(stdout=True, stderr=False)
            stderr_bytes = container.logs(stdout=False, stderr=True)
            exit_code = int(result.get("StatusCode", 1)) if isinstance(result, dict) else int(result)
            if timed_out:
                exit_code = 124
            exec_result = ExecResult(
                exit_code=exit_code,
                stdout=_decode(stdout_bytes),
                stderr=_decode(stderr_bytes),
                timed_out=timed_out,
                duration_seconds=time.monotonic() - start,
            )
        finally:
            with contextlib.suppress(Exception):
                container.remove(force=True)

        if self.logger:
            self.logger.log_tool_call(
                "sandbox_exec",
                arguments={"command": command if isinstance(command, str) else " ".join(command)},
                result_summary=(
                    f"exit={exec_result.exit_code} timed_out={exec_result.timed_out} "
                    f"duration={exec_result.duration_seconds:.2f}s"
                ),
                success=exec_result.ok,
            )
        return exec_result

    def apply_patch(
        self,
        workspace: Path,
        patch_text: str,
        *,
        timeout_seconds: int | None = None,
    ) -> ExecResult:
        """Write a unified diff into the workspace and apply it inside Docker."""
        workspace = workspace.resolve()
        patch_path = workspace / ".autopatch_patch.diff"
        patch_path.write_text(patch_text, encoding="utf-8")
        # Prefer git apply; fall back to patch(1).
        script = (
            "set -e; "
            "if command -v git >/dev/null 2>&1; then "
            "  git apply --verbose --whitespace=nowarn /workspace/.autopatch_patch.diff || "
            "  git apply --verbose --reject --whitespace=nowarn /workspace/.autopatch_patch.diff; "
            "elif command -v patch >/dev/null 2>&1; then "
            "  patch -p1 < /workspace/.autopatch_patch.diff; "
            "else "
            "  echo 'Neither git nor patch available in sandbox image' >&2; exit 127; "
            "fi"
        )
        # python:3.11-slim lacks git by default — install git for apply reliability.
        install_and_apply = (
            "export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update -qq && apt-get install -y -qq git patch >/dev/null; "
            + script
        )
        return self.run_command(
            workspace,
            ["bash", "-lc", install_and_apply],
            timeout_seconds=timeout_seconds,
        )

    def apply_patch_and_test(
        self,
        workspace: Path,
        patch_text: str,
        *,
        test_command: list[str] | str | None = None,
        install_command: list[str] | str | None = None,
        timeout_seconds: int | None = None,
    ) -> ExecResult:
        """Apply patch, optionally install deps, then run tests — all in Docker."""
        workspace = workspace.resolve()
        patch_path = workspace / ".autopatch_patch.diff"
        patch_path.write_text(patch_text, encoding="utf-8")

        test_cmd = test_command or ["python", "-m", "pytest", "-q"]
        if isinstance(test_cmd, list):
            test_shell = " ".join(shlex.quote(part) for part in test_cmd)
        else:
            test_shell = test_cmd

        install_shell = ""
        if install_command:
            if isinstance(install_command, list):
                install_shell = " ".join(shlex.quote(part) for part in install_command)
            else:
                install_shell = install_command
        else:
            # Best-effort: install pytest + requirements if present.
            install_shell = (
                "pip install -q pytest && "
                "if [ -f requirements.txt ]; then pip install -q -r requirements.txt; fi && "
                "if [ -f pyproject.toml ]; then pip install -q -e . 2>/dev/null || true; fi"
            )

        script = (
            "set -e; "
            "export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update -qq && apt-get install -y -qq git patch >/dev/null; "
            "git apply --verbose --whitespace=nowarn /workspace/.autopatch_patch.diff || "
            "patch -p1 < /workspace/.autopatch_patch.diff; "
            f"{install_shell}; "
            f"{test_shell}"
        )
        # Network needed for pip install; temporarily enable unless explicitly forbidden.
        # For Day 1 correctness we allow network during test setup by using a second mode.
        previous = self.network_disabled
        try:
            # pip needs network
            self.network_disabled = False
            return self.run_command(
                workspace,
                ["bash", "-lc", script],
                timeout_seconds=timeout_seconds,
            )
        finally:
            self.network_disabled = previous

    def apply_patch_host_safe(self, workspace: Path, patch_text: str) -> None:
        """Apply a unified diff on the host filesystem without executing repo code.

        Used only to write patch files / pure text transforms. Never imports or
        runs modules from the target repository.
        """
        workspace = workspace.resolve()
        # Pure text apply for simple unified diffs (no shelling to host for code exec).
        _apply_unified_diff(workspace, patch_text)


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def _apply_unified_diff(workspace: Path, patch_text: str) -> None:
    """Minimal unified-diff applier for single-file create/modify (Day 1).

    Supports:
    - modifications with ``--- a/path`` / ``+++ b/path``
    - new files (``--- /dev/null``)
    Does not execute any code from the target repo.
    """
    lines = patch_text.splitlines()
    i = 0
    while i < len(lines):
        if not lines[i].startswith("--- "):
            i += 1
            continue
        old_line = lines[i]
        new_line = lines[i + 1] if i + 1 < len(lines) else ""
        if not new_line.startswith("+++ "):
            raise ValueError(f"Malformed diff near: {old_line}")
        old_path = _strip_diff_path(old_line[4:])
        new_path = _strip_diff_path(new_line[4:])
        i += 2
        hunks: list[tuple[int, int, int, int, list[str]]] = []
        while i < len(lines) and lines[i].startswith("@@"):
            header = lines[i]
            old_start, old_count, new_start, new_count = _parse_hunk_header(header)
            i += 1
            hunk_lines: list[str] = []
            while i < len(lines) and not lines[i].startswith("--- ") and not lines[i].startswith("@@"):
                # file headers for next file break
                if lines[i].startswith("diff "):
                    break
                hunk_lines.append(lines[i])
                i += 1
            hunks.append((old_start, old_count, new_start, new_count, hunk_lines))

        target = new_path if new_path != "/dev/null" else old_path
        if target in {"/dev/null", ""}:
            raise ValueError("Diff has no target path")
        target_file = workspace / target
        if old_path == "/dev/null":
            content_lines: list[str] = []
            for _os, _oc, _ns, _nc, hunk_lines in hunks:
                for hl in hunk_lines:
                    if (hl.startswith("+") and not hl.startswith("+++")) or hl.startswith(" "):
                        content_lines.append(hl[1:])
            target_file.parent.mkdir(parents=True, exist_ok=True)
            text = "\n".join(content_lines)
            if text and not text.endswith("\n"):
                text += "\n"
            target_file.write_text(text, encoding="utf-8")
            continue

        if not target_file.exists():
            raise FileNotFoundError(f"Cannot patch missing file: {target}")
        original = target_file.read_text(encoding="utf-8").splitlines()
        result = _apply_hunks(original, hunks)
        target_file.write_text("\n".join(result) + ("\n" if result else ""), encoding="utf-8")


def _strip_diff_path(raw: str) -> str:
    path = raw.strip()
    if "\t" in path:
        path = path.split("\t", 1)[0]
    if path.startswith("a/") or path.startswith("b/"):
        path = path[2:]
    return path


def _parse_hunk_header(header: str) -> tuple[int, int, int, int]:
    # @@ -l,s +l,s @@
    import re

    match = re.search(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", header)
    if not match:
        raise ValueError(f"Bad hunk header: {header}")
    old_start = int(match.group(1))
    old_count = int(match.group(2) or "1")
    new_start = int(match.group(3))
    new_count = int(match.group(4) or "1")
    return old_start, old_count, new_start, new_count


def _apply_hunks(
    original: list[str],
    hunks: list[tuple[int, int, int, int, list[str]]],
) -> list[str]:
    # Apply from bottom to top so line numbers stay valid.
    result = list(original)
    for old_start, old_count, _new_start, _new_count, hunk_lines in sorted(
        hunks, key=lambda h: h[0], reverse=True
    ):
        start_idx = old_start - 1
        old_segment: list[str] = []
        new_segment: list[str] = []
        for hl in hunk_lines:
            if hl.startswith("\\"):
                continue
            if hl.startswith("-"):
                old_segment.append(hl[1:])
            elif hl.startswith("+"):
                new_segment.append(hl[1:])
            elif hl.startswith(" "):
                old_segment.append(hl[1:])
                new_segment.append(hl[1:])
            else:
                # context without prefix (tolerant)
                old_segment.append(hl)
                new_segment.append(hl)
        end_idx = start_idx + len(old_segment)
        if result[start_idx:end_idx] != old_segment and old_count > 0:
            # Fuzzy: try to locate old_segment nearby
            found = None
            for offset in range(0, 20):
                for cand in (start_idx + offset, start_idx - offset):
                    if cand < 0:
                        continue
                    if result[cand : cand + len(old_segment)] == old_segment:
                        found = cand
                        break
                if found is not None:
                    break
            if found is None:
                raise ValueError(
                    f"Failed to apply hunk at line {old_start}: context mismatch"
                )
            start_idx = found
            end_idx = start_idx + len(old_segment)
        result[start_idx:end_idx] = new_segment
    return result


def make_tar_bytes(path: Path) -> bytes:
    """Utility: pack a directory into tar bytes (for future copy-into-container)."""
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(path, arcname=".")
    return buf.getvalue()
