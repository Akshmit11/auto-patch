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
            exit_code = (
                int(result.get("StatusCode", 1)) if isinstance(result, dict) else int(result)
            )
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
        """Apply a unified diff (host text first, Docker git/patch fallback)."""
        workspace = workspace.resolve()
        normalize_workspace_newlines(workspace)
        patch_text = normalize_text_to_lf(patch_text)
        write_text_lf(workspace / ".autopatch_patch.diff", patch_text)

        try:
            _apply_unified_diff(workspace, patch_text)
            return ExecResult(
                exit_code=0,
                stdout="Applied patch via host pure-text applier\n",
                stderr="",
                timed_out=False,
                duration_seconds=0.0,
            )
        except Exception as host_exc:
            host_err = str(host_exc)

        # Prefer git apply; fall back to patch(1) inside Docker.
        script = (
            "set -e; "
            "export DEBIAN_FRONTEND=noninteractive; "
            "apt-get update -qq && apt-get install -y -qq git patch >/dev/null; "
            # Strip CRs from sources (Windows bind mounts / autocrlf leftovers).
            "find /workspace -type f \\( -name '*.py' -o -name '*.txt' -o -name '*.toml' "
            "-o -name '*.md' -o -name '*.cfg' -o -name '*.ini' \\) "
            "! -path '*/.git/*' -print0 2>/dev/null | "
            "xargs -0 -r sed -i 's/\\r$//' ; "
            "git -c core.autocrlf=false apply --verbose --whitespace=nowarn "
            "/workspace/.autopatch_patch.diff || "
            "patch -p1 --binary < /workspace/.autopatch_patch.diff"
        )
        result = self.run_command(
            workspace,
            ["bash", "-lc", script],
            timeout_seconds=timeout_seconds,
        )
        if not result.ok:
            # Surface both failure modes for the retry loop.
            merged_stderr = (
                f"host_apply_error: {host_err}\n{result.stderr}".strip()
            )
            return ExecResult(
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=merged_stderr,
                timed_out=result.timed_out,
                duration_seconds=result.duration_seconds,
            )
        return result

    def apply_patch_and_test(
        self,
        workspace: Path,
        patch_text: str,
        *,
        test_command: list[str] | str | None = None,
        install_command: list[str] | str | None = None,
        timeout_seconds: int | None = None,
    ) -> ExecResult:
        """Apply patch (text-safe), then install deps + run tests inside Docker.

        Patch application is pure text (host fuzzy applier, then Docker git/patch).
        Target-repo code execution (pip/pytest) always stays in Docker.
        """
        workspace = workspace.resolve()
        normalize_workspace_newlines(workspace)
        patch_text = normalize_text_to_lf(patch_text)
        write_text_lf(workspace / ".autopatch_patch.diff", patch_text)
        write_text_lf(workspace / ".autopatch_generated.diff", patch_text)

        apply_notes: list[str] = []
        try:
            _apply_unified_diff(workspace, patch_text)
            apply_notes.append("host pure-text apply: ok")
        except Exception as host_exc:
            apply_notes.append(f"host pure-text apply: failed ({host_exc})")
            # Fall back to git/patch in Docker before running tests.
            apply_result = self.apply_patch(
                workspace, patch_text, timeout_seconds=timeout_seconds
            )
            if not apply_result.ok:
                return ExecResult(
                    exit_code=apply_result.exit_code or 1,
                    stdout=apply_result.stdout,
                    stderr=(
                        "Patch failed to apply before tests.\n"
                        + "\n".join(apply_notes)
                        + "\n"
                        + apply_result.stderr
                    ),
                    timed_out=apply_result.timed_out,
                    duration_seconds=apply_result.duration_seconds,
                )
            apply_notes.append("docker git/patch apply: ok")

        test_cmd = test_command or ["python", "-m", "pytest", "-q"]
        if isinstance(test_cmd, list):
            test_shell = " ".join(shlex.quote(part) for part in test_cmd)
        else:
            test_shell = test_cmd

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

        # Tests only — patch already applied as pure text on the mounted workspace.
        script = (
            "set -e; "
            "export DEBIAN_FRONTEND=noninteractive; "
            f"{install_shell}; "
            f"{test_shell}"
        )
        previous = self.network_disabled
        try:
            # pip needs network
            self.network_disabled = False
            result = self.run_command(
                workspace,
                ["bash", "-lc", script],
                timeout_seconds=timeout_seconds,
            )
        finally:
            self.network_disabled = previous

        note = "; ".join(apply_notes)
        return ExecResult(
            exit_code=result.exit_code,
            stdout=f"{note}\n{result.stdout}",
            stderr=result.stderr,
            timed_out=result.timed_out,
            duration_seconds=result.duration_seconds,
        )

    def apply_patch_host_safe(self, workspace: Path, patch_text: str) -> None:
        """Apply a unified diff on the host filesystem without executing repo code.

        Used only to write patch files / pure text transforms. Never imports or
        runs modules from the target repository.
        """
        workspace = workspace.resolve()
        normalize_workspace_newlines(workspace)
        _apply_unified_diff(workspace, normalize_text_to_lf(patch_text))


