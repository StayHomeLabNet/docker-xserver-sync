from __future__ import annotations

import hashlib
import json

from .models import Project, SyncMapping
from .presets import get_preset


def project_config_sha256(project: Project, mapping: SyncMapping) -> str:
    """Hash only deployment-relevant configuration, excluding labels and timestamps."""
    preset = get_preset(project.site_type)
    payload = {
        "project_id": project.project_id,
        "site_type": project.site_type.value,
        "domain": project.domain,
        "ssh": {
            "host": project.ssh_host,
            "port": project.ssh_port,
            "username": project.ssh_username,
            "host_key_fingerprint": project.host_key_fingerprint,
            "private_key_reference": project.private_key_reference,
        },
        "mapping": {
            "mapping_id": mapping.mapping_id,
            "local_root": mapping.local_root,
            "remote_root": mapping.remote_root,
            "exclude_patterns": list(mapping.exclude_patterns),
            "protect_patterns": list(mapping.protect_patterns),
            "runtime_paths_confirmed": mapping.runtime_paths_confirmed,
            "file_mode": mapping.file_mode,
            "directory_mode": mapping.directory_mode,
        },
        "preset": {
            "exclude_patterns": list(preset.exclude_patterns),
            "protect_patterns": list(preset.protect_patterns),
        },
    }
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
