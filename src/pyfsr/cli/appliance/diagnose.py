"""``pyfsr appliance diagnose`` — passthrough to ``fsr_diagnose.sh``.

The canonical diagnoser lives at
``~/PycharmProjects/Miscellaneous/fortisoar/troubleshooting/fsr_diagnose.sh``
(and is also shipped to the appliance at ``/opt/cyops/scripts/fsr_diagnose.sh``
on instrumented boxes). This verb is a thin front door that runs it via the
transport and streams stdout, so you get the full triage report without having to
remember the path or the SSH incantation.
"""

from __future__ import annotations

from .transport import Transport

# Standard install path on an instrumented appliance.
_ON_BOX_PATH = "/opt/cyops/scripts/fsr_diagnose.sh"


def run(transport: Transport, *, path: str = _ON_BOX_PATH, timeout: float = 120.0) -> str:
    """Run ``fsr_diagnose.sh`` on the appliance and return its output.

    Raises ``FileNotFoundError`` if the script is not present at ``path``.
    """
    if transport.run(["test", "-f", path], sudo=False).returncode != 0:
        raise FileNotFoundError(
            f"diagnose script not found at {path!r}; deploy fsr_diagnose.sh to the appliance or pass --script <path>"
        )
    return transport.run(["bash", path], sudo=True, timeout=timeout).check().stdout
