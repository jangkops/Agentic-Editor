"""Harness controller — validates requests against anti-pattern rules."""
import re

import yaml


class HarnessViolationError(Exception):
    pass


class HarnessController:
    def __init__(self, rules_path: str = ".ai-harness/rules/anti-patterns.yml"):
        try:
            with open(rules_path) as f:
                self.rules = yaml.safe_load(f)
        except FileNotFoundError:
            self.rules = {"patterns": []}

    def validate_request(self, request: dict) -> None:
        request_str = str(request)
        for rule in self.rules.get("patterns", []):
            if re.search(rule["pattern"], request_str, re.IGNORECASE):
                raise HarnessViolationError(f"Anti-pattern: {rule['message']}")
