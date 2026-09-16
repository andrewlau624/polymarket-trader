import json
import os
from datetime import datetime, timezone


def bury(graveyard_path, proposal, reason):
    os.makedirs(os.path.dirname(graveyard_path) or ".", exist_ok=True)
    entry = {
        "proposal_id": proposal.get("proposal_id"),
        "mechanism": proposal.get("mechanism"),
        "reason": reason,
        "buried_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(graveyard_path, "a") as fh:
        fh.write(json.dumps(entry) + "\n")


def load(graveyard_path):
    if not os.path.exists(graveyard_path):
        return []
    out = []
    with open(graveyard_path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def summaries(graveyard_path, limit=200):
    entries = load(graveyard_path)[-limit:]
    return [
        f"{e['proposal_id']}: {e['mechanism']} -- buried because {e['reason']}"
        for e in entries
    ]