def _decode(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


_TEXT_SUFFIXES = {
    ".py",
    ".txt",
    ".md",
    ".toml",
    ".cfg",
    ".ini",
    ".yml",
    ".yaml",
    ".json",
    ".rst",
    ".in",
    ".diff",
    ".patch",
}


def normalize_text_to_lf(text: str) -> str:
    """Normalize any newline style to Unix LF."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def write_text_lf(path: Path, text: str) -> None:
    """Write text as UTF-8 with LF only (never Windows CRLF translation)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(normalize_text_to_lf(text).encode("utf-8"))


def normalize_workspace_newlines(workspace: Path) -> None:
    """Rewrite text files under ``workspace`` to LF in place.

    Critical on Windows: ``Path.write_text`` and ``git core.autocrlf`` can leave
    CRLF on disk while LLM patches use LF, so ``git apply`` / ``patch`` fail with
    "different line endings" or context mismatches.
    """
    workspace = workspace.resolve()
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        try:
            rel_parts = path.relative_to(workspace).parts
        except ValueError:
            continue
        if any(part in {".git", "__pycache__", ".venv", "venv", "node_modules"} for part in rel_parts):
            continue
        if path.suffix.lower() not in _TEXT_SUFFIXES and path.name not in {
            "Dockerfile",
            "Makefile",
            ".gitignore",
            ".env.example",
        }:
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw[:8192]:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        normalized = normalize_text_to_lf(text)
        if normalized.encode("utf-8") != raw:
            path.write_bytes(normalized.encode("utf-8"))


def _apply_unified_diff(workspace: Path, patch_text: str) -> None:
    """Minimal unified-diff applier for create/modify (no target code execution).

    Supports:
    - modifications with ``--- a/path`` / ``+++ b/path``
    - new files (``--- /dev/null``)
    - fuzzy context match and deleted-line-only fallback (LLM hunks are often imperfect)

    Writes are atomic per patch: all files are computed first, then written, so a
    mid-patch failure never leaves a half-applied tree.
    """
    lines = normalize_text_to_lf(patch_text).splitlines()
    i = 0
    pending_writes: list[tuple[Path, str]] = []
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
            while (
                i < len(lines) and not lines[i].startswith("--- ") and not lines[i].startswith("@@")
            ):
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
            text = "\n".join(content_lines)
            if text and not text.endswith("\n"):
                text += "\n"
            pending_writes.append((target_file, text))
            continue

        if not target_file.exists():
            raise FileNotFoundError(f"Cannot patch missing file: {target}")
        original = normalize_text_to_lf(target_file.read_text(encoding="utf-8")).splitlines()
        result = _apply_hunks(original, hunks)
        pending_writes.append((target_file, "\n".join(result) + ("\n" if result else "")))

    for target_file, text in pending_writes:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        write_text_lf(target_file, text)


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
        deleted_only: list[str] = []
        added_only: list[str] = []
        for hl in hunk_lines:
            if hl.startswith("\\"):
                continue
            if hl.startswith("---") or hl.startswith("+++"):
                continue
            if hl.startswith("-"):
                body = hl[1:]
                old_segment.append(body)
                deleted_only.append(body)
            elif hl.startswith("+"):
                body = hl[1:]
                new_segment.append(body)
                added_only.append(body)
            elif hl.startswith(" "):
                old_segment.append(hl[1:])
                new_segment.append(hl[1:])
            else:
                # context without prefix (tolerant)
                old_segment.append(hl)
                new_segment.append(hl)
        end_idx = start_idx + len(old_segment)
        if result[start_idx:end_idx] != old_segment and old_count > 0:
            # Fuzzy: try to locate full old_segment nearby, then whole file.
            found = _find_segment(result, old_segment, preferred=start_idx)
            if found is None and deleted_only:
                # LLM often drops nearby context lines (e.g. a comment between
                # statements). Match pure deleted lines and swap for pure adds.
                found_del = _find_segment(result, deleted_only, preferred=start_idx)
                if found_del is not None:
                    result[found_del : found_del + len(deleted_only)] = added_only
                    continue
            if found is None:
                raise ValueError(f"Failed to apply hunk at line {old_start}: context mismatch")
            start_idx = found
            end_idx = start_idx + len(old_segment)
        result[start_idx:end_idx] = new_segment
    return result


def _find_segment(lines: list[str], segment: list[str], *, preferred: int) -> int | None:
    """Locate ``segment`` near ``preferred`` index, then anywhere in the file."""
    if not segment:
        return preferred if 0 <= preferred <= len(lines) else None
    n = len(segment)
    if n > len(lines):
        return None
    for offset in range(0, max(len(lines), 40)):
        for cand in (preferred + offset, preferred - offset):
            if cand < 0 or cand + n > len(lines):
                continue
            if lines[cand : cand + n] == segment:
                return cand
        if offset > len(lines):
            break
    # Full scan as last resort
    for cand in range(0, len(lines) - n + 1):
        if lines[cand : cand + n] == segment:
            return cand
    return None


def make_tar_bytes(path: Path) -> bytes:
    """Utility: pack a directory into tar bytes (for future copy-into-container)."""
    buf = BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        tar.add(path, arcname=".")
    return buf.getvalue()
