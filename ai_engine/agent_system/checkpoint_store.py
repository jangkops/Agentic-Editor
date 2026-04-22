"""JSON-based checkpoint store for agent workflows."""
import os
import json
from datetime import datetime


class CheckpointStore:
    """Persist and restore agent workflow state as JSON files."""

    def __init__(self, base_dir: str = ""):
        if not base_dir:
            # Default: userData/checkpoints/
            base_dir = os.path.join(os.path.expanduser("~"), ".ai-editor", "checkpoints")
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def _path(self, workflow_id: str) -> str:
        return os.path.join(self.base_dir, f"{workflow_id}.json")

    def save(self, workflow_id: str, state: dict) -> str:
        """Save workflow state to JSON file."""
        checkpoint = {
            "workflow_id": workflow_id,
            "timestamp": datetime.utcnow().isoformat(),
            "state": state,
        }
        path = self._path(workflow_id)
        with open(path, "w") as f:
            json.dump(checkpoint, f, indent=2, default=str)
        return path

    def load(self, workflow_id: str) -> dict | None:
        """Load workflow state from JSON file."""
        path = self._path(workflow_id)
        if not os.path.isfile(path):
            return None
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("state")

    def list_checkpoints(self) -> list:
        """List all saved checkpoints."""
        results = []
        for fname in os.listdir(self.base_dir):
            if fname.endswith(".json"):
                path = os.path.join(self.base_dir, fname)
                try:
                    with open(path, "r") as f:
                        data = json.load(f)
                    results.append({
                        "workflow_id": data.get("workflow_id", fname[:-5]),
                        "timestamp": data.get("timestamp", ""),
                    })
                except Exception:
                    pass
        return sorted(results, key=lambda x: x["timestamp"], reverse=True)

    def delete(self, workflow_id: str) -> bool:
        """Delete a checkpoint."""
        path = self._path(workflow_id)
        if os.path.isfile(path):
            os.remove(path)
            return True
        return False
