import json
import os
import uuid
from datetime import datetime, timezone


REQUIRED_FIELDS = [
    "mechanism",
    "counterparty",
    "why_not_arbitraged",
    "falsification",
    "target_market",
    "relations",
]


def new_proposal(**kwargs):
    proposal = {
        "proposal_id": str(uuid.uuid4())[:8],
        "status": "proposed",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "holdout_result": None,
        **kwargs,
    }
    return proposal


KNOWN_TYPES = ("yes_no", "implies", "event_exhaustive")


def normalize_relations(relations):
    out = []
    for rel in relations:
        if isinstance(rel, dict) and "type" not in rel:
            keys = [k for k in rel.keys() if isinstance(k, str)]
            if len(keys) == 1 and keys[0] in KNOWN_TYPES and isinstance(rel[keys[0]], dict):
                rel = {"type": keys[0], **rel[keys[0]]}
        out.append(rel)
    return out


def validate(proposal):
    missing = [f for f in REQUIRED_FIELDS if f not in proposal]
    if missing:
        raise ValueError(f"proposal missing required fields: {missing}")
    proposal["relations"] = normalize_relations(proposal.get("relations", []))
    if not isinstance(proposal["relations"], list) or not proposal["relations"]:
        raise ValueError("proposal.relations must be a non-empty list")
    for rel in proposal["relations"]:
        if "type" not in rel:
            raise ValueError(f"relation missing 'type': {rel}")
    return True


def save_proposal(proposal, directory):
    validate(proposal)
    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, f"{proposal['proposal_id']}.json")
    with open(path, "w") as fh:
        json.dump(proposal, fh, indent=2)
    return path


def load_proposals(directory):
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        if name.endswith(".json"):
            with open(os.path.join(directory, name)) as fh:
                out.append(json.load(fh))
    return out


def update_status(directory, proposal_id, status, holdout_result):
    path = os.path.join(directory, f"{proposal_id}.json")
    if not os.path.exists(path):
        return
    with open(path) as fh:
        proposal = json.load(fh)
    proposal["status"] = status
    proposal["holdout_result"] = holdout_result
    with open(path, "w") as fh:
        json.dump(proposal, fh, indent=2)